#!/usr/bin/env python3
"""Usage-aware self-capping for Claude Code sessions.

Subcommands:
  check                          - query /usage live, log a reading, print JSON
  on --meter M (--pct N | --until-reset [--buffer N])  - activate a cap
  off                            - deactivate the cap
  status                         - live snapshot + velocity + projection
  guard                          - PreToolUse hook entrypoint (allow/block)

State lives under ~/.claude/usage-tracking/:
  active-cap.json  - {enabled, mode, meter, capPct, setAt}
  history.jsonl     - one {queriedAt, meter, pct, resetRaw} line per reading
"""
import json
import os
import re
import subprocess
import sys
import datetime

STATE_DIR = os.path.expanduser("~/.claude/usage-tracking")
STATE_FILE = os.path.join(STATE_DIR, "active-cap.json")
HIST_FILE = os.path.join(STATE_DIR, "history.jsonl")

METER_PATTERN = re.compile(
    r'^(Current session|Current week \(([^)]+)\)):\s*(\d+)%\s*used.*?resets (.+)$',
    re.MULTILINE,
)


def now_iso():
    return datetime.datetime.now().astimezone().isoformat()


def ensure_state_dir():
    os.makedirs(STATE_DIR, exist_ok=True)


def query_usage():
    """Run `claude -p "/usage"` non-interactively. Returns (meters, error)."""
    try:
        proc = subprocess.run(
            ["claude", "-p", "/usage", "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except FileNotFoundError:
        return None, "claude_binary_not_found"

    if proc.returncode != 0:
        return None, f"exit_{proc.returncode}"

    try:
        data = json.loads(proc.stdout)
        text = data.get("result", "")
    except Exception:
        return None, "bad_json"

    if not text:
        return None, "empty_result"

    meters = []
    for label, submeter, pct, reset_desc in METER_PATTERN.findall(text):
        if label == "Current session":
            key = "session"
        else:
            key = "week-" + re.sub(r"\s+", "-", submeter.strip().lower())
        meters.append({"meter": key, "pct": int(pct), "resetRaw": reset_desc.strip()})

    if not meters:
        return None, "no_meters_found"

    return meters, None


def append_history(meters, queried_at):
    ensure_state_dir()
    with open(HIST_FILE, "a") as f:
        for m in meters:
            f.write(json.dumps({"queriedAt": queried_at, **m}) + "\n")


def read_state():
    if not os.path.exists(STATE_FILE):
        return {"enabled": False}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"enabled": False}


def write_state(state):
    ensure_state_dir()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def read_history_for_meter(meter):
    if not os.path.exists(HIST_FILE):
        return []
    rows = []
    with open(HIST_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("meter") == meter:
                rows.append(row)
    return rows


def velocity_pct_per_hour(rows):
    """Simple first-vs-last slope over the logged history for one meter."""
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    try:
        t0 = datetime.datetime.fromisoformat(first["queriedAt"])
        t1 = datetime.datetime.fromisoformat(last["queriedAt"])
    except Exception:
        return None
    hours = (t1 - t0).total_seconds() / 3600.0
    if hours <= 0:
        return None
    return (last["pct"] - first["pct"]) / hours


def cmd_check():
    meters, err = query_usage()
    if err:
        print(json.dumps({"error": err}))
        return 1
    queried_at = now_iso()
    append_history(meters, queried_at)
    print(json.dumps({"queriedAt": queried_at, "meters": meters}, indent=2))
    return 0


def cmd_on(args):
    meter = None
    pct = None
    until_reset = False
    buffer_pct = 8
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--meter":
            meter = args[i + 1]
            i += 2
        elif a == "--pct":
            pct = int(args[i + 1])
            i += 2
        elif a == "--until-reset":
            until_reset = True
            i += 1
        elif a == "--buffer":
            buffer_pct = int(args[i + 1])
            i += 2
        else:
            print(f"Unknown arg: {a}", file=sys.stderr)
            return 1

    if not meter:
        print("Missing --meter (session|week-all-models|week-fable|...)", file=sys.stderr)
        return 1

    if until_reset:
        cap_pct = 100 - buffer_pct
        mode = "until-reset"
    else:
        if pct is None:
            print("Missing --pct (or pass --until-reset)", file=sys.stderr)
            return 1
        cap_pct = pct
        mode = "cap"

    state = {
        "enabled": True,
        "mode": mode,
        "meter": meter,
        "capPct": cap_pct,
        "setAt": now_iso(),
    }
    write_state(state)
    print(json.dumps(state, indent=2))
    return 0


def cmd_off():
    write_state({"enabled": False})
    print("Usage cap disabled.")
    return 0


def cmd_status():
    state = read_state()
    meters, err = query_usage()
    queried_at = now_iso()
    if meters:
        append_history(meters, queried_at)

    print("=== Usage status ===")
    if err:
        print(f"Live query failed ({err}); showing last known state only.")
    else:
        for m in meters:
            print(f"  {m['meter']}: {m['pct']}% used, resets {m['resetRaw']}")

    if not state.get("enabled"):
        print("Cap: not active. (Nothing will be blocked.)")
        return 0

    meter = state["meter"]
    cap_pct = state["capPct"]
    current = next((m["pct"] for m in (meters or []) if m["meter"] == meter), None)
    rows = read_history_for_meter(meter)
    vel = velocity_pct_per_hour(rows)

    print(f"Cap: mode={state['mode']} meter={meter} capPct={cap_pct}% (set {state['setAt']})")
    if current is not None:
        remaining = cap_pct - current
        print(f"  current={current}% remaining-to-cap={remaining}%")
        if vel and vel > 0:
            hours_left = remaining / vel
            print(f"  velocity={vel:.2f}%/hr -> projected to hit cap in ~{hours_left:.1f}h")
        elif vel is not None:
            print(f"  velocity={vel:.2f}%/hr (flat or decreasing; no projection)")
    else:
        print(f"  no live reading for meter '{meter}'")
    return 0


def _permission_decision(decision, reason=None):
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
        }
    }
    if reason:
        out["hookSpecificOutput"]["permissionDecisionReason"] = reason
    print(json.dumps(out))


def cmd_guard():
    """PreToolUse hook entrypoint. Emits hookSpecificOutput.permissionDecision JSON."""
    state = read_state()
    if not state.get("enabled"):
        return 0  # fast no-op path: no subprocess call, no stdout at all

    meter = state["meter"]
    cap_pct = state["capPct"]
    meters, err = query_usage()
    if err:
        # fail open: never block on a transient query failure
        print(f"usage-guard: live query failed ({err}); allowing.", file=sys.stderr)
        return 0

    append_history(meters, now_iso())
    current = next((m["pct"] for m in meters if m["meter"] == meter), None)
    if current is None:
        print(f"usage-guard: meter '{meter}' not found in reading; allowing.", file=sys.stderr)
        return 0

    if current >= cap_pct:
        reset_raw = next((m["resetRaw"] for m in meters if m["meter"] == meter), "unknown")
        reason = (
            f"Usage cap reached: {meter} at {current}% (cap {cap_pct}%, resets {reset_raw}). "
            f"Subagent/workflow dispatch is paused. Run `usage-cap.sh off` to lift it, "
            f"or raise the cap with `usage-cap.sh on --meter {meter} --pct <N>`."
        )
        _permission_decision("deny", reason)
        return 0

    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "check":
        return cmd_check()
    if cmd == "on":
        return cmd_on(args)
    if cmd == "off":
        return cmd_off()
    if cmd == "status":
        return cmd_status()
    if cmd == "guard":
        return cmd_guard()
    print(f"Unknown subcommand: {cmd}", file=sys.stderr)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
