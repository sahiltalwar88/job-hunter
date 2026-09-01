#!/usr/bin/env python3
"""PreToolUse hook: universal exec whitelist (pipeline mode only).

Restricts ALL exec calls to a whitelist of safe commands. No agent
identification needed — one rule for all agents.

**Only active in pipeline mode** (`DEVIN_PIPELINE_MODE=1`). In debug mode,
the hook allows everything — the user is present and can approve/deny
commands themselves. The whitelist is a safety net for automated runs
where there's no human approval.

Two tiers:

**Tier 1 — pipeline commands** (matched as full-command patterns):
  - `python3 -m pipeline.helpers.count_lines ...`  (customizer self-measurement)
  - `python3 -m pytest ...`               (test runner)
  - `mv ...`                              (grader rename [TBD] → [score])
  - `echo "..." >> .grades.log`           (grader audit log append)

**Tier 2 — general commands** (matched by first word; chaining/pipes allowed
if every sub-command in the chain is also whitelisted):
  - `git`, `gh`                           (commits, PRs, status checks)
  - `ls`, `cd`, `cat`, `head`, `tail`, `wc`, `pwd`, `diff`, `grep`, `find`,
    `which`, `file`, `echo`, `cp`         (read-only / harmless)

**Always blocked (in pipeline mode):**
  - `$()` and backticks (command substitution — can embed arbitrary commands)
  - Single `>` redirect (could overwrite files; `>>` only allowed for
    `echo "..." >> .grades.log`)
  - `python3 -c` and any `python3` not running `pipeline.helpers.count_lines` or `pytest`
  - Any command not on the whitelist
  - Chaining where any sub-command is not whitelisted (e.g. `git foo && rm bar`)

See docs/adr/0002-universal-exec-whitelist-hook.md for the rationale.

Reads PreToolUse stdin payload, exits 2 to block, exits 0 to allow.
"""
import json
import os
import re
import sys

# Command substitution — always blocked, cannot be whitelisted.
SUBSTITUTION_RE = re.compile(r"\$\(|`")

# Single `>` redirect (not `>>`). Always blocked except in echo >> .grades.log.
SINGLE_REDIRECT_RE = re.compile(r"(?<!>)>(?!>)")

# Chaining operators — we split on these and check each sub-command.
CHAIN_RE = re.compile(r"&&|;|\|")

# Tier 1 patterns (full command match).
COUNT_LINES_RE = re.compile(r"^python3\s+-m\s+pipeline\.helpers\.count_lines(\s+.*)?$")
PYTEST_RE = re.compile(r"^python3\s+-m\s+pytest(\s+.*)?$")
MV_RE = re.compile(r"^mv\s+.+$")
ECHO_GRADES_RE = re.compile(r'^echo\s+"[^"]*"\s*>>\s*\.grades\.log\s*$')

# Tier 2 — commands allowed by first word.
TIER2_COMMANDS = {
    "git", "gh", "ls", "cd", "cat", "head", "tail", "wc", "pwd",
    "diff", "grep", "find", "which", "file", "echo", "cp",
}


def _block(reason: str):
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(2)


def _is_tier1(command: str) -> bool:
    """Check if a single command (no chaining) matches a tier-1 pattern."""
    return bool(COUNT_LINES_RE.match(command)
                or PYTEST_RE.match(command)
                or MV_RE.match(command)
                or ECHO_GRADES_RE.match(command))


def _is_tier2(command: str) -> bool:
    """Check if a single command starts with a tier-2 whitelisted command."""
    cmd = command.strip()
    if not cmd:
        return True  # empty sub-command (e.g. trailing ;) — harmless
    parts = cmd.split()
    if not parts:
        return True
    first = parts[0]
    if first not in TIER2_COMMANDS:
        return False
    # echo: block single > redirect; >> only allowed to .grades.log
    if first == "echo":
        if SINGLE_REDIRECT_RE.search(cmd):
            return False
        if ">>" in cmd:
            return bool(ECHO_GRADES_RE.match(cmd))
    return True


def _is_allowed(command: str) -> bool:
    """Check if a single command (no chaining) is allowed (tier 1 or 2)."""
    return _is_tier1(command) or _is_tier2(command)


def main():
    # Only enforce in pipeline mode. In debug mode, the user is present
    # and can approve/deny commands themselves.
    if os.environ.get("DEVIN_PIPELINE_MODE") != "1":
        sys.exit(0)

    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # can't parse — allow

    tool_name = data.get("tool_name", "")
    if tool_name != "exec":
        sys.exit(0)  # not our jurisdiction

    command = data.get("tool_input", {}).get("command", "").strip()
    if not command:
        sys.exit(0)  # empty — allow

    # Always block command substitution — can embed arbitrary commands.
    if SUBSTITUTION_RE.search(command):
        _block(
            "Command substitution ($() or backticks) is blocked — "
            "cannot embed arbitrary commands."
        )

    # Block single `>` redirect (could overwrite files).
    # `>>` is allowed only in the echo >> .grades.log pattern (checked per
    # sub-command in _is_tier2). A standalone single `>` anywhere is blocked.
    if SINGLE_REDIRECT_RE.search(command) and not ECHO_GRADES_RE.match(command):
        _block(
            "Single > redirect is blocked (could overwrite files). "
            "Only >> is allowed, and only in `echo \"...\" >> .grades.log`."
        )

    # Split on chaining operators and verify every sub-command is whitelisted.
    # This allows `git diff | head` (both whitelisted) but blocks
    # `git foo && rm bar` (rm not whitelisted).
    sub_commands = CHAIN_RE.split(command)
    for sub in sub_commands:
        sub = sub.strip()
        if not sub:
            continue
        if not _is_allowed(sub):
            _block(
                f"Command not on the exec whitelist: '{sub}'. "
                "Allowed: git, gh, ls, cd, cat, head, tail, wc, pwd, diff, "
                "grep, find, which, file, echo, cp, mv, "
                "python3 -m pipeline.helpers.count_lines. "
                "Chaining (&&, ;, |) is allowed only if every sub-command "
                "is whitelisted. See docs/adr/0002-universal-exec-whitelist-hook.md."
            )

    sys.exit(0)


if __name__ == "__main__":
    main()
