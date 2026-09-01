# Self-measurement inside the customizer's own turn

**Status:** proposed

The customizer LLM cannot reliably count rendered lines (research confirms LLMs lose accuracy as text gets longer). We need a measurement step to enforce the line budget. Instead of a parent-orchestrated loop (generate → parent runs count_lines.py → parent re-prompts customizer to trim), the customizer runs `count_lines.py` itself via exec during its own turn, reads the output, and self-trims if over the ceiling.

## Considered Options

- **Parent-orchestrated trim loop:** Parent agent runs `count_lines.py` after the customizer finishes, then re-prompts the customizer with "you're at X lines, trim to ≤75." Rejected because it loses the customizer's context (it knows why it wrote each bullet) and adds orchestration complexity to `run_pipeline.py`.
- **Mid-generation tool calling:** Give the LLM a "line counter" tool it calls during generation. Rejected because it requires streaming infrastructure not available via `devin -p` CLI.
- **Self-measurement inside the customizer's turn (chosen):** The customizer writes the resume, runs `count_lines.py --json`, reads the output, and trims if over 75. Keeps full context, no parent loop logic, works with `devin -p --permission-mode dangerous`.

## Consequences

- The customizer needs exec access (restricted to `count_lines.py` only by the exec whitelist hook — see ADR-0002).
- The customizer's turn is longer (writing + measuring + trimming), but this is cheaper than a multi-call parent loop.
- Up to 3 trim passes before accepting — bounded by the guide instruction, not by code.
