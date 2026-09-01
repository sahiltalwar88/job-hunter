"""Step 3: Discover new jobs (batch, plain function — not a graph node).

Queries feasible jobs with descriptions from the SQLite mirror, deduplicates
by URL against existing workspace folders, and returns the list of new jobs
to process. Ingestion (creating listing folders) is handled by the per-job
graph's step3_ingest node — this function only discovers.

Returns a PrepResult with new_jobs for the orchestrator to feed into the
per-job graph.
"""
from __future__ import annotations

import logging

from pipeline.infrastructure.paths import Paths
from pipeline.infrastructure.file_ops import discover_new_jobs


class PrepResult:
    """Result of the prep phase — new jobs to process."""

    def __init__(self, new_jobs: list[dict] | None = None):
        self.new_jobs = new_jobs or []

    def __repr__(self) -> str:
        return f"PrepResult(new_jobs={len(self.new_jobs)})"


def step3_discover(
    logger: logging.Logger,
    store,
    paths: Paths,
) -> PrepResult:
    """Discover new feasible jobs with descriptions that aren't already in the workspace.

    Args:
        logger: Logger.
        store: EnrichmentStore instance (data/jobs.db).
        paths: Workspace paths.

    Returns:
        PrepResult with new_jobs list (each job is a dict with url, company,
        title, description, etc.).
    """
    logger.info("Step 3: Discover new jobs")

    # Query feasible jobs with descriptions from the SQLite mirror
    feasible_jobs = store.query_jobs(status="feasible", has_description=True)
    logger.info(f"Found {len(feasible_jobs)} feasible jobs with descriptions.")

    if not feasible_jobs:
        logger.info("No feasible jobs with descriptions. Nothing to process.")
        return PrepResult(new_jobs=[])

    # Dedup by URL against existing workspace folders
    new_jobs = discover_new_jobs(feasible_jobs, paths)
    logger.info(f"New jobs (not in workspace): {len(new_jobs)}")

    if not new_jobs:
        logger.info("No new jobs to process.")
        return PrepResult(new_jobs=[])

    return PrepResult(new_jobs=new_jobs)
