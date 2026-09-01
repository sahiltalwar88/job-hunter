#!/usr/bin/env python3
"""Delta sync — incremental job ingestion from the scraper.

Consumes delta files from the scraper's output/deltas/ directory (or HTTP
equivalent) instead of re-syncing the full all_jobs.json every run.

Flow:
    1. Read output/deltas/index.jsonl (the manifest).
    2. Filter to run_at values not in state["processed_deltas"].
    3. For each unprocessed delta: read the delta file, upsert via
       EnrichmentStore.sync_delta(), append run_at to processed_deltas.
    4. Return a summary.

Cold start:
    If state["processed_deltas"] is empty (first run ever), raise
    ColdStartError. The caller should do a full sync via sync_jobs_db()
    first, then retry delta sync.

Error handling:
    On any error (network failure, parse error, file not found for a
    delta that IS in the manifest but NOT in processed_deltas), log a
    LOUD error and raise. Do NOT fall back to full sync. The caller
    decides what to do.

    If a delta file is missing but its run_at IS in processed_deltas,
    skip it with a warning (the job data was already upserted in a
    previous run — the scraper prunes delta files after 30 days).

Transport:
    - "filesystem" (default): read from local paths via Paths.deltas_dir.
    - "http": fetch from scraper_url + /deltas/index.jsonl and
      scraper_url + /deltas/<filename> via urllib.request.urlopen.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline.infrastructure.enrichment_store import EnrichmentStore
    from pipeline.infrastructure.config import PipelineConfig
    from pipeline.infrastructure.paths import Paths


class ColdStartError(Exception):
    """Raised when no deltas have been processed yet — caller should do
    full sync via sync_jobs_db() first, then retry delta sync."""


def _read_file(path: Path) -> str:
    """Read a file as text (filesystem transport)."""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _fetch_url(url: str) -> str:
    """Fetch a URL as text (HTTP transport)."""
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _read_index(paths: Paths, config: PipelineConfig) -> str:
    """Read the delta manifest (index.jsonl) via the configured transport."""
    if config.scraper_transport == "http" and config.scraper_url:
        url = config.scraper_url.rstrip("/") + "/deltas/index.jsonl"
        return _fetch_url(url)
    # filesystem (default)
    if not paths.deltas_index.exists():
        raise FileNotFoundError(
            f"Delta manifest not found: {paths.deltas_index}. "
            f"Ensure the scraper repo path is correct and the scraper "
            f"has been run at least once."
        )
    return _read_file(paths.deltas_index)


def _read_delta_file(
    filename: str, paths: Paths, config: PipelineConfig
) -> str:
    """Read a delta file via the configured transport."""
    if config.scraper_transport == "http" and config.scraper_url:
        url = config.scraper_url.rstrip("/") + "/deltas/" + filename
        return _fetch_url(url)
    # filesystem (default)
    delta_path = paths.deltas_dir / filename
    if not delta_path.exists():
        raise FileNotFoundError(f"Delta file not found: {delta_path}")
    return _read_file(delta_path)


def _parse_index(index_text: str) -> list[dict]:
    """Parse index.jsonl into a list of manifest entries.

    Each line is a JSON object with run_at, file, source, added, updated.
    Blank lines are skipped.
    """
    entries = []
    for line in index_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        entries.append(json.loads(line))
    return entries


def sync_deltas(
    state: dict,
    paths: Paths,
    config: PipelineConfig,
    store: "EnrichmentStore",
    logger: logging.Logger,
) -> dict:
    """Discover and consume unprocessed delta files.

    Args:
        state: Pipeline state dict (must contain "processed_deltas" list).
        paths: Workspace Paths.
        config: PipelineConfig (scraper_transport, scraper_url).
        store: EnrichmentStore for upserting jobs.
        logger: Logger for errors/warnings/info.

    Returns:
        Summary dict with:
            - deltas_processed: number of new deltas consumed
            - jobs_added: total rows upserted from "added" arrays
            - jobs_updated: total rows upserted from "updated" arrays
            - new_processed_deltas: list of run_at strings to append to state

    Raises:
        ColdStartError: if state["processed_deltas"] is empty (first run).
        FileNotFoundError: if manifest or a needed delta file is missing.
        json.JSONDecodeError: if manifest or delta file is malformed.
        URLError: if HTTP fetch fails.
    """
    processed = state.get("processed_deltas", [])

    # Cold start: no deltas processed yet → caller should do full sync first.
    if not processed:
        raise ColdStartError(
            "No deltas have been processed yet. Run a full sync via "
            "sync_jobs_db() first, then retry delta sync."
        )

    # Read + parse the manifest.
    try:
        index_text = _read_index(paths, config)
    except Exception as e:
        logger.error(f"Delta sync failed: could not read delta manifest: {e}")
        raise

    try:
        entries = _parse_index(index_text)
    except json.JSONDecodeError as e:
        logger.error(f"Delta sync failed: manifest is malformed: {e}")
        raise

    # Filter to unprocessed deltas.
    processed_set = set(processed)
    unprocessed = [e for e in entries if e.get("run_at") not in processed_set]

    if not unprocessed:
        logger.info("No new deltas to process.")
        return {
            "deltas_processed": 0,
            "jobs_added": 0,
            "jobs_updated": 0,
            "new_processed_deltas": [],
        }

    logger.info(f"Found {len(unprocessed)} new delta(s) to process.")

    total_added = 0
    total_updated = 0
    new_processed = []

    for entry in unprocessed:
        run_at = entry.get("run_at")
        filename = entry.get("file")
        if not run_at or not filename:
            logger.warning(
                f"Skipping malformed manifest entry: {entry}"
            )
            continue

        # If the delta file is missing but run_at is in processed_deltas,
        # skip with a warning (scraper prunes after 30 days; data already
        # upserted in a previous run). This shouldn't happen here since
        # we filtered to unprocessed, but guard against it anyway.
        if run_at in processed_set:
            continue

        # Read + upsert the delta file.
        try:
            delta_text = _read_delta_file(filename, paths, config)
        except FileNotFoundError as e:
            # Delta file was pruned before we processed it.
            # This is a real problem — we haven't upserted these jobs yet.
            # Log loud and raise.
            logger.error(
                f"Delta sync failed: delta file '{filename}' (run_at={run_at}) "
                f"is listed in the manifest but does not exist. "
                f"The scraper may have pruned it before we could process it. "
                f"Manual intervention required: run a full sync via "
                f"sync_jobs_db() to recover missing jobs, then clear "
                f"processed_deltas to restart delta tracking."
            )
            raise

        try:
            # Write to a temp file for sync_delta (which takes a path).
            # For filesystem transport, we could pass the path directly,
            # but for HTTP we need to write first. Using a consistent
            # approach simplifies the code.
            import tempfile
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(delta_text)
                tmp_path = tmp.name

            try:
                added, updated = store.sync_delta(
                    tmp_path, quiet=True, logger=logger
                )
            finally:
                import os
                os.unlink(tmp_path)

        except Exception as e:
            logger.error(
                f"Delta sync failed: error processing delta '{filename}' "
                f"(run_at={run_at}): {e}"
            )
            raise

        total_added += added
        total_updated += updated
        new_processed.append(run_at)
        logger.info(
            f"  Delta {filename}: +{added} added, {updated} updated"
        )

    return {
        "deltas_processed": len(new_processed),
        "jobs_added": total_added,
        "jobs_updated": total_updated,
        "new_processed_deltas": new_processed,
    }
