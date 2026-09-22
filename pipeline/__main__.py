#!/usr/bin/env python3
"""Job Hunter Pipeline CLI — LangGraph-based orchestrator.

This is the systemd entry point (replaces run_pipeline.py). It:
  1. Runs the prep phase (feasibility, fetch, discover) as plain functions.
  2. Invokes the per-job LangGraph for each new job (with checkpointer).
  3. Computes stats from final states, sends notifications, commits.

Usage:
    python3 -m pipeline [--dry-run] [--job <slug>] [--step <name>]

--dry-run: Process but don't move files or commit.
--job <slug>: Reprocess a single job by company-role slug.
--step <name>: Run a single node directly (for debugging). One of:
    feasibility, fetch, discover, grade-jd, triage, customize,
    grade-resume, truthfulness, should-continue, finalize, ready
"""
from __future__ import annotations

import argparse
import structlog
import sys
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver

from pipeline.infrastructure.notify import notify_error, notify_pipeline_summary, notify_ready
from pipeline.infrastructure.delta_sync import ColdStartError, sync_deltas
from pipeline.infrastructure.config import PipelineConfig, load_config
from pipeline.infrastructure.file_ops import company_role_slug
from pipeline.infrastructure.graph import build_job_graph
from pipeline.infrastructure.lifecycle import (
    acquire_lock,
    check_scraper_updates,
    cleanup_transient_dirs,
    get_scraper_sha,
    git_commit_and_push,
    load_state,
    release_lock,
    save_state,
    setup_logging,
    update_scraper,
)
from pipeline.infrastructure.llm_interface import RealLLM
from pipeline.infrastructure.observability import setup_tracing
from pipeline.infrastructure.paths import Paths
from pipeline.infrastructure.state import JobState, TriageDestination, build_job_state_from_filesystem
from pipeline.steps.step1_feasibility import step1_feasibility
from pipeline.steps.step2_fetch_jds import step2_fetch_jds
from pipeline.steps.step3_discover import step3_discover


# ─── Step map for --step mode ────────────────────────────────────────────────

# Lazy import to avoid pulling all node modules at startup
def _get_step_map():
    from pipeline.steps.step10_final_veracity import step10_final_veracity_node
    from pipeline.steps.step11_finalize import (
        rejected_job_fit_node,
        rejected_resume_node,
        step11_finalize_node,
        step11_ready_node,
        trash_node,
    )
    from pipeline.steps.step3_ingest import step3_ingest_node
    from pipeline.steps.step4_grade_jd import step4_grade_jd_node
    from pipeline.steps.step5_triage import step5_triage_node
    from pipeline.steps.step6_customize import step6_customize_node
    from pipeline.steps.step7_grade_resume import step7_grade_resume_node
    from pipeline.steps.step8_veracity import step8_veracity_node
    from pipeline.steps.step9_should_continue import step9_should_continue_node

    return {
        "grade-jd": step4_grade_jd_node,
        "triage": step5_triage_node,
        "customize": step6_customize_node,
        "grade-resume": step7_grade_resume_node,
        "truthfulness": step8_veracity_node,
        "should-continue": step9_should_continue_node,
        "final-truthfulness": step10_final_veracity_node,
        "finalize": step11_finalize_node,
        "ready": step11_ready_node,
        "trash": trash_node,
        "rejected_job_fit": rejected_job_fit_node,
        "rejected_resume": rejected_resume_node,
        "ingest": step3_ingest_node,
    }


# ─── CLI ─────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Job Hunter Pipeline — LangGraph-based orchestrator"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Process but don't move files or commit",
    )
    parser.add_argument(
        "--job", type=str, default=None,
        help="Reprocess a single job (by company-role slug)",
    )
    parser.add_argument(
        "--step", type=str, default=None,
        help="Run only one step/node (for debugging): "
             "feasibility, fetch, discover, grade-jd, triage, customize, "
             "grade-resume, truthfulness, should-continue, finalize, ready, "
             "trash, rejected_job_fit, rejected_resume, ingest",
    )
    parser.add_argument(
        "--llm-provider",
        choices=("devin", "codex", "claude"),
        default=None,
        help="Override config.json llm_provider for this run",
    )
    return parser.parse_args()


def run_single_step(
    step_name: str,
    slug: str,
    config: PipelineConfig,
    llm,
    logger,
    paths: Paths,
) -> None:
    """--step mode: call a single node function directly (bypass graph).

    Constructs a JobState from existing files on disk, calls the node,
    and prints the result + updated state.
    """
    step_map = _get_step_map()
    node_fn = step_map.get(step_name)
    if not node_fn:
        logger.error(f"Unknown step: {step_name}")
        logger.info(f"Available steps: {', '.join(sorted(step_map.keys()))}")
        return

    state = build_job_state_from_filesystem(slug, paths)
    job_logger = logger.bind(slug=slug, node=step_name)
    node_config: RunnableConfig = {
        "configurable": {
            "llm": llm,
            "logger": job_logger,
            "config": config,
            "paths": paths,
        }
    }

    job_logger.info(f"Running step '{step_name}' for '{slug}'")
    result = node_fn(state, node_config)
    job_logger.info(f"Step result: {result}")

    # Pretty-print the updated state for debugging
    updated = state.model_copy(update=result)
    print(updated.model_dump_json(indent=2))


