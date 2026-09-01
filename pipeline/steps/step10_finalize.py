"""Step 10 + terminal nodes: finalize and move to ready/rejected/trash.

This module contains:
- step10_ready_node: moves drafts/<slug>/ to ready/ when passing
- trash_node: moves listings/<slug>/ to trash/ (clearance or grade < 6)
- rejected_job_fit_node: moves listings/<slug>/ to rejected/[JOB-FIT] (grade 6-7.9)
- rejected_resume_node: moves drafts/<slug>/ to rejected/[RESUME] (grade < 9 or unverified)

All are LangGraph nodes. They set final_destination on state. The
conditional edges route to these nodes based on grade/verification.
After each move, they record a state_transitions audit row (E2, ADR-0012).
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from pipeline.infrastructure.file_ops import move_dir
from pipeline.infrastructure.llm_interface import get_deps
from pipeline.infrastructure.state import JobState, TriageDestination
from pipeline.infrastructure.state_store import JobStatus, StateStore


def _record(deps, slug: str, from_status: JobStatus, to_status: JobStatus,
            reason: str) -> None:
    """Record a state transition after a successful move (E2, ADR-0012).

    Failure-isolated — never raises. Constructed lazily so dry_run and
    missing-folder paths don't touch the DB.
    """
    store = StateStore(str(deps.paths.jobs_db))
    store.record_transition(slug, from_status, to_status, reason=reason)
    store.close()


def step10_ready_node(state: JobState, config: RunnableConfig) -> dict:
    """Move the job folder from drafts/ to ready/ (grade >= 9 AND verified).

    Returns partial state with final_destination = READY.
    In dry_run, skips the move.
    """
    deps = get_deps(config)
    slug = state.slug

    if not deps.config.dry_run:
        src = deps.paths.drafts / slug
        if src.exists():
            move_dir(src, deps.paths.ready)
            _record(deps, slug, JobStatus.DRAFTS, JobStatus.READY, "grade >= 9 + verified")

    deps.logger.info(f"  {slug}: -> ready/")
    return {"final_destination": TriageDestination.READY}


# ─── Terminal nodes ──────────────────────────────────────────────────────────


def trash_node(state: JobState, config: RunnableConfig) -> dict:
    """Move listings/<slug>/ to trash/ (clearance or grade < 6).

    Returns partial state with final_destination = TRASH.
    In dry_run, skips the move.
    """
    deps = get_deps(config)
    slug = state.slug

    if not deps.config.dry_run:
        src = deps.paths.listings / slug
        if src.exists():
            move_dir(src, deps.paths.trash)
            _record(deps, slug, JobStatus.LISTINGS, JobStatus.TRASH, "clearance or grade < 6")

    deps.logger.info(f"  {slug}: -> trash/")
    return {"final_destination": TriageDestination.TRASH}


def rejected_job_fit_node(state: JobState, config: RunnableConfig) -> dict:
    """Move listings/<slug>/ to rejected/[JOB-FIT] <slug>/ (grade 6-7.9).

    Returns partial state with final_destination = REJECTED_JOB_FIT.
    In dry_run, skips the move.
    """
    deps = get_deps(config)
    slug = state.slug

    if not deps.config.dry_run:
        src = deps.paths.listings / slug
        if src.exists():
            move_dir(src, deps.paths.rejected, prefix="[JOB-FIT]")
            _record(deps, slug, JobStatus.LISTINGS, JobStatus.REJECTED_JOB_FIT, "grade 6-7.9")

    deps.logger.info(f"  {slug}: -> rejected/[JOB-FIT]")
    return {"final_destination": TriageDestination.REJECTED_JOB_FIT}


def rejected_resume_node(state: JobState, config: RunnableConfig) -> dict:
    """Move drafts/<slug>/ to rejected/[RESUME] <slug>/ (grade < 9 or unverified).

    Returns partial state with final_destination = REJECTED_RESUME.
    In dry_run, skips the move.
    """
    deps = get_deps(config)
    slug = state.slug

    if not deps.config.dry_run:
        src = deps.paths.drafts / slug
        if src.exists():
            move_dir(src, deps.paths.rejected, prefix="[RESUME]")
            _record(deps, slug, JobStatus.DRAFTS, JobStatus.REJECTED_RESUME, "grade < 9 or unverified")

    deps.logger.info(f"  {slug}: -> rejected/[RESUME]")
    return {"final_destination": TriageDestination.REJECTED_RESUME}
