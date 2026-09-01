#!/usr/bin/env python3
"""List feasible jobs from the SQLite mirror with server-side pagination.

Agents should use this instead of reading all_jobs.json directly (11MB+).
The CLI auto-syncs from all_jobs.json when the file's mtime is newer than
the last sync. All queries use COALESCE so pipeline enrichments override
scraper values.

Usage:
    # Summary: list all feasible jobs (title/company/url only, no descriptions)
    python3 -m pipeline.helpers.list_feasible_jobs --summary

    # Full: first 10 feasible jobs with descriptions (for grading)
    python3 -m pipeline.helpers.list_feasible_jobs --limit 10

    # Full: next 10
    python3 -m pipeline.helpers.list_feasible_jobs --limit 10 --offset 10

    # Only feasible jobs that HAVE descriptions (ready for grading)
    python3 -m pipeline.helpers.list_feasible_jobs --limit 10 --has-description

    # Only feasible jobs MISSING descriptions (need fetch_jds.py first)
    python3 -m pipeline.helpers.list_feasible_jobs --limit 50 --missing-description

    # Only "preferred" tier jobs (Big Tech / top-tier fit)
    python3 -m pipeline.helpers.list_feasible_jobs --tier preferred --summary

    # Untagged jobs (need --feasibility-check first)
    python3 -m pipeline.helpers.list_feasible_jobs --status untagged --summary --limit 50

    # JSON output (default is human-readable; --json for machine consumption)
    python3 -m pipeline.helpers.list_feasible_jobs --limit 10 --json

    # --enriched is now a no-op (all queries are enriched by default)
    python3 -m pipeline.helpers.list_feasible_jobs --enriched --summary  # works, deprecated

Environment variables:
    JOB_SCRAPER_DIR  — path to the scraper repo (default: /path/to/job-scraper)
    ALL_JOBS_PATH    — path to all_jobs.json (default: $JOB_SCRAPER_DIR/output/all_jobs.json)
    JOBS_DB_PATH     — path to jobs.db (default: data/jobs.db)
"""
import argparse
import json
import os
import sys

SCRAPER_DIR = os.environ.get("JOB_SCRAPER_DIR", os.path.expanduser("/path/to/job-scraper"))
ALL_JOBS = os.environ.get("ALL_JOBS_PATH", os.path.join(SCRAPER_DIR, "output", "all_jobs.json"))
JOBS_DB = os.environ.get("JOBS_DB_PATH", os.path.join(os.getcwd(), "data", "jobs.db"))

# Track last sync time in-process to avoid re-checking mtime on every call
_last_sync_mtime: float = 0.0


def _maybe_sync(all_jobs_path: str = ALL_JOBS, db_path: str = JOBS_DB,
                force: bool = False) -> None:
    """Auto-sync from all_jobs.json if the file is newer than the last sync.

    Uses mtime to detect changes. Prints a progress message to stderr when
    a sync happens. No-ops if the file hasn't changed since the last sync
    in this process.
    """
    global _last_sync_mtime
    try:
        mtime = os.path.getmtime(all_jobs_path)
    except OSError:
        return  # file doesn't exist, nothing to sync

    if not force and mtime <= _last_sync_mtime:
        return  # already synced this mtime in this process

    from pipeline.infrastructure.enrichment_store import EnrichmentStore
    store = EnrichmentStore(db_path)
    try:
        print("  Syncing jobs database...", file=sys.stderr)
        store.sync_jobs_db(all_jobs_path)
        _last_sync_mtime = mtime
    finally:
        store.close()


def query_jobs(*, status: str = "feasible", tier: str | None = None,
               has_description: bool | None = None,
               limit: int = 0, offset: int = 0,
               all_jobs_path: str = ALL_JOBS, db_path: str = JOBS_DB) -> list[dict]:
    """Query jobs from the SQLite mirror with server-side filtering.

    Auto-syncs from all_jobs.json if the file is newer than the last sync.
    Returns list[dict] with the same keys as the old load_jobs() output.

    Args:
        status: "feasible", "infeasible", "untagged", or "all".
        tier: None, "preferred", "yes", or "no".
        has_description: None (any), True (has JD), False (no JD).
        limit: Max jobs (0 = all).
        offset: Skip this many jobs.
    """
    _maybe_sync(all_jobs_path, db_path)
    from pipeline.infrastructure.enrichment_store import EnrichmentStore
    store = EnrichmentStore(db_path)
    try:
        return store.query_jobs(status=status, tier=tier,
                                has_description=has_description,
                                limit=limit, offset=offset)
    finally:
        store.close()


def count_jobs(*, status: str = "feasible", tier: str | None = None,
               has_description: bool | None = None,
               all_jobs_path: str = ALL_JOBS, db_path: str = JOBS_DB) -> int:
    """Count jobs matching filters without fetching rows."""
    _maybe_sync(all_jobs_path, db_path)
    from pipeline.infrastructure.enrichment_store import EnrichmentStore
    store = EnrichmentStore(db_path)
    try:
        return store.count_jobs(status=status, tier=tier,
                                has_description=has_description)
    finally:
        store.close()


# ─── Back-compat shims (deprecated, call query_jobs under the hood) ────────

