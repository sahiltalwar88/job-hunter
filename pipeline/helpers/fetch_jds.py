#!/usr/bin/env python3
"""Fetch JDs for feasible jobs.

Reads raw jobs from all_jobs.json (read-only), merges feasibility tags from
the enrichment sidecar, fetches JD descriptions from LinkedIn for feasible
jobs without descriptions, and writes them to the sidecar — never writes
to all_jobs.json.

Reuses the scraper's _linkedin_posting_details (LinkedIn HTTP + HTML parsing)
via deferred import. The sidecar handles persistence.

Usage:
    python3 -m pipeline.helpers.fetch_jds

Environment variables:
    JOB_SCRAPER_DIR  — path to the scraper repo (default: /path/to/job-scraper)
    ALL_JOBS_PATH    — path to all_jobs.json (default: $JOB_SCRAPER_DIR/output/all_jobs.json)
"""
import json
import os
import random
import re
import sys
import time

# Path to the adjacent scraper repo
SCRAPER_DIR = os.environ.get("JOB_SCRAPER_DIR", os.path.expanduser("/path/to/job-scraper"))
ALL_JOBS = os.environ.get("ALL_JOBS_PATH", os.path.join(SCRAPER_DIR, "output", "all_jobs.json"))

# Import from the scraper repo — deferred so tests can mock these without
# requiring the scraper repo to be present at import time.
_linkedin_posting_details = None
_LINKEDIN_REQUEST_DELAY = None


def _ensure_scraper_imports():
    """Import scraper functions on first use (deferred for testability)."""
    global _linkedin_posting_details, _LINKEDIN_REQUEST_DELAY
    if _linkedin_posting_details is not None:
        return
    sys.path.insert(0, SCRAPER_DIR)
    from scrape_jobs import _linkedin_posting_details as _lpd, LINKEDIN_REQUEST_DELAY as _lrd
    _linkedin_posting_details = _lpd
    _LINKEDIN_REQUEST_DELAY = _lrd


def fetch_jds(all_jobs_path=ALL_JOBS, store=None):
    """Fetch JDs for feasible jobs without descriptions.

    Queries the SQLite mirror for feasible jobs, fetches JD descriptions
    from LinkedIn for those without descriptions, and writes them to the
    enrichment store.

    Args:
        all_jobs_path: Path to the scraper's all_jobs.json (for sync fallback).
        store: EnrichmentStore instance. If None, creates a default one.

    Returns:
        (fetched_count, failed_count)
    """
    _ensure_scraper_imports()
    if store is None:
        from pipeline.infrastructure.enrichment_store import EnrichmentStore
        store = EnrichmentStore()

    # Query feasible jobs from the DB mirror (no full-file JSON parse)
    feasible_jobs = store.query_jobs(status="feasible")

    # Find feasible URLs that still need descriptions
    url_to_job = {j.get("url", ""): j for j in feasible_jobs if j.get("url")}
    all_urls = list(url_to_job.keys())
    need_desc_urls = set(store.get_undescribed_feasible_urls(all_urls))
    to_fetch = [url_to_job[u] for u in need_desc_urls if u in url_to_job]
    print(f"Fetching JDs for {len(to_fetch)} feasible jobs without descriptions...")

    if not to_fetch:
        print("  All feasible jobs already have descriptions.")
        return 0, 0

    fetched = 0
    failed = 0
    for i, job in enumerate(to_fetch):
        url = job.get("url", "")
        m = re.search(r'/jobs/view/(\d+)', url)
        if not m:
            continue
        time.sleep(_LINKEDIN_REQUEST_DELAY + random.uniform(0, 2))
        try:
            salary, desc = _linkedin_posting_details(m.group(1))
            if desc:
                store.set_description(url, desc, salary=salary if salary else None,
                                      first_seen=job.get("first_seen"))
                fetched += 1
            # If no description (with or without salary), skip — the job will
            # be re-attempted next run. Writing an empty description would
            # falsely imply a JD was fetched.
        except Exception as e:
            print(f"  ⚠️  Failed: {job.get('title', '?')} — {e}")
            failed += 1
        if (i + 1) % 25 == 0:
            print(f"  📊 Fetched {i + 1}/{len(to_fetch)} ({fetched} ok, {failed} failed)")

    print(f"✅ Done: {fetched} descriptions fetched, {failed} failed")
    return fetched, failed


def main():
    fetch_jds()


if __name__ == "__main__":
    main()