# ─── Main ────────────────────────────────────────────────────────────────────


def main() -> None:
    args = parse_args()
    hunter_dir = Path(__file__).resolve().parent.parent
    paths = Paths.from_hunter_dir(hunter_dir)
    logger = setup_logging(paths)
    config = load_config(paths.config_file)

    # Rebuild Paths with the configured profile filenames. The config
    # file's own location stays a fixed convention — it's how the config
    # is found in the first place.
    paths = Paths.from_hunter_dir(
        hunter_dir,
        base_resume_filename=config.profile.base_resume_file,
        linkedin_experience_filename=config.profile.linkedin_experience_file,
    )

    # Apply --dry-run flag (overrides config)
    if args.dry_run:
        config = config.model_copy(update={"dry_run": True})

    provider = args.llm_provider or config.llm_provider
    llm = RealLLM(
        db_path=str(paths.jobs_db),
        export_dir=str(paths.exports),
        provider=provider,
    )

    # LLM observability (ADR-0014) — optional, no-op without Phoenix installed.
    if setup_tracing(fallback_dir=str(paths.exports)):
        logger.info("Phoenix tracing enabled.")

    logger.info("=" * 60)
    logger.info("Job Hunter Pipeline starting (LangGraph)")
    logger.info(f"Dry run: {config.dry_run}")
    logger.info(f"LLM provider: {provider}")
    if args.job:
        logger.info(f"Single job: {args.job}")
    if args.step:
        logger.info(f"Single step: {args.step}")
    logger.info("=" * 60)

    # Acquire lock
    if not acquire_lock(paths, logger):
        return

    state = load_state(paths)
    stats = {
        "processed": 0,
        "trashed": 0,
        "rejected_job_fit": 0,
        "rejected_resume": 0,
        "ready": 0,
        "errors": 0,
    }

    try:
        # ── --step mode: call a single node directly ──
        if args.step and args.step not in ("feasibility", "fetch", "discover"):
            if not args.job:
                logger.error("--step requires --job <slug>")
                return
            run_single_step(args.step, args.job, config, llm, logger, paths)
            return

        # ── Prep phase (plain functions) ──
        # Initialize enrichment store
        from pipeline.infrastructure.enrichment_store import EnrichmentStore

        store = EnrichmentStore(str(paths.jobs_db))

        # Sync jobs from scraper: delta sync (default) or full sync (cold start).
        # Skip if --job (reprocessing a single job, no scraper sync needed).
        if not args.job:
            logger.info("Step 0: Syncing jobs from scraper")
            try:
                # Try delta sync first.
                result = sync_deltas(state, paths, config, store, logger)
                if result["deltas_processed"] > 0:
                    logger.info(
                        f"  Delta sync: {result['deltas_processed']} delta(s), "
                        f"+{result['jobs_added']} added, "
                        f"{result['jobs_updated']} updated."
                    )
                    # Append new processed deltas to state.
                    state["processed_deltas"].extend(
                        result["new_processed_deltas"]
                    )
                else:
                    logger.info("  No new deltas.")

            except ColdStartError:
                # First run ever — do a full sync, then retry delta sync
                # to catch any deltas produced after the full sync snapshot.
                logger.info(
                    "  Cold start: no deltas processed yet. "
                    "Running full sync from all_jobs.json..."
                )
                try:
                    count = store.sync_jobs_db(
                        str(paths.all_jobs_path), quiet=True, logger=logger
                    )
                    logger.info(f"  Full sync: {count} jobs from all_jobs.json.")
                except Exception as e:
                    logger.error(
                        f"  Full sync failed: {e}. "
                        f"Pipeline halted. Manual intervention required."
                    )
                    raise

                # Mark all existing deltas as processed (they're covered
                # by the full sync). Then retry delta sync for any deltas
                # produced after the full sync snapshot.
                try:
                    from pipeline.infrastructure.delta_sync import _read_index, _parse_index
                    index_text = _read_index(paths, config)
                    entries = _parse_index(index_text)
                    state["processed_deltas"] = [
                        e["run_at"] for e in entries if e.get("run_at")
                    ]
                    logger.info(
                        f"  Marked {len(state['processed_deltas'])} "
                        f"existing delta(s) as processed."
                    )
                except FileNotFoundError:
                    # No deltas directory yet — that's fine for a cold start.
                    state["processed_deltas"] = []
                    logger.info("  No existing deltas to mark as processed.")

                # Retry delta sync (catches deltas produced after full sync).
                try:
                    result = sync_deltas(state, paths, config, store, logger)
                    if result["deltas_processed"] > 0:
                        logger.info(
                            f"  Delta sync (post-cold-start): "
                            f"{result['deltas_processed']} delta(s), "
                            f"+{result['jobs_added']} added, "
                            f"{result['jobs_updated']} updated."
                        )
                        state["processed_deltas"].extend(
                            result["new_processed_deltas"]
                        )
                except ColdStartError:
                    # Still no deltas — fine, full sync covered everything.
                    pass

            except Exception as e:
                # Delta sync failed (not cold start) — HALT the pipeline.
                # Do NOT fall back to full sync. The error is loud and clear.
                logger.error(
                    f"  Delta sync failed — pipeline halted. "
                    f"Manual intervention required. Error: {e}"
                )
                state["last_run"] = datetime.now(timezone.utc).isoformat()
                state["last_run_status"] = "error"
                state["last_run_stats"] = stats
                save_state(state, paths, logger)
                notify_error("pipeline", f"Delta sync failed: {e}"[:500])
                stats["errors"] += 1
                return

        # Check for scraper updates (skip if --job or --step)
        if not args.job and not args.step:
            if not check_scraper_updates(state, paths, logger):
                logger.info("No scraper changes. Exiting.")
                save_state(state, paths, logger)
                release_lock(paths, logger)
                return
            update_scraper(paths, logger)

        # Step 1: Feasibility check (skip in --job mode — single job bypasses prep)
        if not args.job and (not args.step or args.step == "feasibility"):
            step1_feasibility(config, logger, store, paths, llm=llm)

        # Step 2: Fetch missing JDs
        if not args.step or args.step == "fetch":
            step2_fetch_jds(logger, store, paths)

        # Step 3: Discover new jobs
        if not args.step or args.step == "discover":
            if args.job:
                # Single job mode — construct a minimal job dict
                new_jobs = [{"slug": args.job}]
                logger.info(f"Single job mode: {args.job}")
            else:
                prep_result = step3_discover(logger, store, paths)
                new_jobs = prep_result.new_jobs
        else:
            new_jobs = []

        if not new_jobs and not args.step:
            logger.info("No new jobs to process. Done.")
            save_state(state, paths, logger)
            release_lock(paths, logger)
            return

        # ── Per-job phase (LangGraph) ──
        if args.step:
            # --step was one of the prep steps — we're done
            logger.info(f"Step '{args.step}' complete.")
            save_state(state, paths, logger)
            release_lock(paths, logger)
            return

        # Build the graph with checkpointer
        checkpointer = SqliteSaver.from_conn_string(str(paths.checkpoints_db))
        graph = build_job_graph(checkpointer=checkpointer)

        final_states = []
        for job in new_jobs:
            slug = job.get("slug") or company_role_slug(
                job.get("company", ""), job.get("title", "")
            )
            try:
                initial_state = JobState(
                    slug=slug,
                    url=job.get("url", ""),
                    company=job.get("company", ""),
                    title=job.get("title", ""),
                    jd_text=job.get("description", ""),
                )
                # Bind slug onto a per-job logger so every log call inside
                # the graph (and the per-job error boundary below) carries
                # the slug correlation key (ADR-0014).
                job_logger = logger.bind(slug=slug)
                node_config: RunnableConfig = {
                    "configurable": {
                        "llm": llm,
                        "logger": job_logger,
                        "config": config,
                        "paths": paths,
                    },
                    "thread_id": slug,  # per-job checkpoint thread
                }
                result = graph.invoke(initial_state, config=node_config)
                final_state = JobState(**result)
                final_states.append(final_state)

                if final_state.final_destination == TriageDestination.READY:
                    notify_ready(slug, final_state.title)

                job_logger.info(
                    f"  {slug}: -> {final_state.final_destination}"
                    if final_state.final_destination
                    else f"  {slug}: no final destination"
                )

            except Exception as e:
                logger.error(f"Job {slug} failed: {e}", exc_info=True)
                notify_error("pipeline", str(e)[:500], slug)
                stats["errors"] += 1

        # ── Stats (computed from final states) ──
        stats["processed"] = len(final_states)
        stats["trashed"] = sum(
            1 for s in final_states
            if s.final_destination == TriageDestination.TRASH
        )
        stats["rejected_job_fit"] = sum(
            1 for s in final_states
            if s.final_destination == TriageDestination.REJECTED_JOB_FIT
        )
        stats["rejected_resume"] = sum(
            1 for s in final_states
            if s.final_destination == TriageDestination.REJECTED_RESUME
        )
        stats["ready"] = sum(
            1 for s in final_states
            if s.final_destination == TriageDestination.READY
        )

        logger.info(f"Run summary: {stats}")
        if config.notifications_enabled:
            notify_pipeline_summary(stats)

        # ── Lifecycle: git commit + push ──
        if not config.dry_run:
            git_commit_and_push(stats, paths, logger, config.git_push)

        # ── Save state ──
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        state["last_run_status"] = "success"
        state["last_run_stats"] = stats
        state["last_scraper_sha"] = get_scraper_sha(paths, logger)
        save_state(state, paths, logger)

        logger.info("Pipeline complete.")

    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=True)
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        state["last_run_status"] = "error"
        state["last_run_stats"] = stats
        save_state(state, paths, logger)
        notify_error("pipeline", str(e)[:500])
        stats["errors"] += 1
    finally:
        cleanup_transient_dirs(paths, logger)
        release_lock(paths, logger)


if __name__ == "__main__":
    main()
