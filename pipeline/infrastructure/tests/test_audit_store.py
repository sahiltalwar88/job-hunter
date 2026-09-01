"""Tests for pipeline.audit_store — LLM call audit logging (W7).

Tests the llm_calls table: append-only records of every LLM invocation
with full prompt, response, model params, and timing. .grades.log remains
the human-readable summary; this table is the detailed audit trail.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from pipeline.infrastructure.audit_store import AuditStore


@pytest.fixture
def audit(tmp_path):
    """Fresh AuditStore pointing at a tmp DB."""
    a = AuditStore(str(tmp_path / "jobs.db"))
    yield a
    a.close()


# ─── Schema ──────────────────────────────────────────────────────────────────


class TestSchema:
    def test_table_created_on_init(self, tmp_path):
        db = tmp_path / "jobs.db"
        a = AuditStore(str(db))
        a.close()
        conn = sqlite3.connect(str(db))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_calls'"
        ).fetchall()
        assert len(tables) == 1
        cols = {r[1] for r in conn.execute("PRAGMA table_info(llm_calls)")}
        assert "id" in cols
        assert "timestamp" in cols
        assert "job_slug" in cols
        assert "step" in cols
        assert "model" in cols
        assert "prompt" in cols
        assert "response" in cols
        assert "params" in cols
        assert "duration_ms" in cols
        conn.close()

    def test_idempotent_init(self, tmp_path):
        """Constructing AuditStore twice on the same DB doesn't error."""
        db = str(tmp_path / "jobs.db")
        a1 = AuditStore(db)
        a1.log_llm_call(step="test", model="x", prompt="p", response="r")
        a1.close()
        a2 = AuditStore(db)
        a2.close()


# ─── log_llm_call ────────────────────────────────────────────────────────────


class TestLogLlmCall:
    def test_basic_insert(self, audit):
        audit.log_llm_call(
            job_slug="google-engineer",
            step="grade_jd",
            model="customizer-model",
            prompt="Grade this JD...",
            response="GRADE: 8.5",
            params={"timeout": 120, "retries": 2},
            duration_ms=4500,
        )
        calls = audit.query_llm_calls("google-engineer")
        assert len(calls) == 1
        call = calls[0]
        assert call["job_slug"] == "google-engineer"
        assert call["step"] == "grade_jd"
        assert call["model"] == "customizer-model"
        assert call["prompt"] == "Grade this JD..."
        assert call["response"] == "GRADE: 8.5"
        assert json.loads(call["params"]) == {"timeout": 120, "retries": 2}
        assert call["duration_ms"] == 4500
        assert call["timestamp"] is not None

    def test_insert_with_none_response(self, audit):
        """Error calls log response=None."""
        audit.log_llm_call(
            job_slug="co", step="grade_resume", model="grader-model",
            prompt="Grade this", response=None, duration_ms=100,
        )
        call = audit.query_llm_calls("co")[0]
        assert call["response"] is None

    def test_insert_with_none_job_slug(self, audit):
        """Calls before a slug is assigned (e.g. feasibility) log job_slug=None."""
        audit.log_llm_call(
            job_slug=None, step="feasibility", model="customizer-model",
            prompt="Check feasibility", response="YES", duration_ms=200,
        )
        # query by slug won't find it (slug is None), but the row exists
        conn = sqlite3.connect(audit.db_path)
        count = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        conn.close()
        assert count == 1

    def test_multiple_calls_in_order(self, audit):
        audit.log_llm_call(job_slug="co", step="grade_jd", model="a", prompt="p1", response="r1")
        audit.log_llm_call(job_slug="co", step="customize", model="b", prompt="p2", response="r2")
        audit.log_llm_call(job_slug="co", step="grade_resume", model="c", prompt="p3", response="r3")
        calls = audit.query_llm_calls("co")
        assert len(calls) == 3
        assert calls[0]["step"] == "grade_jd"
        assert calls[2]["step"] == "grade_resume"


# ─── query_llm_calls ─────────────────────────────────────────────────────────


class TestQueryLlmCalls:
    def test_empty(self, audit):
        assert audit.query_llm_calls("nonexistent") == []

    def test_only_returns_requested_slug(self, audit):
        audit.log_llm_call(job_slug="co-a", step="s", model="m", prompt="p", response="r")
        audit.log_llm_call(job_slug="co-b", step="s", model="m", prompt="p", response="r")
        assert len(audit.query_llm_calls("co-a")) == 1
        assert len(audit.query_llm_calls("co-b")) == 1

    def test_returns_dicts(self, audit):
        audit.log_llm_call(job_slug="co", step="s", model="m", prompt="p", response="r")
        call = audit.query_llm_calls("co")[0]
        assert isinstance(call, dict)


# ─── Append-only ─────────────────────────────────────────────────────────────


class TestAppendOnly:
    def test_no_update_delete_methods(self):
        assert not hasattr(AuditStore, "update")
        assert not hasattr(AuditStore, "delete")
        assert not hasattr(AuditStore, "update_call")
        assert not hasattr(AuditStore, "delete_call")


# ─── Failure isolation ───────────────────────────────────────────────────────


class TestFailureIsolation:
    def test_log_llm_call_swallows_db_error(self, tmp_path):
        """If the DB is corrupted, log_llm_call logs but doesn't raise."""
        db = str(tmp_path / "jobs.db")
        a = AuditStore(db)
        a.close()
        Path(db).write_text("not a database")
        a2 = AuditStore(db)  # construction fails silently
        a2.log_llm_call(step="test", model="x", prompt="p", response="r")  # no raise
        a2.close()

    def test_log_llm_call_no_db_path(self):
        """AuditStore with a bad path doesn't raise on log."""
        a = AuditStore("/nonexistent/path/that/doesnt/exist/jobs.db")
        a.log_llm_call(step="test", model="x", prompt="p", response="r")
        a.close()
