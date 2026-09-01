"""Pipeline configuration — Pydantic model + loader.

Replaces the plain-dict config in pipeline_cli.py with a typed,
validated Pydantic model. Catches typos in config.json at
load time (e.g. "max_optimization_iterations": "3" string instead of int).
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class ModelConfig(BaseModel):
    """LLM model names for each cognitive task."""

    customizer: str = "customizer-model"
    grader: str = "grader-model"
    truthfulness: str = "grader-model"


class PipelineConfig(BaseModel):
    """Pipeline configuration, merged from defaults + config.json."""

    models: ModelConfig = ModelConfig()
    candidate_name: str = "Squall Leonhart"
    scraper_repo_path: str = ""
    scraper_transport: str = "filesystem"  # "filesystem" or "http"
    scraper_url: str = ""  # used when scraper_transport == "http"
    feasibility_prompt: str = ""
    few_shot_examples: list[str] = Field(default_factory=list)
    max_optimization_iterations: int = Field(default=3, ge=0, le=10)
    jd_grade_threshold: float = Field(default=8.0, ge=0, le=10)
    resume_grade_threshold: float = Field(default=9.0, ge=0, le=10)
    llm_timeout_seconds: int = Field(default=120, ge=10, le=600)
    llm_retries: int = Field(default=2, ge=0, le=5)
    llm_retry_delay: int = Field(default=5, ge=0, le=60)
    notifications_enabled: bool = True
    git_push: bool = True
    dry_run: bool = False  # set at runtime, not from config file


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
