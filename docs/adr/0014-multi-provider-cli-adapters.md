# Multi-provider CLI adapters

**Status:** accepted

The automated pipeline supports Devin, Codex, and Claude Code as first-class
LLM CLI providers. `config.json` selects one with `llm_provider`, while the
existing `models` object continues to select task-specific models. A
`--llm-provider` option provides a one-run override.

## Decision

`RealLLM` remains the production effect boundary and lazily dispatches to one
of three adapters:

- `devin_cli.py` invokes `devin -p` and preserves existing behavior.
- `codex_cli.py` invokes `codex exec`, supplies prompts on stdin, captures the
  final message with `--output-last-message`, and disables interactive
  approvals. `normal` calls use a read-only sandbox; customizer calls use the
  workspace-write sandbox.
- `claude_cli.py` invokes `claude -p` with no session persistence. `normal`
  calls expose only read tools. Customizer calls use `dontAsk` with explicit
  allow rules for reads, draft edits, and the resume line-count command.

All adapters implement the existing retry, timeout, error, working-directory,
model, and export parameters. A model value of `default` lets Codex or Claude
use the model configured in that CLI.

## Consequences

- Existing Devin configurations continue to work because Devin is the default.
- Provider selection is validated when configuration loads.
- Grading and truthfulness calls remain read-only for every provider.
- Codex's workspace-write sandbox is broader than Devin's path-level
  customizer policy, though it remains confined to the repository. Repository
  instructions continue to declare source profile and pipeline files read-only.
- Devin lifecycle hooks and interactive subagent profiles remain Devin-only;
  provider parity applies to automated pipeline LLM calls.
