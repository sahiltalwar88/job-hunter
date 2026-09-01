"""Shared fixtures for pipeline tests."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from pipeline.infrastructure.config import PipelineConfig
from pipeline.infrastructure.llm_interface import FakeLLM
from pipeline.infrastructure.paths import Paths


@pytest.fixture
def tmp_paths(tmp_path: Path) -> Paths:
    """Paths pointing at a tmp workspace."""
    return Paths.from_hunter_dir(tmp_path)


@pytest.fixture
def config() -> PipelineConfig:
    """Default pipeline config for tests."""
    return PipelineConfig()


@pytest.fixture
def dry_run_config() -> PipelineConfig:
    """Pipeline config with dry_run=True (no filesystem writes)."""
    return PipelineConfig(dry_run=True)


@pytest.fixture
def fake_llm() -> FakeLLM:
    """FakeLLM with no preset responses — add via add_response() in tests."""
    return FakeLLM()


@pytest.fixture
def logger() -> logging.Logger:
    """Quiet logger for tests."""
    log = logging.getLogger("test")
    log.handlers.clear()
    log.setLevel(logging.WARNING)
    return log


@pytest.fixture
def node_config(tmp_paths, config, fake_llm, logger):
    """LangGraph RunnableConfig with FakeLLM + tmp paths."""
    return {
        "configurable": {
            "llm": fake_llm,
            "logger": logger,
            "config": config,
            "paths": tmp_paths,
        }
    }


@pytest.fixture
def dry_run_node_config(tmp_paths, dry_run_config, fake_llm, logger):
    """LangGraph RunnableConfig with dry_run=True."""
    return {
        "configurable": {
            "llm": fake_llm,
            "logger": logger,
            "config": dry_run_config,
            "paths": tmp_paths,
        }
    }
