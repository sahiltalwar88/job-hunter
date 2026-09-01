"""Shared fixtures for job-hunter tests.

This conftest sits at the repo root so pytest discovers it for all tests,
regardless of which subdirectory they live in.
"""
import json
import sys
from pathlib import Path

import pytest

# Make the repo root importable so `pipeline` is a top-level package.
HUNTER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HUNTER_DIR))

FIXTURES_DIR = HUNTER_DIR / "tests" / "fixtures"


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture
def sample_all_jobs():
    """Synthetic all_jobs.json with 10 jobs (mix of feasible/infeasible/unchecked)."""
    path = FIXTURES_DIR / "sample_all_jobs_with_feasibility_tags.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def sample_all_jobs_path(tmp_path, sample_all_jobs):
    """Write sample all_jobs.json to a temp path and return it."""
    path = tmp_path / "all_jobs.json"
    path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))
    return str(path)
