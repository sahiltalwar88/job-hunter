"""Step 2: Fetch missing JDs (batch, plain function — not a graph node).

Fetches JD descriptions from LinkedIn for feasible jobs without them.
Descriptions are stored in the enrichment sidecar (data/jobs.db), not
in all_jobs.json. Built-in exponential backoff for LinkedIn rate-limiting.
"""
from __future__ import annotations

import logging

from pipeline.infrastructure.paths import Paths


def step2_fetch_jds(
    logger: logging.Logger,
    store,
    paths: Paths,
) -> tuple[int, int]:
    """Fetch JDs for feasible jobs without descriptions.

    Args:
        logger: Logger.
        store: EnrichmentStore instance (data/jobs.db).
        paths: Workspace paths (for all_jobs_path).

    Returns:
        (fetched_count, failed_count)
    """
    logger.info("Step 2: Fetch missing JDs")
    try:
        import fetch_jds

        fetched, failed = fetch_jds.fetch_jds(str(paths.all_jobs_path), store=store)
        logger.info(f"JDs fetched: {fetched}, failed: {failed}")
        return fetched, failed
    except Exception as e:
        logger.error(f"JD fetch error: {e}")
        return 0, 0
