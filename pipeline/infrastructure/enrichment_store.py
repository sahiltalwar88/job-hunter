#!/usr/bin/env python3
"""SQLite-backed job store for job-hunter.

Two tables in a single DB (data/jobs.db):

    jobs — mirror of the scraper's all_jobs.json. Wiped and re-upserted
           on sync. Contains all 13 fields from the scraper (url, company,
           title, location, date_posted, salary, ats, first_seen, description,
           feasible, feasibility, feasibility_error, duplicate_urls).

    enrichments — pipeline-produced overrides keyed by URL. Feasibility
                  verdicts and JD descriptions fetched by the pipeline
                  live here and override the scraper's values via COALESCE.
                  Preserved across syncs — never wiped.

The scraper writes feasible/feasibility/feasibility_error directly into
all_jobs.json via its own --feasibility-check. The pipeline may produce
its own verdicts (stored in enrichments), which take precedence. Queries
use COALESCE(enrichments.feasible, jobs.feasible) so pipeline wins when
present, scraper as fallback.

Usage:
    from pipeline.infrastructure.enrichment_store import EnrichmentStore
    store = EnrichmentStore("data/jobs.db")
    store.sync_jobs_db("/path/to/all_jobs.json")  # mirror scraper data
    store.set_feasibility(url, feasible=True, feasibility="preferred")
    store.set_description(url, description="...", salary="$100k-$150k")
    jobs = store.query_jobs(status="feasible", has_description=True, limit=10)
"""
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

from pipeline.infrastructure.job_schema import validate_job_entry
from pathlib import Path


_JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    url TEXT PRIMARY KEY,
    company TEXT,
    title TEXT,
    location TEXT,
    date_posted TEXT,
    salary TEXT,
    ats TEXT,
    first_seen TEXT,
    description TEXT,
    feasible INTEGER,
    feasibility TEXT,
    feasibility_error INTEGER,
    duplicate_urls TEXT,
    synced_at TEXT
);
"""

_ENRICHMENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS enrichments (
    url TEXT PRIMARY KEY,
    feasible INTEGER,
    feasibility TEXT,
    feasibility_error INTEGER,
    feasibility_rationale TEXT,
    description TEXT,
    salary TEXT,
    feasibility_checked_at TEXT,
    description_fetched_at TEXT,
    first_seen TEXT
);
"""

# Migration: add feasibility_rationale column to existing DBs that predate W9.
# SQLite's ALTER TABLE ADD COLUMN is idempotent-safe via this PRAGMA check.
_MIGRATION_ADD_RATIONALE = """
-- Migration: add feasibility_rationale column if it doesn't exist.
-- This runs after schema creation; CREATE TABLE IF NOT EXISTS won't add
-- columns to an existing table, so we handle it here.
"""


