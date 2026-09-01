"""Tests for pipeline.paths.Paths."""
from pathlib import Path

from pipeline.infrastructure.paths import Paths


def test_from_hunter_dir_real_workspace():
    """Paths constructed from the real workspace should resolve."""
    hunter_dir = Path(__file__).resolve().parents[3]
    paths = Paths.from_hunter_dir(hunter_dir)
    assert paths.hunter_dir.exists()
    assert paths.base_resume.parent.exists()  # _config/profile/base-resume/
    assert paths.jobs_db.name == "jobs.db"
    assert paths.checkpoints_db == paths.jobs_db  # same DB (ADR-0005)


def test_from_hunter_dir(tmp_paths):
    assert tmp_paths.hunter_dir.exists()
    assert tmp_paths.listings == tmp_paths.hunter_dir / "stages" / "1_listings"
    assert tmp_paths.drafts == tmp_paths.hunter_dir / "stages" / "2_drafts"
    assert tmp_paths.ready == tmp_paths.hunter_dir / "stages" / "4_ready"
    assert tmp_paths.jobs_db == tmp_paths.data_dir / "jobs.db"


def test_paths_is_frozen(tmp_paths):
    import pytest

    with pytest.raises(Exception):
        tmp_paths.hunter_dir = Path("/other")


def test_checkpoints_db_equals_jobs_db(tmp_paths):
    """Checkpointer lives in the same DB as jobs data (user decision)."""
    assert tmp_paths.checkpoints_db == tmp_paths.jobs_db
