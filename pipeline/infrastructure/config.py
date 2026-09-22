"""Pipeline configuration — Pydantic model + loader.

Replaces the plain-dict config in pipeline_cli.py with a typed,
validated Pydantic model. Catches typos in config.json at
load time (e.g. "max_optimization_iterations": "3" string instead of int).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

# Single model constant used across the entire pipeline (production + tests).
# Swap this one constant to change the model everywhere.
DEFAULT_LLM_MODEL = "glm-5.2-high"


class ModelConfig(BaseModel):
    """LLM model names for each cognitive task."""

    customizer: str = DEFAULT_LLM_MODEL
    grader: str = DEFAULT_LLM_MODEL
    truthfulness: str = DEFAULT_LLM_MODEL


class ProfileConfig(BaseModel):
    """Profile document filenames inside _config/profile/.

    The directory names (base-resume/, full-experience/) are fixed
    conventions; only the filenames are configurable so a user's own
    files can keep their names (e.g. "My-Resume.pdf"→.md).
    """

    base_resume_file: str = "base-resume.md"
    linkedin_experience_file: str = "full-experience.md"


class PipelineConfig(BaseModel):
    """Pipeline configuration, merged from defaults + config.json."""

    models: ModelConfig = ModelConfig()
    profile: ProfileConfig = ProfileConfig()
    llm_provider: Literal["devin", "codex", "claude"] = "devin"
    candidate_name: str = "Squall Leonhart"
    scraper_repo_path: str = ""
    scraper_transport: str = "filesystem"  # "filesystem" or "http"
    scraper_url: str = ""  # used when scraper_transport == "http"
    feasibility_prompt: str = ""
    few_shot_examples: list[str] = Field(default_factory=list)
    # Free-text location/commute/relocation policy inlined into grading
    # prompts so the grader doesn't flag location as a GAP incorrectly.
    # Empty = no location note added to prompts.
    location_policy: str = ""
    max_optimization_iterations: int = Field(default=3, ge=0, le=10)
    # True: compute grades in code from Call-1 verdicts (deterministic).
    # False: legacy two-call grading — a second LLM call applies the
    # scoring formula. Flip to A/B compare the two approaches.
    deterministic_scoring: bool = True
    jd_grade_threshold: float = Field(default=7.0, ge=0, le=10)
    resume_grade_threshold: float = Field(default=8.5, ge=0, le=10)
    llm_timeout_seconds: int = Field(default=120, ge=10, le=600)
    llm_retries: int = Field(default=3, ge=0, le=5)
    llm_retry_delay: int = Field(default=5, ge=0, le=60)
    # Per-step timeout overrides (seconds). Keys are step names matching
    # the `step=` kwarg passed to deps.llm(). Falls back to llm_timeout_seconds.
    # Example: {"customize": 240, "optimize": 240}
    llm_timeout_overrides: dict[str, int] = Field(default_factory=dict)
    notifications_enabled: bool = True
    git_push: bool = True
    dry_run: bool = False  # set at runtime, not from config file

    def timeout_for(self, step: str) -> int:
        """Return the LLM timeout for a given step, with override fallback.

        Usage in step nodes:
            timeout=deps.config.timeout_for("customize")
        """
        return self.llm_timeout_overrides.get(step, self.llm_timeout_seconds)


def load_config(config_file: Path) -> PipelineConfig:
    """Load pipeline config, merging with defaults.

    Reads config.json if present, validates it via
    PipelineConfig, and returns a typed config object.
    """
    if config_file.exists():
        with open(config_file) as f:
            user_config = json.load(f)
        return PipelineConfig(**user_config)
    return PipelineConfig()