class EnrichmentStore:
    """SQLite-backed store for jobs + pipeline enrichments."""

    def __init__(self, db_path: str = "data/jobs.db"):
        self.db_path = str(db_path)
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_JOBS_SCHEMA)
        self._conn.executescript(_ENRICHMENTS_SCHEMA)
        self._migrate_add_rationale()
        self._conn.commit()

    def _migrate_add_rationale(self):
        """Add feasibility_rationale column to existing DBs (W9 migration).

        CREATE TABLE IF NOT EXISTS won't add columns to an existing table,
        so we check the schema and ALTER if the column is missing.
        """
        cols = self._conn.execute("PRAGMA table_info(enrichments)").fetchall()
        col_names = {c[1] for c in cols}
        if "feasibility_rationale" not in col_names:
            self._conn.execute(
                "ALTER TABLE enrichments ADD COLUMN feasibility_rationale TEXT"
            )

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ─── Jobs table (scraper mirror) ──────────────────────────────────────

    def _upsert_jobs(self, jobs: list[dict], *, quiet: bool = False,
                     logger: logging.Logger | None = None) -> int:
        """Validate and upsert a list of job dicts into the jobs table.

        Shared by sync_jobs_db (full sync) and sync_delta (incremental).
        Returns the number of rows upserted.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        rows = []
        for j in jobs:
            # W11: Validate each entry before inserting.
            # Bad URL scheme raises ValueError (security-critical).
            # Missing required fields are skipped with a warning (data-quality).
            schema, err = validate_job_entry(j)
            if err:
                msg = f"Skipping invalid job entry: {err}"
                if logger:
                    logger.warning(msg)
                elif not quiet:
                    print(f"  ⚠️  {msg}", file=sys.stderr)
                continue
            # schema is guaranteed non-None here (err is None means valid)
            url = schema.url
            dup_urls = j.get("duplicate_urls")
            rows.append((
                url,
                schema.company,
                schema.title,
                schema.location or "",
                schema.date_posted or "",
                schema.salary or "",
                schema.ats or "",
                schema.first_seen,
                schema.description,
                1 if schema.feasible is True else (0 if schema.feasible is False else None),
                schema.feasibility,
                1 if schema.feasibility_error else (0 if schema.feasibility_error is False else None),
                json.dumps(dup_urls) if dup_urls else None,
                now,
            ))

        self._conn.executemany(
            """INSERT INTO jobs (url, company, title, location, date_posted,
               salary, ats, first_seen, description, feasible, feasibility,
               feasibility_error, duplicate_urls, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                 company=excluded.company,
                 title=excluded.title,
                 location=excluded.location,
                 date_posted=excluded.date_posted,
                 salary=excluded.salary,
                 ats=excluded.ats,
                 first_seen=COALESCE(excluded.first_seen, jobs.first_seen),
                 description=excluded.description,
                 feasible=excluded.feasible,
                 feasibility=excluded.feasibility,
                 feasibility_error=excluded.feasibility_error,
                 duplicate_urls=excluded.duplicate_urls,
                 synced_at=excluded.synced_at""",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def sync_jobs_db(self, all_jobs_path: str, *, quiet: bool = False,
                     logger: logging.Logger | None = None) -> int:
        """Upsert all_jobs.json into the jobs table.

        Reads the scraper's all_jobs.json (read-only) and upserts every job
        into the jobs table. Existing enrichment rows are never touched.
        Uses ON CONFLICT(url) DO UPDATE so rows are updated in place.

        Each job entry is validated via JobSchema (W11):
        - Bad URL scheme (file://, javascript:, etc.) → raises ValueError
          (security-critical — must not proceed).
        - Missing required fields (url, title, company) → logged and skipped
          (data-quality — pipeline continues without the bad entry).

        Args:
            logger: If provided, skip warnings go to this logger (which writes
                    to the pipeline run log). Falls back to stderr if None.

        Returns the number of rows upserted.
        """
        with open(all_jobs_path, encoding="utf-8") as f:
            data = json.load(f)
        jobs = data.get("jobs", [])
        count = self._upsert_jobs(jobs, quiet=quiet, logger=logger)
        if not quiet:
            print(f"  Synced {count} jobs from {all_jobs_path}", file=sys.stderr)
        return count

    def sync_delta(self, delta_path: str, *, quiet: bool = False,
                   logger: logging.Logger | None = None) -> tuple[int, int]:
        """Upsert a single delta file's jobs into the jobs table.

        Reads a delta JSON file (produced by the scraper's _write_delta),
        upserts all jobs in 'added' and 'updated' arrays by URL. Both
        arrays contain full job records — both are upserts (the distinction
        is informational; 'added' = new to all_jobs.json, 'updated' =
        backfilled/enriched).

        Same validation as sync_jobs_db: bad URL scheme raises ValueError,
        missing required fields are skipped with a warning.

        Args:
            delta_path: Path to the delta JSON file.
            quiet: If True, suppress stderr warnings for skipped entries.
            logger: If provided, skip warnings go to this logger.

        Returns (added_count, updated_count) — the number of rows upserted
        from each array.
        """
        with open(delta_path, encoding="utf-8") as f:
            delta = json.load(f)

        added_jobs = delta.get("added", [])
        updated_jobs = delta.get("updated", [])

        added_count = self._upsert_jobs(added_jobs, quiet=quiet, logger=logger)
        updated_count = self._upsert_jobs(updated_jobs, quiet=quiet, logger=logger)

        if not quiet:
            print(f"  Delta {delta_path}: +{added_count} added, "
                  f"{updated_count} updated", file=sys.stderr)
        return (added_count, updated_count)

    def query_jobs(self, *, status: str = "feasible", tier: str | None = None,
                   has_description: bool | None = None,
                   limit: int = 0, offset: int = 0) -> list[dict]:
        """Query jobs with server-side filtering and pagination.

        Uses COALESCE so enrichment values override scraper values:
        COALESCE(enrichments.feasible, jobs.feasible) etc.

        Args:
            status: "feasible", "infeasible", "untagged", or "all".
            tier: None, "preferred", "yes", or "no".
            has_description: None (any), True (has JD), False (no JD).
            limit: Max rows (0 = all).
            offset: Skip this many rows.
        """
        where = []
        params: list = []

        # Feasibility status — COALESCE so enrichment overrides scraper
        if status == "feasible":
            where.append("COALESCE(e.feasible, j.feasible) = 1")
        elif status == "infeasible":
            where.append("COALESCE(e.feasible, j.feasible) = 0")
        elif status == "untagged":
            where.append("COALESCE(e.feasible, j.feasible) IS NULL")
        elif status == "all":
            pass
        else:
            raise ValueError(f"Unknown status: {status}")

        if tier is not None:
            where.append("COALESCE(e.feasibility, j.feasibility) = ?")
            params.append(tier)

        if has_description is True:
            where.append("COALESCE(e.description, j.description) IS NOT NULL "
                         "AND COALESCE(e.description, j.description) != ''")
        elif has_description is False:
            where.append("(COALESCE(e.description, j.description) IS NULL "
                         "OR COALESCE(e.description, j.description) = '')")

        # Structural f-string: `where` holds only hardcoded SQL fragments
        # selected by the `status`/`tier`/`has_description` args. User values
        # (tier, limit, offset) bind through '?' in `params`. No user input
        # enters the SQL string itself. See TestSQLInjectionSafety.
        where_clause = f"WHERE {' AND '.join(where)}" if where else ""

        sql = f"""SELECT
                j.url, j.company, j.title, j.location, j.date_posted,
                COALESCE(e.salary, j.salary) AS salary,
                j.ats, j.first_seen,
                COALESCE(e.description, j.description) AS description,
                COALESCE(e.feasible, j.feasible) AS feasible,
                COALESCE(e.feasibility, j.feasibility) AS feasibility,
                COALESCE(e.feasibility_error, j.feasibility_error) AS feasibility_error,
                e.feasibility_rationale AS feasibility_rationale,
                j.duplicate_urls
            FROM jobs j
            LEFT JOIN enrichments e ON j.url = e.url
            {where_clause}
            ORDER BY j.date_posted DESC, j.url"""

        if limit and limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        rows = self._conn.execute(sql, params).fetchall()
        return [self._job_row_to_dict(r) for r in rows]

    def count_jobs(self, *, status: str = "feasible", tier: str | None = None,
                   has_description: bool | None = None) -> int:
        """Count jobs matching the same filters as query_jobs, without fetching rows."""
        where = []
        params: list = []

        if status == "feasible":
            where.append("COALESCE(e.feasible, j.feasible) = 1")
        elif status == "infeasible":
            where.append("COALESCE(e.feasible, j.feasible) = 0")
        elif status == "untagged":
            where.append("COALESCE(e.feasible, j.feasible) IS NULL")
        elif status == "all":
            pass
        else:
            raise ValueError(f"Unknown status: {status}")

        if tier is not None:
            where.append("COALESCE(e.feasibility, j.feasibility) = ?")
            params.append(tier)

        if has_description is True:
            where.append("COALESCE(e.description, j.description) IS NOT NULL "
                         "AND COALESCE(e.description, j.description) != ''")
        elif has_description is False:
            where.append("(COALESCE(e.description, j.description) IS NULL "
                         "OR COALESCE(e.description, j.description) = '')")

        # Structural f-string: same safe pattern as query_jobs — hardcoded
        # fragments only, user values bind through '?'. See TestSQLInjectionSafety.
        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"""SELECT COUNT(*) FROM jobs j
            LEFT JOIN enrichments e ON j.url = e.url {where_clause}"""
        return self._conn.execute(sql, params).fetchone()[0]

    def purge_jobs_older_than(self, cutoff_date: str) -> int:
        """STUB — not yet implemented.

        Delete jobs from the jobs table with date_posted older than cutoff_date.
        Enrichment rows for those URLs are preserved (they may still be useful
        if the job reappears in the scraper). Returns the number of deleted rows.

        To implement: DELETE FROM jobs WHERE date_posted < ? AND url NOT IN
        (SELECT url FROM enrichments WHERE feasible = 1) — or similar, depending
        on whether you want to preserve feasible jobs regardless of age.
        """
        raise NotImplementedError(
            "purge_jobs_older_than is a stub. Not yet implemented. "
            "See docstring for intended behavior."
        )

    @staticmethod
    def _job_row_to_dict(row: sqlite3.Row) -> dict:
        """Convert a joined jobs+enrichments row to a dict matching the old shape."""
        feasible = row["feasible"]
        feasibility_error = row["feasibility_error"]
        dup_urls = row["duplicate_urls"]
        return {
            "url": row["url"],
            "company": row["company"],
            "title": row["title"],
            "location": row["location"],
            "date_posted": row["date_posted"],
            "salary": row["salary"],
            "ats": row["ats"],
            "first_seen": row["first_seen"],
            "description": row["description"],
            "feasible": bool(feasible) if feasible is not None else None,
            "feasibility": row["feasibility"],
            "feasibility_error": bool(feasibility_error) if feasibility_error is not None else None,
            "feasibility_rationale": row["feasibility_rationale"],
            "duplicate_urls": json.loads(dup_urls) if dup_urls else None,
        }

    def get(self, url: str) -> dict | None:
        """Get enrichment data for a single URL. Returns None if not found."""
        row = self._conn.execute(
            "SELECT * FROM enrichments WHERE url = ?", (url,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def get_many(self, urls: list[str]) -> dict[str, dict]:
        """Get enrichment data for multiple URLs. Returns {url: dict}."""
        if not urls:
            return {}
        # placeholders is just "?,?,?" — only '?' chars, count driven by len(urls).
        # The actual URL values bind through the `urls` param. No user input
        # enters the SQL string. See TestSQLInjectionSafety.
        placeholders = ",".join("?" * len(urls))
        rows = self._conn.execute(
            f"SELECT * FROM enrichments WHERE url IN ({placeholders})", urls
        ).fetchall()
        return {row["url"]: self._row_to_dict(row) for row in rows}

    def set_feasibility(self, url: str, feasible: bool,
                        feasibility: str, error: bool = False,
                        first_seen: str | None = None,
                        rationale: str | None = None):
        """Set or update feasibility verdict for a URL.

        Args:
            rationale: One-sentence LLM-generated rationale for the verdict (W9).
                       None preserves backward compat (no rationale stored).
            first_seen: ISO timestamp from the scraper's all_jobs.json.
                        Set once on insert; never overwritten on update.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._conn.execute(
            """INSERT INTO enrichments (url, feasible, feasibility,
               feasibility_error, feasibility_rationale,
               feasibility_checked_at, first_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                 feasible=excluded.feasible,
                 feasibility=excluded.feasibility,
                 feasibility_error=excluded.feasibility_error,
                 feasibility_rationale=excluded.feasibility_rationale,
                 feasibility_checked_at=excluded.feasibility_checked_at,
                 first_seen=COALESCE(enrichments.first_seen, excluded.first_seen)""",
            (url, 1 if feasible else 0, feasibility,
             1 if error else 0, rationale, now, first_seen),
        )
        self._conn.commit()

    def set_description(self, url: str, description: str,
                        salary: str | None = None,
                        first_seen: str | None = None):
        """Set or update JD description for a URL.

        Salary is always written — None clears any stale value. This avoids
        a re-fetch that returns no salary leaving the old salary in place.

        Args:
            first_seen: ISO timestamp from the scraper's all_jobs.json.
                        Set once on insert; never overwritten on update.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._conn.execute(
            """INSERT INTO enrichments (url, description, salary,
               description_fetched_at, first_seen)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                 description=excluded.description,
                 salary=excluded.salary,
                 description_fetched_at=excluded.description_fetched_at,
                 first_seen=COALESCE(enrichments.first_seen, excluded.first_seen)""",
            (url, description, salary, now, first_seen),
        )
        self._conn.commit()

    def get_unchecked_urls(self, all_urls: list[str]) -> list[str]:
        """Return URLs that don't have a feasibility verdict yet.

        A URL is "checked" if EITHER:
        - The enrichments table has a non-null feasible value (pipeline checked it), OR
        - The jobs table has a non-null feasible value (scraper already tagged it).

        This avoids re-checking jobs the scraper has already tagged via its
        own --feasibility-check (Q6c decision).
        """
        if not all_urls:
            return []
        # placeholders is just "?,?,?" — only '?' chars. URL values bind
        # through the params list. No user input in the SQL string.
        # See TestSQLInjectionSafety.
        placeholders = ",".join("?" * len(all_urls))
        # URLs checked by either the pipeline (enrichments) or the scraper (jobs)
        rows = self._conn.execute(
            f"""SELECT url FROM (
                    SELECT url, feasible FROM enrichments WHERE url IN ({placeholders})
                    UNION ALL
                    SELECT url, feasible FROM jobs WHERE url IN ({placeholders})
                )
                WHERE feasible IS NOT NULL""",
            all_urls + all_urls,
        ).fetchall()
        checked = {row["url"] for row in rows}
        return [u for u in all_urls if u not in checked]

    def get_undescribed_feasible_urls(self, all_urls: list[str]) -> list[str]:
        """Return feasible URLs that don't have a (non-empty) description yet."""
        if not all_urls:
            return []
        # placeholders is just "?,?,?" — only '?' chars. URL values bind
        # through the params list. No user input in the SQL string.
        # See TestSQLInjectionSafety.
        placeholders = ",".join("?" * len(all_urls))
        rows = self._conn.execute(
            f"""SELECT url FROM enrichments
                WHERE url IN ({placeholders})
                  AND feasible = 1
                  AND (description IS NULL OR description = '')""",
            all_urls,
        ).fetchall()
        return [row["url"] for row in rows]

    def merge_into_jobs(self, jobs: list[dict]) -> list[dict]:
        """Merge sidecar enrichments into a list of raw jobs from all_jobs.json.

        Adds feasible, feasibility, feasibility_error, feasibility_rationale,
        description, salary fields from the sidecar. Jobs not in the sidecar
        get no enrichment fields (same as raw). Does not mutate the input
        list — returns a new list.
        """
        all_urls = [j.get("url", "") for j in jobs if j.get("url")]
        enrichments = self.get_many(all_urls)
        result = []
        for job in jobs:
            enriched = dict(job)  # shallow copy
            url = job.get("url", "")
            if url in enrichments:
                e = enrichments[url]
                if e.get("feasible") is not None:
                    enriched["feasible"] = e["feasible"]
                if e.get("feasibility") is not None:
                    enriched["feasibility"] = e["feasibility"]
                if e.get("feasibility_error") is not None:
                    enriched["feasibility_error"] = e["feasibility_error"]
                if e.get("feasibility_rationale") is not None:
                    enriched["feasibility_rationale"] = e["feasibility_rationale"]
                if e.get("description") is not None:
                    enriched["description"] = e["description"]
                if e.get("salary") is not None:
                    enriched["salary"] = e["salary"]
            result.append(enriched)
        return result

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        """Convert a DB row to a dict with Python-native types."""
        return {
            "url": row["url"],
            "feasible": bool(row["feasible"]) if row["feasible"] is not None else None,
            "feasibility": row["feasibility"],
            "feasibility_error": bool(row["feasibility_error"]) if row["feasibility_error"] is not None else None,
            "feasibility_rationale": row["feasibility_rationale"],
            "description": row["description"],
            "salary": row["salary"],
            "feasibility_checked_at": row["feasibility_checked_at"],
            "description_fetched_at": row["description_fetched_at"],
            "first_seen": row["first_seen"],
        }
