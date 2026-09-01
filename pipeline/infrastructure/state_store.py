"""SQLite state transition audit log (E2, ADR-0012).

The pipeline's primary state mechanism is the filesystem — directories move
between listings/, drafts/, ready/, rejected/, trash/. This module adds an
append-only `state_transitions` table to data/jobs.db that records every
state change as an auditable event. The filesystem remains the source of
truth; this table is a query/audit convenience.

If the SQLite DB is corrupted or deleted, the pipeline still works —
record_transition() swallows errors and logs a warning. The table is not
on the critical path.

Usage:
    from pipeline.infrastructure.state_store import StateStore, JobStatus
    store = StateStore("data/jobs.db")
    store.record_transition("google-engineer", JobStatus.LISTINGS,
                            JobStatus.DRAFTS, "JD grade 8.5 >= threshold",
                            grade=8.5)
    history = store.query_history("google-engineer")
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from enum import Enum


logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    """Closed vocabulary for state transitions (ADR-0012).

    Maps to the pipeline's directory structure + the discovered pre-state.
    TriageDestination (in state.py) covers the post-triage outcomes;
    JobStatus adds the pre-triage states for a complete lifecycle.
    """

    DISCOVERED = "discovered"
    LISTINGS = "listings"
    DRAFTS = "drafts"
    READY = "ready"
    REJECTED_JOB_FIT = "rejected_job_fit"
    REJECTED_RESUME = "rejected_resume"
    TRASH = "trash"


_STATE_TRANSITIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS state_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_slug TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    reason TEXT,
    grade REAL,
    metadata TEXT
);
"""


class StateStore:
    """Append-only audit log for job state transitions.

    The table is never updated or deleted from — only appended to.
    If the DB is corrupted/deleted, record_transition() logs a warning
    but does not raise (failure isolation — ADR-0012).
    """

    def __init__(self, db_path: str = "data/jobs.db"):
        self.db_path = str(db_path)
        # If the DB file is corrupted or the path is invalid, construction
        # fails silently — the audit log is non-critical (ADR-0012). _conn
        # stays None and all ops become no-ops.
        self._conn: sqlite3.Connection | None = None
        try:
            parent = os.path.dirname(self.db_path) or "."
            os.makedirs(parent, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_STATE_TRANSITIONS_SCHEMA)
            self._conn.commit()
        except (sqlite3.Error, OSError) as e:
            logger.warning(
                "state_transitions DB init failed for %s (continuing — audit log "
                "is non-critical): %s",
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

    def record_transition(
        self,
        job_slug: str,
        from_status: JobStatus | None,
        to_status: JobStatus,
        reason: str = "",
        grade: float | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Append a transition record. Never raises on DB errors.

        Args:
            job_slug: The company-role slug.
            from_status: Previous status (None for the first transition).
            to_status: New status.
            reason: Human-readable reason for the transition.
            grade: Optional grade associated with the transition (e.g. JD grade).
            metadata: Optional dict, stored as JSON.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        from_val = from_status.value if from_status else None
        to_val = to_status.value if isinstance(to_status, JobStatus) else to_status
        meta_json = json.dumps(metadata) if metadata else None
        if self._conn is None:
            logger.warning(
                "state_transitions write skipped for %s (DB unavailable)", job_slug
            )
            return
        try:
            self._conn.execute(
                """INSERT INTO state_transitions
                   (job_slug, from_status, to_status, timestamp, reason, grade, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (job_slug, from_val, to_val, now, reason, grade, meta_json),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            logger.warning(
                "state_transitions write failed for %s (continuing — audit log is "
                "non-critical): %s",
                job_slug,
                e,
            )

    def query_history(self, job_slug: str) -> list[dict]:
        """List transition history for a job slug, oldest first.

        Returns a list of dicts. Returns [] if no transitions or DB error.
        """
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute(
                """SELECT id, job_slug, from_status, to_status, timestamp,
                          reason, grade, metadata
                   FROM state_transitions
                   WHERE job_slug = ?
                   ORDER BY id ASC""",
                (job_slug,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except sqlite3.Error as e:
            logger.warning(
                "state_transitions query failed for %s: %s", job_slug, e
            )
            return []

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "job_slug": row["job_slug"],
            "from_status": row["from_status"],
            "to_status": row["to_status"],
            "timestamp": row["timestamp"],
            "reason": row["reason"],
            "grade": row["grade"],
            "metadata": row["metadata"],
        }