def load_jobs(path=ALL_JOBS, enriched=False):
    """DEPRECATED shim — use query_jobs() instead.

    Returns all jobs from the SQLite mirror. The `path` and `enriched`
    parameters are accepted for backward compatibility but have limited
    effect: `path` is used as the all_jobs.json source for syncing,
    `enriched` is a no-op (all queries are enriched by default now).
    """
    if enriched:
        print("  ⚠️  --enriched is deprecated (all queries are enriched by default).",
              file=sys.stderr)
    return query_jobs(status="all", all_jobs_path=path)


def filter_jobs(jobs, *, status="feasible", tier=None, has_description=None):
    """DEPRECATED shim — use query_jobs() instead.

    Filters an in-memory list of job dicts. Maintained for backward compat
    with callers that already have jobs in memory (e.g. pipeline_cli.py
    during the cutover period). New code should call query_jobs() directly.
    """
    if status == "feasible":
        result = [j for j in jobs if j.get("feasible") is True]
    elif status == "infeasible":
        result = [j for j in jobs if j.get("feasible") is False]
    elif status == "untagged":
        result = [j for j in jobs if "feasible" not in j or j.get("feasible") is None]
    elif status == "all":
        result = list(jobs)
    else:
        raise ValueError(f"Unknown status: {status}")

    if tier is not None:
        result = [j for j in result if j.get("feasibility") == tier]

    if has_description is True:
        result = [j for j in result if j.get("description")]
    elif has_description is False:
        result = [j for j in result if not j.get("description")]

    return result


def summarize(jobs):
    """Return a list of {url, title, company, location, date_posted, feasibility} dicts."""
    return [
        {
            "url": j.get("url", ""),
            "title": j.get("title", ""),
            "company": j.get("company", ""),
            "location": j.get("location", ""),
            "date_posted": j.get("date_posted", ""),
            "has_description": bool(j.get("description")),
            "feasibility": j.get("feasibility", ""),
        }
        for j in jobs
    ]


def full_jobs(jobs):
    """Return full job dicts (includes description)."""
    return jobs


def main():
    parser = argparse.ArgumentParser(
        description="List feasible jobs from the SQLite mirror with pagination."
    )
    parser.add_argument(
        "--status",
        choices=["feasible", "infeasible", "untagged", "all"],
        default="feasible",
        help="Filter by feasibility status (default: feasible).",
    )
    parser.add_argument(
        "--tier",
        choices=["preferred", "yes", "no"],
        default=None,
        help="Filter by feasibility tier (only applies to tagged jobs). "
             "'preferred' = Big Tech / top-tier fit; 'yes' = fits but not Big Tech; "
             "'no' = doesn't fit.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max jobs to return (default: 50). Use 0 for all.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip this many jobs before returning (default: 0).",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Output title/company/url only (no descriptions). Lightweight.",
    )
    parser.add_argument(
        "--has-description",
        action="store_true",
        help="Only return jobs that have a description (ready for grading).",
    )
    parser.add_argument(
        "--missing-description",
        action="store_true",
        help="Only return jobs missing a description (need fetch_jds.py).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON (default: human-readable table for summary, JSON for full).",
    )
    parser.add_argument(
        "--enriched",
        action="store_true",
        help="(Deprecated, no-op) All queries are enriched by default now. "
             "Pipeline enrichments override scraper values via COALESCE.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Force a sync from all_jobs.json before querying.",
    )
    args = parser.parse_args()

    if args.enriched:
        print("  ⚠️  --enriched is deprecated (all queries are enriched by default).",
              file=sys.stderr)

    if args.sync:
        _maybe_sync(force=True)

    # Apply description filter
    has_desc = None
    if args.has_description:
        has_desc = True
    elif args.missing_description:
        has_desc = False

    page = query_jobs(status=args.status, tier=args.tier,
                      has_description=has_desc,
                      limit=args.limit, offset=args.offset)
    total = count_jobs(status=args.status, tier=args.tier,
                       has_description=has_desc)

    # Output
    if args.summary:
        rows = summarize(page)
        if args.json or not sys.stdout.isatty():
            print(json.dumps({"total": total, "returned": len(rows), "offset": args.offset, "jobs": rows}, ensure_ascii=False, indent=2))
        else:
            print(f"Total: {total} | Showing: {len(rows)} (offset {args.offset})")
            print(f"{'Title':<45} {'Company':<25} {'Location':<25} {'Tier':<12} {'Has JD'}")
            print("-" * 130)
            for r in rows:
                title = r["title"][:43] + ".." if len(r["title"]) > 45 else r["title"]
                company = r["company"][:23] + ".." if len(r["company"]) > 25 else r["company"]
                location = r["location"][:23] + ".." if len(r["location"]) > 25 else r["location"]
                tier = r["feasibility"] or "-"
                print(f"{title:<45} {company:<25} {location:<25} {tier:<12} {'Y' if r['has_description'] else 'N'}")
    else:
        # Full output (with descriptions) — always JSON for machine consumption
        rows = full_jobs(page)
        print(json.dumps({"total": total, "returned": len(rows), "offset": args.offset, "jobs": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
