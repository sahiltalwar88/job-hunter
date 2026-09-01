"""Lifecycle concerns — cross-cutting operations wrapped around the graph.

These are NOT graph nodes. The orchestrator (pipeline_cli.py) calls them
before/after invoking the per-job graph:
    - Logging setup
    - File lock (prevents concurrent pipeline runs)
    - Pipeline state (last scraper SHA, run metadata)
    - Scraper sync (fetch + pull the scraper repo)
    - Git commit + push (pipeline output only)
    - Cleanup of transient directories (.grading/, .veracity/)

All functions take Paths + logger as arguments (not module-level constants).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from pipeline.infrastructure.paths import Paths


# ─── Logging ───────────────────────────────────────────────────────────────


def setup_logging(paths: Paths) -> logging.Logger:
    """Set up per-run logging to logs/pipeline-YYYY-MM-DD-HHMM.log + console."""
    paths.logs.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    log_file = paths.logs / f"pipeline-{timestamp}.log"

    logger = logging.getLogger("pipeline")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # File handler — debug level
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)

    # Console handler — info level
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(ch)

    logger.info(f"Log file: {log_file}")
    return logger


# ─── Locking ──────────────────────────────────────────────────────────────


def acquire_lock(paths: Paths, logger: logging.Logger) -> bool:
    """Acquire the pipeline lock. Returns True if acquired, False if already held."""
    paths.devin_dir.mkdir(parents=True, exist_ok=True)
    if paths.lock_file.exists():
        try:
            pid = int(paths.lock_file.read_text().strip())
            # Check if the PID is still alive
            try:
                os.kill(pid, 0)
                logger.info(f"Pipeline already running (PID {pid}). Exiting.")
                return False
            except OSError:
                # Stale lock — remove it
                logger.warning(f"Stale lock from PID {pid}. Removing.")
                paths.lock_file.unlink()
        except (ValueError, OSError):
            # Corrupt lock file — remove it
            logger.warning("Corrupt lock file. Removing.")
            paths.lock_file.unlink()

    paths.lock_file.write_text(str(os.getpid()))
    return True


def release_lock(paths: Paths, logger: logging.Logger) -> None:
    """Release the pipeline lock."""
    if paths.lock_file.exists():
        try:
            paths.lock_file.unlink()
        except OSError:
            pass


# ─── Pipeline state ────────────────────────────────────────────────────────


def load_state(paths: Paths) -> dict:
    """Load pipeline state (last scraper SHA, run metadata)."""
    if paths.state_file.exists():
        with open(paths.state_file) as f:
            return json.load(f)
    return {
        "last_scraper_sha": None,
        "last_run": None,
        "last_run_status": None,
        "last_run_stats": {},
        "processed_deltas": [],
    }


def save_state(state: dict, paths: Paths, logger: logging.Logger) -> None:
    """Save pipeline state."""
    paths.devin_dir.mkdir(parents=True, exist_ok=True)
    with open(paths.state_file, "w") as f:
        json.dump(state, f, indent=2)
    logger.debug(f"State saved: {state}")


# ─── Scraper sync ──────────────────────────────────────────────────────────


def get_scraper_sha(paths: Paths, logger: logging.Logger) -> str | None:
    """Get the current HEAD SHA of the scraper repo."""
    try:
        result = subprocess.run(
            ["git", "-C", str(paths.scraper_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        logger.error(f"Failed to get scraper SHA: {e}")
    return None


def update_scraper(paths: Paths, logger: logging.Logger) -> bool:
    """Fetch + pull the scraper repo. Returns True if updated.

    A plain pull --ff-only suffices — the pipeline no longer writes to
    all_jobs.json (enrichments go to the sidecar).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(paths.scraper_dir), "fetch", "origin"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.error(f"git fetch failed: {result.stderr}")
            return False

        result = subprocess.run(
            ["git", "-C", str(paths.scraper_dir), "status", "-sb"],
            capture_output=True, text=True, timeout=10,
        )
        if "behind" not in result.stdout:
            logger.debug("Scraper is up to date.")
            return False

        logger.info("Scraper is behind origin. Pulling...")

        result = subprocess.run(
            ["git", "-C", str(paths.scraper_dir), "pull", "--ff-only"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.error(f"git pull failed: {result.stderr}")
            return False

        logger.info("Scraper updated.")
        return True
    except Exception as e:
        logger.error(f"Failed to update scraper: {e}")
        return False


def check_scraper_updates(
    state: dict, paths: Paths, logger: logging.Logger
) -> bool:
    """Check if the scraper has new commits since last run.

    Returns True if there are new jobs to process, False if unchanged.
    """
    current_sha = get_scraper_sha(paths, logger)
    if current_sha is None:
        logger.error("Could not get scraper SHA. Proceeding anyway.")
        return True

    last_sha = state.get("last_scraper_sha")
    if last_sha == current_sha:
        logger.info(f"Scraper unchanged (SHA {current_sha[:8]}). No new jobs.")
        return False

    logger.info(
        f"Scraper changed: {last_sha[:8] if last_sha else 'none'} → {current_sha[:8]}"
    )
    return True


# ─── Git commit + push ─────────────────────────────────────────────────────


def git_commit_and_push(
    stats: dict, paths: Paths, logger: logging.Logger, git_push: bool
) -> None:
    """Commit pipeline output and push to origin.

    Only stages files within pipeline output directories (listings/,
    trash/, rejected/, drafts/, ready/), .grades.log, and
    .devin/pipeline-url-index.json. Never does `git add -A` on the whole
    repo — only specific changed files within those directories.
    """
    if not git_push:
        logger.info("git_push disabled in config. Skipping commit.")
        return

    pipeline_dirs = ["listings/", "trash/", "rejected/", "drafts/", "ready/"]
    pipeline_files = [".grades.log", ".devin/pipeline-url-index.json"]
    hunter = str(paths.hunter_dir)

    try:
        # Find changed files within pipeline dirs only (vs HEAD)
        result = subprocess.run(
            ["git", "-C", hunter, "diff", "--name-only", "-z", "HEAD", "--"]
            + pipeline_dirs,
            capture_output=True, text=True, timeout=10,
        )
        changed = [f for f in result.stdout.split("\0") if f]

        # Untracked files in pipeline dirs
        result = subprocess.run(
            ["git", "-C", hunter, "ls-files", "--others", "--exclude-standard", "-z", "--"]
            + pipeline_dirs,
            capture_output=True, text=True, timeout=10,
        )
        untracked = [f for f in result.stdout.split("\0") if f]

        # Check pipeline files for changes
        extra_files = []
        for pf in pipeline_files:
            pf_path = paths.hunter_dir / pf
            if not pf_path.exists():
                continue
            result = subprocess.run(
                ["git", "-C", hunter, "diff", "--name-only", "HEAD", "--", pf],
                capture_output=True, text=True, timeout=10,
            )
            if result.stdout.strip():
                extra_files.append(pf)
                continue
            result = subprocess.run(
                ["git", "-C", hunter, "ls-files", "--others", "--exclude-standard", "--", pf],
                capture_output=True, text=True, timeout=10,
            )
            if result.stdout.strip():
                extra_files.append(pf)

        all_files = changed + untracked + extra_files

        if not all_files:
            logger.info("No pipeline changes to commit.")
            return

        # Stage each changed file individually
        for f in all_files:
            subprocess.run(
                ["git", "-C", hunter, "add", "-A", "--", f],
                capture_output=True, text=True, timeout=10,
            )

        logger.info(f"Staged {len(all_files)} pipeline files.")

        processed = stats.get("processed", 0)
        ready = stats.get("ready", 0)
        message = (
            f"pipeline: processed {processed} jobs, {ready} ready\n\n"
            "Generated with [Devin](https://devin.ai)"
        )
        result = subprocess.run(
            ["git", "-C", hunter, "commit", "-m", message],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            logger.error(f"git commit failed: {result.stderr}")
            return
        logger.info(f"Committed: {message.splitlines()[0]}")

        # Pull --rebase before pushing
        result = subprocess.run(
            ["git", "-C", hunter, "pull", "--rebase"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.error(f"git pull --rebase failed: {result.stderr}")
            return
        if result.stdout.strip():
            logger.info("Rebased on origin before pushing.")

        # Push
        result = subprocess.run(
            ["git", "-C", hunter, "push"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.error(f"git push failed: {result.stderr}")
        else:
            logger.info("Pushed to origin.")
    except Exception as e:
        logger.error(f"git commit/push failed: {e}")


# ─── Cleanup ───────────────────────────────────────────────────────────────


def cleanup_transient_dirs(paths: Paths, logger: logging.Logger) -> None:
    """Clean up transient directories (.grading/, .veracity/).

    These are gitignored and rebuilt each run. The grader and truthfulness
    reviewer write JSON here during their turns; we clean up after.
    """
    for d in (paths.grading, paths.veracity):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            logger.debug(f"Cleaned up {d}")
