"""Workspace path constants.

All pipeline paths are derived from a single root (the job-hunter directory).
Tests can construct a Paths pointing at a tmp directory.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    """All workspace paths used by the pipeline.

    Frozen so it can be shared safely across nodes without mutation.
    """

    hunter_dir: Path
    listings: Path
    trash: Path
    drafts: Path
    rejected: Path
    ready: Path
    in_progress: Path
    submitted: Path
    logs: Path
    grading: Path
    veracity: Path
    grades_log: Path
    data_dir: Path
    jobs_db: Path
    checkpoints_db: Path
    devin_dir: Path
    lock_file: Path
    state_file: Path
    config_file: Path
    scraper_dir: Path
    all_jobs_path: Path
    deltas_dir: Path
    deltas_index: Path
    base_resume: Path
    linkedin_experience: Path
    agent_permissions: Path

    @staticmethod
    def from_hunter_dir(hunter_dir: Path) -> Paths:
        """Construct Paths from a given hunter root directory."""
        scraper_dir = Path(
            os.environ.get(
                "JOB_SCRAPER_DIR",
                os.path.expanduser("/path/to/job-scraper"),
            )
        )
        data_dir = hunter_dir / "data"
        devin_dir = hunter_dir / ".devin"
        stages = hunter_dir / "stages"
        profile = hunter_dir / "_config" / "profile"
        return Paths(
            hunter_dir=hunter_dir,
            listings=stages / "1_listings",
            trash=stages / "7_trash",
            drafts=stages / "2_drafts",
            rejected=stages / "6_rejected",
            ready=stages / "4_ready",
            in_progress=stages / "3_in-progress",
            submitted=stages / "5_submitted",
            logs=hunter_dir / "logs",
            grading=hunter_dir / ".grading",
            veracity=hunter_dir / ".veracity",
            grades_log=hunter_dir / ".grades.log",
            data_dir=data_dir,
            jobs_db=data_dir / "jobs.db",
            # Checkpointer lives in the same DB as jobs (ADR-0005).
            # LangGraph's SqliteSaver creates its own tables (checkpoints,
            # writes) in this file, keyed by thread_id = slug.
            checkpoints_db=data_dir / "jobs.db",
            devin_dir=devin_dir,
            lock_file=devin_dir / "pipeline.lock",
            state_file=devin_dir / "pipeline-state.json",
            config_file=hunter_dir / "config.json",
            scraper_dir=scraper_dir,
            all_jobs_path=scraper_dir / "output" / "all_jobs.json",
            deltas_dir=scraper_dir / "output" / "deltas",
            deltas_index=scraper_dir / "output" / "deltas" / "index.jsonl",
            base_resume=profile / "base-resume" / "base-resume.md",
            linkedin_experience=profile / "full-experience" / "full-experience.md",
            agent_permissions=hunter_dir / "util" / "agent-profiles" / "agent-permissions.json",
        )
