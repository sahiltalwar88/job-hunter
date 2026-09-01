"""Tests for pipeline.lifecycle."""
import os

from pipeline.infrastructure.lifecycle import (
    acquire_lock,
    check_scraper_updates,
    cleanup_transient_dirs,
    git_commit_and_push,
    load_state,
    release_lock,
    save_state,
    setup_logging,
)


def test_setup_logging_creates_logs_dir(tmp_paths):
    setup_logging(tmp_paths)
    assert tmp_paths.logs.exists()


def test_lock_acquire_and_release(tmp_paths, logger):
    assert acquire_lock(tmp_paths, logger) is True
    assert tmp_paths.lock_file.exists()
    # Second acquire fails (our PID is alive)
    assert acquire_lock(tmp_paths, logger) is False
    release_lock(tmp_paths, logger)
    assert not tmp_paths.lock_file.exists()


def test_stale_lock_removed(tmp_paths, logger):
    tmp_paths.devin_dir.mkdir(parents=True)
    tmp_paths.lock_file.write_text("999999")  # dead PID
    assert acquire_lock(tmp_paths, logger) is True
    assert tmp_paths.lock_file.read_text() == str(os.getpid())
    release_lock(tmp_paths, logger)


def test_corrupt_lock_removed(tmp_paths, logger):
    tmp_paths.devin_dir.mkdir(parents=True)
    tmp_paths.lock_file.write_text("not-a-pid")
    assert acquire_lock(tmp_paths, logger) is True
    release_lock(tmp_paths, logger)


def test_state_load_default(tmp_paths):
    state = load_state(tmp_paths)
    assert state["last_scraper_sha"] is None


def test_state_save_and_reload(tmp_paths, logger):
    state = {"last_scraper_sha": "abc123", "last_run": "2026-01-01T00:00:00Z"}
    save_state(state, tmp_paths, logger)
    state2 = load_state(tmp_paths)
    assert state2["last_scraper_sha"] == "abc123"
    assert state2["last_run"] == "2026-01-01T00:00:00Z"


def test_cleanup_transient_dirs(tmp_paths, logger):
    tmp_paths.grading.mkdir(parents=True)
    (tmp_paths.grading / "test.json").write_text("{}")
    tmp_paths.veracity.mkdir(parents=True)
    (tmp_paths.veracity / "test.json").write_text("{}")
    cleanup_transient_dirs(tmp_paths, logger)
    assert not tmp_paths.grading.exists()
    assert not tmp_paths.veracity.exists()


def test_git_commit_push_disabled(tmp_paths, logger):
    """git_push=False should be a no-op."""
    git_commit_and_push({"processed": 0, "ready": 0}, tmp_paths, logger, git_push=False)


def test_check_scraper_updates_no_scraper(tmp_paths, logger):
    """No scraper dir → get_scraper_sha returns None → proceed anyway."""
    state = {"last_scraper_sha": None}
    assert check_scraper_updates(state, tmp_paths, logger) is True
