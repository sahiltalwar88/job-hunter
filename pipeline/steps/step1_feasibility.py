"""Step 1: Feasibility check (batch, plain function — not a graph node).

Uses job-hunter's own LLMFeasibilityChecker (ported from the scraper) to tag
jobs with feasible: true/false + feasibility tier. Verdicts are stored
in the enrichment sidecar (data/jobs.db), not in all_jobs.json.

Skips jobs the scraper has already tagged (get_unchecked_urls checks
both the enrichments and jobs tables).
"""
from __future__ import annotations

import structlog

from pipeline.infrastructure.config import PipelineConfig
from pipeline.infrastructure.paths import Paths


def step1_feasibility(
    config: PipelineConfig,
    logger: structlog.stdlib.BoundLogger,
    store,
    paths: Paths,
    llm=None,
) -> None:
    """Run feasibility check on unenriched jobs.

    Args:
        config: Pipeline config (model, timeout, feasibility_prompt).
        logger: Logger.
        store: EnrichmentStore instance (data/jobs.db).
        paths: Workspace paths.
        llm: LLM callable (RealLLM or FakeLLM). If None, creates a RealLLM.
    """
    logger.info("Step 1: Feasibility check")
    try:
        from pipeline.infrastructure.feasibility_checker import LLMFeasibilityChecker
        from pipeline.infrastructure.llm_interface import RealLLM

        if llm is None:
            llm = RealLLM(
                db_path=str(paths.jobs_db),
                export_dir=str(paths.exports),
                provider=config.llm_provider,
            )

        # Query all jobs from the DB mirror
        jobs = store.query_jobs(status="all")

        # Find URLs not yet checked (skips jobs the scraper already tagged)
        all_urls = [j.get("url", "") for j in jobs if j.get("url")]
        unchecked_urls = store.get_unchecked_urls(all_urls)
        if not unchecked_urls:
            logger.info("  All jobs already checked.")
            return

        # Build list of unchecked jobs for the checker
        url_to_job = {j.get("url", ""): j for j in jobs}
        unchecked_jobs = [url_to_job[u] for u in unchecked_urls if u in url_to_job]

        prompt = config.feasibility_prompt
        model = config.models.customizer
        timeout = config.timeout_for("feasibility")
        checker = LLMFeasibilityChecker(
            llm=llm, model=model, prompt=prompt, timeout=timeout,
            workspace=str(paths.hunter_dir),
            retries=config.llm_retries,
            retry_delay=config.llm_retry_delay,
        )

        batch_size = checker.BATCH_SIZE
        error_count = 0
        for i in range(0, len(unchecked_jobs), batch_size):
            batch = unchecked_jobs[i : i + batch_size]
            try:
                verdicts = checker.check_batch(batch)
            except Exception as e:
                logger.error(
                    f"  Batch {i // batch_size + 1} failed: "
                    f"{type(e).__name__}: {e}"
                )
                verdicts = {}

            batch_errors = 0
            for job in batch:
                url = job.get("url", "")
                first_seen = job.get("first_seen")
                if url in verdicts:
                    tier, rationale = verdicts[url]
                    tier = tier.lower()
                    store.set_feasibility(
                        url,
                        feasible=(tier != "no"),
                        feasibility=tier,
                        error=False,
                        first_seen=first_seen,
                        rationale=rationale,
                    )
                else:
                    # Safe default: feasible=true, mark as error
                    store.set_feasibility(
                        url,
                        feasible=True,
                        feasibility="yes",
                        error=True,
                        first_seen=first_seen,
                    )
                    batch_errors += 1

            error_count += batch_errors
            checked = min(i + batch_size, len(unchecked_jobs))
            if batch_errors:
                logger.warning(
                    f"  Checked {checked}/{len(unchecked_jobs)} "
                    f"({batch_errors} errors this batch)"
                )
            else:
                logger.info(f"  Checked {checked}/{len(unchecked_jobs)}")

        if error_count:
            logger.warning(
                f"Feasibility check complete: {error_count}/{len(unchecked_jobs)} "
                f"jobs defaulted to feasible (LLM errors). Re-run to retry."
            )
        else:
            logger.info("Feasibility check complete.")
    except Exception as e:
        logger.error(f"Feasibility check error: {e}")
