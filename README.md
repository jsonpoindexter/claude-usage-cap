# claude-usage-cap

Let a [Claude Code](https://claude.com/claude-code) session cap its own subagent/Workflow
fan-out against your real `/usage` limits (session and weekly), with zero manual copy-pasting
and zero added cost. Off by default, and only active when you explicitly turn it on.

## Why

Claude Code's interactive `/usage` view has no API and isn't readable from inside a running
session: there's no CLI subcommand, local file, or endpoint that exposes your account's
authoritative weekly/session usage percentage or reset time.

It turns out `claude -p "/usage" --output-format json` (non-interactive print mode) *does*
return exactly the same data as the interactive view, and it's free: no tokens, no cost.

```
$ claude -p "/usage" --output-format json
{
  "total_cost_usd": 0,
  "usage": { "input_tokens": 0, "output_tokens": 0, ... },
  "result": "Current session: 18% used · resets Sep 27 at 3:10pm (America/Los_Angeles)\nCurrent week (all models): 5% used · resets Oct 4 at 1am (America/Los_Angeles)\n...",
  ...
}
```

This project wraps that call into a small local toolchain plus a `PreToolUse` hook, so a
session can say "only work until we hit 25% weekly usage" or "use the rest of the available
usage before reset," and have it actually enforced: hard-blocking subagent/Workflow dispatch
(not ordinary edits/bash) once the threshold is crossed, since past-incident data shows
subagent-heavy fan-out is what actually burns a usage budget, and hitting the *real* limit
kills every running subagent at once with no warning.

## What's here

- `scripts/usage_cli.py`: all the logic, with `check` / `on` / `off` / `status` / `guard`
  subcommands. State lives under `~/.claude/usage-tracking/` (`active-cap.json`,
  `history.jsonl`); nothing here talks to any server besides your own `claude` CLI.
- `scripts/usage-check.sh`, `usage-cap.sh`, `usage-guard.sh`: thin wrappers around the above.
- `install.sh`: copies the scripts into `~/.claude/scripts/`. Does **not** touch your
  `settings.json` or `CLAUDE.md`; add those snippets yourself (below), since they're your
  personal Claude Code config.

## Install

```sh
git clone https://github.com/jsonpoindexter/claude-usage-cap.git
cd claude-usage-cap
./install.sh
```

Then add this to your `~/.claude/settings.json` (merge into any existing `hooks` block,
don't replace it):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Agent|Workflow",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/scripts/usage-guard.sh",
            "timeout": 25
          }
        ]
      }
    ]
  }
}
```

This hook is a fast no-op (~40ms, no subprocess call) unless a cap is active, so it's safe to
leave installed globally.

Optionally, add this to your `~/.claude/CLAUDE.md` (or a project one) so Claude knows to turn
the cap on/off itself in response to natural language, instead of you running the commands by
hand every time:

```markdown
## Usage-aware self-capping

A global `PreToolUse` hook (`~/.claude/scripts/usage-guard.sh`) can hard-block
subagent/Workflow dispatch once a usage threshold is reached. It's off by default and only
activates when explicitly asked for in the current session; never assume it should be on.

- "only work until we hit X% weekly/session usage" / "cap usage at X%" ->
  `~/.claude/scripts/usage-cap.sh on --meter <meter> --pct <X>`
- "use the rest of the available usage before reset" ->
  `~/.claude/scripts/usage-cap.sh on --meter <meter> --until-reset [--buffer <N>]`
- "stop capping" / task's done -> `~/.claude/scripts/usage-cap.sh off`

`<meter>` is `session`, `week-all-models`, `week-fable`, or whatever meter names a live
`usage-cap.sh status` call reports.
```

## Usage

```sh
# Live snapshot: current %, remaining %, reset time, recent velocity, projection
usage-cap.sh status

# Hard cap at 25% of this week's all-models usage
usage-cap.sh on --meter week-all-models --pct 25

# "Use it all" mode: caps at (100 - buffer)% instead of a literal number,
# so you get close to the real limit without ever actually hitting it
usage-cap.sh on --meter week-fable --until-reset --buffer 8

# Turn it back off
usage-cap.sh off
```

Once a cap is on, the next `Agent`/`Workflow` tool call is blocked with a clear reason
(current %, cap, reset time) as soon as the configured meter is at or above the threshold.
Ordinary single-threaded work (edits, bash, etc.) is never affected; this only gates
subagent/Workflow fan-out.

## How it works

- `usage-check.sh` shells out to `claude -p "/usage" --output-format json`, parses the
  `Current session: N% used · resets ...` / `Current week (<meter>): N% used · resets ...`
  lines out of the `result` text, and appends one reading per meter to
  `~/.claude/usage-tracking/history.jsonl` (this is what makes a velocity/projection possible).
- `usage-cap.sh on/off/status` manages `~/.claude/usage-tracking/active-cap.json`.
- `usage-guard.sh` is the hook entrypoint: if no cap is active it exits immediately with no
  subprocess call; otherwise it queries live usage and, if at/above the cap, emits the
  `hookSpecificOutput.permissionDecision: "deny"` JSON contract that Claude Code's `PreToolUse`
  hooks use to block a tool call.

## Caveats

- This shells out to the `claude` CLI itself, so it needs `claude` on `PATH` and an
  already-authenticated session.
- The account-level `%`/reset data comes from Anthropic's own `/usage` view; this project just
  automates reading it non-interactively. It doesn't add any new data source.
- "Use it all before reset" targets a safety buffer below 100%, not exactly 100%: actually
  hitting the real limit is abrupt and kills in-flight subagents, so slightly under-using is
  the safer failure mode.
