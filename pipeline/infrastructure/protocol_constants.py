"""Protocol text constants — inlined into automated LLM prompts.

The protocol files in _config/ remain the source of truth (edited by
humans). This module loads them at import time so the automated prompt
builders can inline the text directly, eliminating a file-read tool call
per LLM invocation (research: "move protocol instructions to system
prompt, not per-grading file reads").

The debug subagent profiles also use these constants via the prompt
builders — both automated and debug paths share the same two-call
decomposition for consistency and maintainability.
"""
from __future__ import annotations

from pathlib import Path

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "_config"


def _load(name: str) -> str:
    """Load a protocol file from _config/ as a string."""
    return (_CONFIG_DIR / name).read_text(encoding="utf-8")


# Two-call grading protocols (ADR-0011)
GRADING_REASONING_PROTOCOL = _load("grading-reasoning-protocol.md")
GRADING_SCORING_PROTOCOL = _load("grading-scoring-protocol.md")

# Two-call JD grading protocols (same rubric, evaluates full background)
JD_GRADING_REASONING_PROTOCOL = _load("jd-grading-reasoning-protocol.md")
JD_GRADING_SCORING_PROTOCOL = _load("jd-grading-scoring-protocol.md")

# Two-call veracity protocols (ADR-0011)
VERACITY_CLASSIFICATION_PROTOCOL = _load("veracity-classification-protocol.md")
VERACITY_SYNTHESIS_PROTOCOL = _load("veracity-synthesis-protocol.md")

# Single-call debug references (kept for backward compat, not used by
# the two-call automated pipeline or debug subagents)
GRADING_PROTOCOL = _load("grading-protocol.md")
VERACITY_PROTOCOL = _load("veracity-protocol.md")

# Customization protocol (used by step6 customize/optimize)
RESUME_CUSTOMIZATION_PROTOCOL = _load("resume-customization-protocol.md")
