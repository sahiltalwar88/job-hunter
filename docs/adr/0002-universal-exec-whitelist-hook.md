# Universal exec whitelist hook

**Status:** accepted (revised 2025-08-24 — expanded tier 2 whitelist, pipeline-mode-only)

A PreToolUse hook restricts all exec calls to a two-tier whitelist. **Only
active in pipeline mode** (`DEVIN_PIPELINE_MODE=1`) — in debug mode, the hook
allows everything because the user is present and can approve/deny commands
themselves. The whitelist is a safety net for automated runs where there's no
human approval.

**Tier 1 — pipeline commands** (strict pattern match, no chaining):
- `python3 scripts/count_lines.py` (customizer self-measurement)
- `mv` (grader file rename)
- `echo "..." >> .grades.log` (grader audit log append)

**Tier 2 — general commands** (allowed by first word; chaining/pipes allowed if every sub-command in the chain is also whitelisted):
- `git`, `gh` (commits, PRs, status checks)
- `ls`, `cd`, `cat`, `head`, `tail`, `wc`, `pwd`, `diff`, `grep`, `find`, `which`, `file`, `echo` (read-only / harmless)

**Always blocked:**
- `$()` and backticks (command substitution — can embed arbitrary commands)
- Single `>` redirect (could overwrite files; `>>` only allowed for `echo >> .grades.log`)
- `python3 -c` and any `python3` not running `scripts/count_lines.py`
- Any command not on the whitelist
- Chaining where any sub-command is not whitelisted (e.g. `git foo && rm bar`)

The rule is universal — no agent identification needed.

## Considered Options

- **Agent-specific restrictions via env vars / marker files:** Set `DEVIN_AGENT_ROLE=customizer` for automated calls, use a `.devin/customizer-active` marker file for interactive subagents. Hook checks the role/marker and applies different rules per agent. Rejected because PreToolUse hooks receive only `tool_name` and `tool_input` — no agent identity. Env vars work for `devin -p` but not for `run_subagent` (subagents inherit parent env). Marker files are fragile (stale on crash). Added complexity for no benefit once we verified no agent needs any python script other than `count_lines.py`.
- **Block only `python3 -c` (inline python):** Addresses the specific Cognichip failure (character counting via inline python) without restricting script invocation. Rejected because it leaves the door open for an agent to run any script — insufficient for a production-grade hard guarantee.
- **Universal exec whitelist — strict 3 commands only (original, pre-revision):** One rule for all agents. The whitelist was tiny (3 commands) because the architecture separates code from LLMs. Rejected in practice because it blocked `git`, `gh`, and read-only commands that agents legitimately need for commits, PRs, and status checks.
- **Universal exec whitelist — two-tier (chosen):** Tier 1 keeps the strict pipeline commands. Tier 2 adds `git`, `gh`, and read-only shell commands. Chaining is allowed but every sub-command must be whitelisted — `git diff | head` passes, `git foo && rm bar` is blocked. Command substitution (`$()`, backticks) and single `>` redirect are always blocked. The user manually removes the hook when debugging interactively.

## Consequences

- The hook only enforces in pipeline mode (`DEVIN_PIPELINE_MODE=1`). In debug mode, everything is allowed — the user is present to approve/deny. No manual hook removal needed.
- Adding a new script that an agent needs to run requires updating the hook whitelist. This is a feature, not a bug — it forces conscious consideration of what agents can do.
- Chaining (&&, ;, |) is allowed for tier 2 commands, but every sub-command is checked. This enables `git diff | head` while blocking `git foo && rm bar`.
- `python3` is only allowed for `scripts/count_lines.py` and `python3 -m pytest` — inline python (`-c`) and all other scripts remain blocked.
