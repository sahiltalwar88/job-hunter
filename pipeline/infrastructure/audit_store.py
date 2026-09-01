"""LLM call audit logging (W7).

A new `llm_calls` table in data/jobs.db records every LLM invocation with
full prompt, response, model parameters, and timing. This is the detailed
audit trail; .grades.log remains the human-readable summary.

The table is append-only. If the SQLite DB is corrupted or deleted, the
pipeline still works — log_llm_call() swallows errors and logs a warning.
Like state_transitions (E2), this is a convenience, not a critical path.

Usage:
    from pipeline.infrastructure.audit_store import AuditStore
    audit = AuditStore("data/jobs.db")
    audit.log_llm_call(
        job_slug="google-engineer",
        step="grade_jd",
        model="customizer-model",
        prompt="Grade this JD...",
        response="GRADE: 8.5\n...",
        params={"timeout": 120, "retries": 2},
        duration_ms=4500,
    )
    history = audit.query_llm_calls("google-engineer")
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone


logger = logging.getLogger(__name__)


_LLM_CALLS_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    job_slug TEXT,
    step TEXT,
    model TEXT,
    prompt TEXT,
    response TEXT,
    params TEXT,
    duration_ms INTEGER
);
"""


class AuditStore:
    """Append-only audit log for LLM calls (W7).

    If the DB is corrupted/deleted, log_llm_call() logs a warning but does
    not raise (failure isolation — same pattern as StateStore).
    """

    def __init__(self, db_path: str = "data/jobs.db"):
        self.db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        try:
            parent = os.path.dirname(self.db_path) or "."
            os.makedirs(parent, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_LLM_CALLS_SCHEMA)
            self._conn.commit()
        except (sqlite3.Error, OSError) as e:
            logger.warning(
                "llm_calls DB init failed for %s (continuing — audit log is "
                "non-critical): %s",
                self.db_path,
                e,
            )
            self._conn = None

    def close(self):
        if self._conn is not None:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def log_llm_call(
        self,
        job_slug: str | None = None,
        step: str | None = None,
        model: str = "",
        prompt: str = "",
        response: str | None = None,
        params: dict | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Append an LLM call record. Never raises on DB errors.

        Args:
            job_slug: The company-role slug (None if not yet known — e.g.
                      feasibility checking before a slug is assigned).
            step: Pipeline step name (e.g. "grade_jd", "customize",
                  "grade_resume", "truthfulness"). None until Stream 1a
                  wires step identification through.
            model: Model identifier.
            prompt: Full prompt text sent to the LLM.
            response: Full response text (None on error).
            params: Dict of model parameters (timeout, retries, etc.).
            duration_ms: Call duration in milliseconds.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        params_json = json.dumps(params) if params else None
        if self._conn is None:
            logger.warning(
                "llm_calls write skipped for step=%s (DB unavailable)", step
            )
            return
        try:
            self._conn.execute(
                """INSERT INTO llm_calls
                   (timestamp, job_slug, step, model, prompt, response,
                    params, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (now, job_slug, step, model, prompt, response,
                 params_json, duration_ms),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            logger.warning(
                "llm_calls write failed for step=%s (continuing — audit log is "
                "non-critical): %s",
                step,
                e,
            )

    def query_llm_calls(self, job_slug: str) -> list[dict]:
        """List LLM call history for a job slug, oldest first.

        Returns a list of dicts. Returns [] if no calls or DB error.
        """
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute(
                """SELECT id, timestamp, job_slug, step, model, prompt,
                          response, params, duration_ms
                   FROM llm_calls
                   WHERE job_slug = ?
                   ORDER BY id ASC""",
                (job_slug,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except sqlite3.Error as e:
            logger.warning("llm_calls query failed for %s: %s", job_slug, e)
            return []

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "job_slug": row["job_slug"],
            "step": row["step"],
            "model": row["model"],
            "prompt": row["prompt"],
            "response": row["response"],
            "params": row["params"],
            "duration_ms": row["duration_ms"],
        }
