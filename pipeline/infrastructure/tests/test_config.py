"""Tests for pipeline.config.PipelineConfig."""
from pathlib import Path

import pytest

from pipeline.infrastructure.config import PipelineConfig, ModelConfig, load_config


def test_default_config():
    config = PipelineConfig()
    assert config.models.customizer == "customizer-model"
    assert config.models.grader == "grader-model"
    assert config.max_optimization_iterations == 3
    assert config.jd_grade_threshold == 8.0
    assert config.resume_grade_threshold == 9.0
    assert config.llm_timeout_seconds == 120
    assert config.notifications_enabled is True
    assert config.git_push is True
    assert config.dry_run is False


def test_load_config_from_real_file():
    paths = Path(__file__).resolve().parent.parent.parent / "config.json"
    if not paths.exists():
        pytest.skip("No config.json in real workspace")
    config = load_config(paths)
    assert config.models.customizer == "customizer-model"
    assert config.max_optimization_iterations == 3


def test_load_config_missing_file(tmp_path):
    config = load_config(tmp_path / "nonexistent.json")
    assert config.models.customizer == "customizer-model"


def test_validation_rejects_bad_iteration_count():
    with pytest.raises(Exception):
        PipelineConfig(max_optimization_iterations="not a number")


def test_validation_rejects_grade_out_of_bounds():
    with pytest.raises(Exception):
        PipelineConfig(jd_grade_threshold=15.0)


def test_validation_rejects_negative_iterations():
    with pytest.raises(Exception):
        PipelineConfig(max_optimization_iterations=-1)


def test_dry_run_settable():
    config = PipelineConfig(dry_run=True)
    assert config.dry_run is True
