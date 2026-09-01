"""Tests for pipeline.state_store — SQLite state transition audit log (E2).

Tests the state_transitions table: append-only transition records written
after every move_dir() call in the pipeline. The filesystem remains the
primary state mechanism; this table is an audit convenience (ADR-0012).
"""
import json
import sqlite3
from pathlib import Path

import pytest

from pipeline.infrastructure.state_store import JobStatus, StateStore


@pytest.fixture
def store(tmp_path):
    """Fresh StateStore pointing at a tmp DB."""
    s = StateStore(str(tmp_path / "jobs.db"))
    yield s
    s.close()


# ─── Schema ──────────────────────────────────────────────────────────────────


class TestSchema:
    def test_table_created_on_init(self, tmp_path):
        db = tmp_path / "jobs.db"
        s = StateStore(str(db))
        s.close()
        conn = sqlite3.connect(str(db))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='state_transitions'"
        ).fetchall()
        assert len(tables) == 1
        cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(state_transitions)")}
        assert "id" in cols
        assert "job_slug" in cols
        assert "from_status" in cols
        assert "to_status" in cols
        assert "timestamp" in cols
        assert "reason" in cols
        assert "grade" in cols
        assert "metadata" in cols
        conn.close()

    def test_idempotent_init(self, tmp_path):
        """Constructing StateStore twice on the same DB doesn't error."""
        db = str(tmp_path / "jobs.db")
        s1 = StateStore(db)
        s1.record_transition("co", None, JobStatus.LISTINGS, "discovered")
        s1.close()
        s2 = StateStore(db)
        s2.close()  # no error


# ─── record_transition ──────────────────────────────────────────────────────


class TestRecordTransition:
    def test_basic_insert(self, store):
        store.record_transition(
            "google-engineer", None, JobStatus.LISTINGS, "ingested from scraper"
        )
        history = store.query_history("google-engineer")
        assert len(history) == 1
        row = history[0]
        assert row["job_slug"] == "google-engineer"
        assert row["from_status"] is None
        assert row["to_status"] == JobStatus.LISTINGS.value
        assert row["reason"] == "ingested from scraper"
        assert row["timestamp"] is not None
        assert row["grade"] is None
        assert row["metadata"] is None

    def test_insert_with_grade_and_metadata(self, store):
        store.record_transition(
            "google-engineer",
            JobStatus.LISTINGS,
            JobStatus.DRAFTS,
            "JD grade 8.5 >= threshold",
            grade=8.5,
            metadata={"jd_grade": 8.5, "threshold": 8},
        )
        history = store.query_history("google-engineer")
        row = history[0]
        assert row["grade"] == 8.5
        assert json.loads(row["metadata"]) == {"jd_grade": 8.5, "threshold": 8}

    def test_multiple_transitions_in_order(self, store):
        """A job's lifecycle: discovered → listings → drafts → ready."""
        store.record_transition("co", None, JobStatus.DISCOVERED, "found")
        store.record_transition("co", JobStatus.DISCOVERED, JobStatus.LISTINGS, "ingested")
        store.record_transition("co", JobStatus.LISTINGS, JobStatus.DRAFTS, "triaged")
        store.record_transition("co", JobStatus.DRAFTS, JobStatus.READY, "passed")
        history = store.query_history("co")
        assert len(history) == 4
        assert history[0]["to_status"] == JobStatus.DISCOVERED.value
        assert history[3]["to_status"] == JobStatus.READY.value

    def test_timestamps_are_iso_format(self, store):
        store.record_transition("co", None, JobStatus.LISTINGS, "test")
        row = store.query_history("co")[0]
        # ISO 8601 with timezone — should parse without error
        from datetime import datetime
        datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))


# ─── query_history ───────────────────────────────────────────────────────────


class TestQueryHistory:
    def test_empty_history(self, store):
        assert store.query_history("nonexistent") == []

    def test_only_returns_requested_slug(self, store):
        store.record_transition("co-a", None, JobStatus.LISTINGS, "a")
        store.record_transition("co-b", None, JobStatus.LISTINGS, "b")
        assert len(store.query_history("co-a")) == 1
        assert len(store.query_history("co-b")) == 1

    def test_returns_dicts(self, store):
        store.record_transition("co", None, JobStatus.LISTINGS, "test")
        row = store.query_history("co")[0]
        assert isinstance(row, dict)


# ─── Append-only (no update/delete) ──────────────────────────────────────────


class TestAppendOnly:
    def test_no_update_method(self):
        """StateStore must not expose any update or delete method."""
        assert not hasattr(StateStore, "update_transition")
        assert not hasattr(StateStore, "delete_transition")
        assert not hasattr(StateStore, "update")
        assert not hasattr(StateStore, "delete")

    def test_repeated_inserts_accumulate(self, store):
        """Recording the same transition twice creates two rows, not one."""
        store.record_transition("co", JobStatus.LISTINGS, JobStatus.DRAFTS, "r1")
        store.record_transition("co", JobStatus.LISTINGS, JobStatus.DRAFTS, "r2")
        assert len(store.query_history("co")) == 2


# ─── Failure isolation ───────────────────────────────────────────────────────


class TestFailureIsolation:
    def test_record_transition_swallows_db_error(self, tmp_path):
        """If the DB is corrupted, record_transition logs but doesn't raise."""
        db = str(tmp_path / "jobs.db")
        s = StateStore(db)
        # Corrupt the DB by closing the connection and writing garbage
        s.close()
        Path(db).write_text("not a database")
        s2 = StateStore(db)  # will try to create tables on garbage — may fail
        # record_transition should not raise even if the DB is broken
        s2.record_transition("co", None, JobStatus.LISTINGS, "test")
        s2.close()

    def test_filesystem_works_without_db(self, tmp_path):
        """The pipeline's move_dir still works if the DB path is gone."""
        from pipeline.infrastructure.file_ops import move_dir

        src = tmp_path / "listings" / "co"
        src.mkdir(parents=True)
        (src / "jd.md").write_text("JD")
        dest = tmp_path / "drafts"

        # DB doesn't exist yet — StateStore would create it, but simulate
        # a deleted DB by pointing at a path whose parent we remove
        db_path = tmp_path / "deleted_dir" / "jobs.db"
        s = StateStore(str(db_path))
        # Now delete the dir to simulate corruption mid-run
        s.close()
        import shutil
        shutil.rmtree(tmp_path / "deleted_dir")

        # move_dir works regardless
        result = move_dir(src, dest)
        assert result.exists()
