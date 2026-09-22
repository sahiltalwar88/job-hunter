"""Tests for pipeline.config.PipelineConfig."""
from pathlib import Path

import pytest

from pipeline.infrastructure.config import PipelineConfig, ModelConfig, ProfileConfig, load_config


def test_default_config():
    config = PipelineConfig()
    assert config.llm_provider == "devin"
    assert config.models.customizer == "glm-5.2-high"
    assert config.models.grader == "glm-5.2-high"
    assert config.max_optimization_iterations == 3
    assert config.jd_grade_threshold == 7.0
    assert config.resume_grade_threshold == 8.5
    assert config.llm_timeout_seconds == 120
    assert config.llm_retries == 3
    assert config.notifications_enabled is True
    assert config.git_push is True
    assert config.dry_run is False


def test_default_deterministic_scoring():
    config = PipelineConfig()
    assert config.deterministic_scoring is True


def test_deterministic_scoring_can_be_disabled():
    config = PipelineConfig(deterministic_scoring=False)
    assert config.deterministic_scoring is False


def test_default_location_policy_empty():
    config = PipelineConfig()
    assert config.location_policy == ""


def test_location_policy_settable():
    config = PipelineConfig(location_policy="Must be based in McLean, VA area.")
    assert "McLean" in config.location_policy


def test_default_profile_config():
    config = PipelineConfig()
    assert config.profile.base_resume_file == "base-resume.md"
    assert config.profile.linkedin_experience_file == "full-experience.md"


def test_profile_config_settable():
    config = PipelineConfig(
        profile=ProfileConfig(
            base_resume_file="MyResume.md",
            linkedin_experience_file="MyLinkedIn.md",
        )
    )
    assert config.profile.base_resume_file == "MyResume.md"
    assert config.profile.linkedin_experience_file == "MyLinkedIn.md"


def test_load_config_from_real_file():
    paths = Path(__file__).resolve().parent.parent.parent / ".devin" / "pipeline-config.json"
    if not paths.exists():
        pytest.skip("No pipeline-config.json in real workspace")
    config = load_config(paths)
    assert isinstance(config, PipelineConfig)
    assert config.models.customizer == "glm-5.2-high"
    assert config.max_optimization_iterations == 3


def test_load_config_missing_file(tmp_path):
    config = load_config(tmp_path / "nonexistent.json")
    assert config.models.customizer == "glm-5.2-high"


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


@pytest.mark.parametrize("provider", ["devin", "codex", "claude"])
def test_supported_llm_providers(provider):
    assert PipelineConfig(llm_provider=provider).llm_provider == provider


def test_validation_rejects_unknown_llm_provider():
    with pytest.raises(Exception):
        PipelineConfig(llm_provider="other")
