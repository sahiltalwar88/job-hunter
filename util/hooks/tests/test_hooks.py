"""Tests for pipeline hooks (score tamper prevention, failing grade blocking)."""
import json
import os
import subprocess
import sys
import tempfile

import pytest

HOOKS_DIR = os.path.join(os.path.dirname(__file__), "..")


def run_hook(script_name, payload):
    """Run a hook script with the given stdin payload, return (exit_code, stdout_json)."""
    script = os.path.join(HOOKS_DIR, script_name)
    proc = subprocess.run(
        [sys.executable, script],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    output = None
    if proc.stdout.strip():
        try:
            output = json.loads(proc.stdout)
        except json.JSONDecodeError:
            pass
    return proc.returncode, output


# --- block_score_tamper.py tests ---


class TestBlockScoreTamper:
    def test_allows_normal_exec(self):
        rc, out = run_hook("block_score_tamper.py", {
            "tool_name": "exec",
            "tool_input": {"command": "ls -la"},
        })
        assert rc == 0

    def test_allows_tbd_to_score_rename(self):
        """Renaming [TBD] resume-v1.md to [8.5] resume-v1.md is allowed (grader's job)."""
        rc, out = run_hook("block_score_tamper.py", {
            "tool_name": "exec",
            "tool_input": {"command": "mv '[TBD] resume-v1.md' '[8.5] resume-v1.md'"},
        })
        assert rc == 0

    def test_blocks_score_to_different_score_rename(self):
        """Renaming [7.0] resume-v1.md to [9.0] resume-v1.md is blocked."""
        rc, out = run_hook("block_score_tamper.py", {
            "tool_name": "exec",
            "tool_input": {"command": "mv '[7.0] resume-v1.md' '[9.0] resume-v1.md'"},
        })
        assert rc == 2
        assert out["decision"] == "block"
        assert "7.0" in out["reason"]
        assert "9.0" in out["reason"]

    def test_allows_non_resume_rename(self):
        rc, out = run_hook("block_score_tamper.py", {
            "tool_name": "exec",
            "tool_input": {"command": "mv '[TBD] job-description.md' '[8.0] job-description.md'"},
        })
        assert rc == 0

    def test_blocks_write_to_new_scored_resume(self, tmp_path):
        """Writing a new file with a score prefix is blocked — only the grader creates scored files via rename."""
        rc, out = run_hook("block_score_tamper.py", {
            "tool_name": "write",
            "tool_input": {"file_path": str(tmp_path / "[9.0] resume-v1.md")},
        })
        assert rc == 2
        assert out["decision"] == "block"
        assert "9.0" in out["reason"]

    def test_blocks_overwrite_of_existing_scored_resume(self, tmp_path):
        """Overwriting an existing [score] resume file is blocked."""
        resume = tmp_path / "[8.5] resume-v1.md"
        resume.write_text("existing content")
        rc, out = run_hook("block_score_tamper.py", {
            "tool_name": "edit",
            "tool_input": {"file_path": str(resume)},
        })
        assert rc == 2
        assert out["decision"] == "block"
        assert "8.5" in out["reason"]

    def test_allows_write_to_ungraded_resume(self, tmp_path):
        """Writing to a [TBD] resume is allowed (not yet graded)."""
        resume = tmp_path / "[TBD] resume-v1.md"
        resume.write_text("existing content")
        rc, out = run_hook("block_score_tamper.py", {
            "tool_name": "edit",
            "tool_input": {"file_path": str(resume)},
        })
        assert rc == 0

    def test_malformed_input_allows(self):
        rc, out = run_hook("block_score_tamper.py", {"unexpected": "data"})
        assert rc == 0


# --- block_failing_grade.py tests ---


class TestBlockFailingGrade:
    def test_blocks_stop_with_ungraded_resume(self, tmp_path, monkeypatch):
        """Stop is blocked when a [TBD] resume exists in drafts/."""
        drafts = tmp_path / "stages" / "2_drafts" / "some-company"
        drafts.mkdir(parents=True)
        (drafts / "[TBD] resume-v1.md").write_text("draft")

        monkeypatch.chdir(tmp_path)
        rc, out = run_hook("block_failing_grade.py", {"stop_hook_active": False})
        assert rc == 2
        assert out["decision"] == "block"
        assert "ungraded" in out["reason"].lower()

    def test_blocks_stop_with_low_score_resume(self, tmp_path, monkeypatch):
        """Stop is blocked when a resume with score < 9 exists in drafts/."""
        drafts = tmp_path / "stages" / "2_drafts" / "some-company"
        drafts.mkdir(parents=True)
        (drafts / "[7.5] resume-v1.md").write_text("draft")

        monkeypatch.chdir(tmp_path)
        rc, out = run_hook("block_failing_grade.py", {"stop_hook_active": False})
        assert rc == 2
        assert out["decision"] == "block"
        assert "7.5" in out["reason"]

    def test_allows_stop_with_passing_resume(self, tmp_path, monkeypatch):
        """Stop is allowed when all resumes in drafts/ have score >= 9."""
        drafts = tmp_path / "stages" / "2_drafts" / "some-company"
        drafts.mkdir(parents=True)
        (drafts / "[9.0] resume-v1.md").write_text("draft")

        monkeypatch.chdir(tmp_path)
        rc, out = run_hook("block_failing_grade.py", {"stop_hook_active": False})
        assert rc == 0

    def test_allows_stop_with_no_drafts(self, tmp_path, monkeypatch):
        """Stop is allowed when drafts/ doesn't exist."""
        monkeypatch.chdir(tmp_path)
        rc, out = run_hook("block_failing_grade.py", {"stop_hook_active": False})
        assert rc == 0

    def test_allows_stop_with_empty_drafts(self, tmp_path, monkeypatch):
        """Stop is allowed when drafts/ is empty."""
        drafts = tmp_path / "stages" / "2_drafts"
        drafts.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        rc, out = run_hook("block_failing_grade.py", {"stop_hook_active": False})
        assert rc == 0

    def test_ignores_non_resume_files(self, tmp_path, monkeypatch):
        """Non-resume .md files in drafts/ are ignored."""
        drafts = tmp_path / "stages" / "2_drafts" / "some-company"
        drafts.mkdir(parents=True)
        (drafts / "notes.md").write_text("notes")
        (drafts / "[TBD] something-else.md").write_text("other")

        monkeypatch.chdir(tmp_path)
        rc, out = run_hook("block_failing_grade.py", {"stop_hook_active": False})
        assert rc == 0

    def test_malformed_input_allows(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rc, out = run_hook("block_failing_grade.py", {"unexpected": "data"})
        assert rc == 0


# --- session_start.py tests ---


class TestSessionStart:
    def test_injects_context(self):
        """SessionStart hook should inject debug workflow context."""
        rc, out = run_hook("session_start.py", {"source": "interactive"})
        assert rc == 0
        assert out is not None
        assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        context = out["hookSpecificOutput"]["additionalContext"]
        assert "Debug Workflow" in context
        assert "resume-grader" in context
        assert "truthfulness-reviewer" in context
        assert "base-resume" in context

    def test_context_mentions_integrity_rules(self):
        """The injected context should mention the integrity rules."""
        rc, out = run_hook("session_start.py", {"source": "interactive"})
        assert rc == 0
        context = out["hookSpecificOutput"]["additionalContext"]
        assert "Never rename a [score] file" in context
        assert "Truthfulness is non-negotiable" in context

    def test_malformed_input_still_injects(self):
        """Even with malformed input, the hook should inject context."""
        rc, out = run_hook("session_start.py", {"unexpected": "data"})
        assert rc == 0
        assert out is not None
        assert "additionalContext" in out["hookSpecificOutput"]


# --- audit_log.py tests ---


class TestAuditLog:
    def test_logs_resume_write(self, tmp_path, monkeypatch):
        """Writing a resume file should be logged to .grades.log."""
        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(tmp_path))
        rc, out = run_hook("audit_log.py", {
            "tool_name": "write",
            "tool_input": {"file_path": "drafts/test-co/[TBD] resume-v1.md"},
            "tool_response": {"success": True},
        })
        assert rc == 0
        log = (tmp_path / ".grades.log").read_text()
        assert "resume-v1.md" in log
        assert "wrote" in log

    def test_logs_jd_write(self, tmp_path, monkeypatch):
        """Writing a JD file should be logged."""
        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(tmp_path))
        rc, out = run_hook("audit_log.py", {
            "tool_name": "write",
            "tool_input": {"file_path": "listings/test-co/[TBD] job-description.md"},
            "tool_response": {"success": True},
        })
        assert rc == 0
        log = (tmp_path / ".grades.log").read_text()
        assert "job-description.md" in log

    def test_ignores_non_pipeline_files(self, tmp_path, monkeypatch):
        """Non-pipeline files should not be logged."""
        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(tmp_path))
        rc, out = run_hook("audit_log.py", {
            "tool_name": "write",
            "tool_input": {"file_path": "pipeline/__main__.py"},
            "tool_response": {"success": True},
        })
        assert rc == 0
        assert not (tmp_path / ".grades.log").exists()

    def test_ignores_read_tools(self, tmp_path, monkeypatch):
        """Read tools should not trigger logging."""
        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(tmp_path))
        rc, out = run_hook("audit_log.py", {
            "tool_name": "read",
            "tool_input": {"file_path": "drafts/test-co/[9.0] resume-v1.md"},
            "tool_response": {"success": True},
        })
        assert rc == 0
        assert not (tmp_path / ".grades.log").exists()

    def test_logs_failed_write(self, tmp_path, monkeypatch):
        """Failed writes should be logged with 'failed-write' action."""
        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(tmp_path))
        rc, out = run_hook("audit_log.py", {
            "tool_name": "write",
            "tool_input": {"file_path": "drafts/test-co/[TBD] resume-v1.md"},
            "tool_response": {"success": False, "error": "permission denied"},
        })
        assert rc == 0
        log = (tmp_path / ".grades.log").read_text()
        assert "failed-write" in log

    def test_logs_grade_json(self, tmp_path, monkeypatch):
        """Writing grade JSON should be logged."""
        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(tmp_path))
        rc, out = run_hook("audit_log.py", {
            "tool_name": "write",
            "tool_input": {"file_path": ".grading/test-co/grade-v1.json"},
            "tool_response": {"success": True},
        })
        assert rc == 0
        log = (tmp_path / ".grades.log").read_text()
        assert "grade-v1.json" in log

    def test_malformed_input_does_not_crash(self, tmp_path, monkeypatch):
        """Malformed input should not crash the hook."""
        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(tmp_path))
        rc, out = run_hook("audit_log.py", {"unexpected": "data"})
        assert rc == 0

    def test_append_mode(self, tmp_path, monkeypatch):
        """Multiple writes should append, not overwrite."""
        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(tmp_path))
        # First write
        run_hook("audit_log.py", {
            "tool_name": "write",
            "tool_input": {"file_path": "drafts/test-co/[TBD] resume-v1.md"},
            "tool_response": {"success": True},
        })
        # Second write
        run_hook("audit_log.py", {
            "tool_name": "write",
            "tool_input": {"file_path": "drafts/test-co/[TBD] resume-v2.md"},
            "tool_response": {"success": True},
        })
        log = (tmp_path / ".grades.log").read_text()
        lines = log.strip().split("\n")
        assert len(lines) == 2
        assert "resume-v1.md" in lines[0]
        assert "resume-v2.md" in lines[1]


# --- Pipeline mode bypass tests ---


class TestPipelineModeBypass:
    """Tests that hooks bypass correctly when DEVIN_PIPELINE_MODE=1 is set."""

    def test_stop_hook_bypasses_in_pipeline_mode(self, tmp_path, monkeypatch):
        """Stop hook exits 0 in pipeline mode even with ungraded resumes."""
        drafts = tmp_path / "stages" / "2_drafts" / "some-company"
        drafts.mkdir(parents=True)
        (drafts / "[TBD] resume-v1.md").write_text("draft")

        monkeypatch.setenv("DEVIN_PIPELINE_MODE", "1")
        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(tmp_path))
        rc, out = run_hook("block_failing_grade.py", {"stop_hook_active": False})
        assert rc == 0

    def test_session_start_bypasses_in_pipeline_mode(self, monkeypatch):
        """SessionStart hook exits 0 in pipeline mode without injecting context."""
        monkeypatch.setenv("DEVIN_PIPELINE_MODE", "1")
        rc, out = run_hook("session_start.py", {"source": "startup"})
        assert rc == 0
        assert out is None  # no JSON output — just exits

    def test_score_tamper_still_runs_in_pipeline_mode(self, tmp_path, monkeypatch):
        """Score tamper hook still blocks in pipeline mode — only Stop/SessionStart bypass."""
        monkeypatch.setenv("DEVIN_PIPELINE_MODE", "1")
        rc, out = run_hook("block_score_tamper.py", {
            "tool_name": "write",
            "tool_input": {"file_path": str(tmp_path / "[9.0] resume-v1.md")},
        })
        assert rc == 2
        assert out["decision"] == "block"


# --- Session-scoped Stop hook tests ---


class TestSessionScopedStop:
    """Tests that the Stop hook only checks slugs the session touched."""

    def test_blocks_only_touched_slugs(self, tmp_path, monkeypatch):
        """Stop hook blocks on slugs touched by this session, not stale ones."""
        project = tmp_path
        drafts = project / "stages" / "2_drafts"
        drafts.mkdir(parents=True)

        # Stale resume from a previous session (should NOT block)
        stale = drafts / "stale-company"
        stale.mkdir()
        (stale / "[TBD] resume-v1.md").write_text("stale")

        # Resume this session created (SHOULD block)
        fresh = drafts / "fresh-company"
        fresh.mkdir()
        (fresh / "[TBD] resume-v1.md").write_text("fresh")

        # Write session state: stale was in initial snapshot, fresh was not
        state_dir = project / ".devin" / "session-state"
        state_dir.mkdir(parents=True)
        state = {
            "session_id": "test-session",
            "initial_slugs": {"stale-company": {"[TBD] resume-v1.md": "ungraded"}},
            "touched_slugs": ["fresh-company"],
        }
        (state_dir / "test-session.json").write_text(json.dumps(state))

        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(project))
        rc, out = run_hook("block_failing_grade.py", {
            "stop_hook_active": False,
            "session_id": "test-session",
        })
        assert rc == 2
        assert out["decision"] == "block"
        assert "fresh-company" in out["reason"]
        assert "stale-company" not in out["reason"]

    def test_allows_stop_when_stale_resume_not_touched(self, tmp_path, monkeypatch):
        """Stop hook allows stopping when only stale (untouched) resumes exist."""
        project = tmp_path
        drafts = project / "stages" / "2_drafts"
        drafts.mkdir(parents=True)

        stale = drafts / "stale-company"
        stale.mkdir()
        (stale / "[TBD] resume-v1.md").write_text("stale")

        state_dir = project / ".devin" / "session-state"
        state_dir.mkdir(parents=True)
        state = {
            "session_id": "test-session",
            "initial_slugs": {"stale-company": {"[TBD] resume-v1.md": "ungraded"}},
            "touched_slugs": [],
        }
        (state_dir / "test-session.json").write_text(json.dumps(state))

        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(project))
        rc, out = run_hook("block_failing_grade.py", {
            "stop_hook_active": False,
            "session_id": "test-session",
        })
        assert rc == 0

    def test_blocks_new_slug_not_in_initial_snapshot(self, tmp_path, monkeypatch):
        """Stop hook blocks on slugs created after session start (not in initial snapshot)."""
        project = tmp_path
        drafts = project / "stages" / "2_drafts"
        drafts.mkdir(parents=True)

        new_slug = drafts / "new-company"
        new_slug.mkdir()
        (new_slug / "[TBD] resume-v1.md").write_text("new")

        state_dir = project / ".devin" / "session-state"
        state_dir.mkdir(parents=True)
        state = {
            "session_id": "test-session",
            "initial_slugs": {},  # nothing at session start
            "touched_slugs": [],
        }
        (state_dir / "test-session.json").write_text(json.dumps(state))

        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(project))
        rc, out = run_hook("block_failing_grade.py", {
            "stop_hook_active": False,
            "session_id": "test-session",
        })
        assert rc == 2
        assert "new-company" in out["reason"]

    def test_falls_back_to_check_all_without_state(self, tmp_path, monkeypatch):
        """Without session state, Stop hook checks all of drafts/ (backward compat)."""
        project = tmp_path
        drafts = project / "stages" / "2_drafts" / "some-company"
        drafts.mkdir(parents=True)
        (drafts / "[TBD] resume-v1.md").write_text("draft")

        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(project))
        rc, out = run_hook("block_failing_grade.py", {
            "stop_hook_active": False,
            "session_id": "no-state-session",
        })
        assert rc == 2
        assert "some-company" in out["reason"]


# --- SessionStart state file tests ---


class TestSessionStateFile:
    """Tests that SessionStart writes a per-session state file."""

    def test_writes_session_state_file(self, tmp_path, monkeypatch):
        """SessionStart hook writes a state file with initial drafts/ snapshot."""
        project = tmp_path
        drafts = project / "stages" / "2_drafts" / "existing-company"
        drafts.mkdir(parents=True)
        (drafts / "[TBD] resume-v1.md").write_text("existing")

        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(project))
        rc, out = run_hook("session_start.py", {
            "source": "interactive",
            "session_id": "test-session-123",
        })
        assert rc == 0

        state_file = project / ".devin" / "session-state" / "test-session-123.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert state["session_id"] == "test-session-123"
        assert "existing-company" in state["initial_slugs"]
        assert state["touched_slugs"] == []

    def test_no_state_file_in_pipeline_mode(self, tmp_path, monkeypatch):
        """SessionStart hook does not write a state file in pipeline mode."""
        monkeypatch.setenv("DEVIN_PIPELINE_MODE", "1")
        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(tmp_path))
        run_hook("session_start.py", {"source": "startup", "session_id": "test"})
        state_file = tmp_path / ".devin" / "session-state" / "test.json"
        assert not state_file.exists()


# --- restrict_exec.py tests ---


class TestRestrictExec:
    """Tests for the universal exec whitelist hook (pipeline mode only)."""

    # All tests in this class run in pipeline mode (DEVIN_PIPELINE_MODE=1).
    # Debug mode is tested separately at the bottom.

    @pytest.fixture(autouse=True)
    def _pipeline_mode(self, monkeypatch):
        monkeypatch.setenv("DEVIN_PIPELINE_MODE", "1")

    # --- debug mode bypass ---

    def test_debug_mode_allows_everything(self, monkeypatch):
        """In debug mode (no DEVIN_PIPELINE_MODE), everything is allowed."""
        monkeypatch.delenv("DEVIN_PIPELINE_MODE", raising=False)
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "rm -rf /"},
        })
        assert rc == 0

    # --- tier 1: pipeline commands (allowed) ---

    def test_allows_count_lines_no_args(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "python3 -m pipeline.helpers.count_lines"},
        })
        assert rc == 0

    def test_allows_count_lines_with_path(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "python3 -m pipeline.helpers.count_lines stages/2_drafts/foo/[TBD] resume-v1.md"},
        })
        assert rc == 0

    def test_allows_count_lines_with_json_flag(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "python3 -m pipeline.helpers.count_lines stages/2_drafts/foo/resume.md --json"},
        })
        assert rc == 0

    def test_allows_count_lines_with_wrap_chars(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "python3 -m pipeline.helpers.count_lines resume.md --wrap-chars 100"},
        })
        assert rc == 0

    def test_allows_pytest(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "python3 -m pytest tests/ -x -q"},
        })
        assert rc == 0

    def test_allows_pytest_no_args(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "python3 -m pytest"},
        })
        assert rc == 0

    def test_allows_mv_rename(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "mv '[TBD] resume-v1.md' '[9.0] resume-v1.md'"},
        })
        assert rc == 0

    def test_allows_mv_any_args(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "mv foo bar baz"},
        })
        assert rc == 0

    def test_allows_echo_to_grades_log(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": 'echo "2025-08-24 grade=9.0 slug=foo" >> .grades.log'},
        })
        assert rc == 0

    # --- tier 2: general commands (allowed) ---

    def test_allows_git_status(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "git status"},
        })
        assert rc == 0

    def test_allows_git_diff(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "git diff --stat"},
        })
        assert rc == 0

    def test_allows_git_log(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "git log --oneline -10"},
        })
        assert rc == 0

    def test_allows_git_commit(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "git commit -m 'test message'"},
        })
        assert rc == 0

    def test_allows_gh_pr_create(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "gh pr create --title test --body test"},
        })
        assert rc == 0

    def test_allows_ls(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "ls -la"},
        })
        assert rc == 0

    def test_allows_cd(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "cd /tmp"},
        })
        assert rc == 0

    def test_allows_cat(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "cat .grades.log"},
        })
        assert rc == 0

    def test_allows_head(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "head -20 file.txt"},
        })
        assert rc == 0

    def test_allows_grep(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "grep -r pattern dir/"},
        })
        assert rc == 0

    def test_allows_find(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "find . -name '*.py'"},
        })
        assert rc == 0

    def test_allows_pwd(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "pwd"},
        })
        assert rc == 0

    def test_allows_diff(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "diff file1 file2"},
        })
        assert rc == 0

    def test_allows_wc(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "wc -l file.txt"},
        })
        assert rc == 0

    def test_allows_echo_without_redirect(self):
        """echo to stdout is allowed (no redirect)."""
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": 'echo "hello"'},
        })
        assert rc == 0

    def test_allows_which(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "which python3"},
        })
        assert rc == 0

    def test_allows_file(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "file resume.md"},
        })
        assert rc == 0

    # --- chaining: allowed when all sub-commands are whitelisted ---

    def test_allows_git_diff_pipe_head(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "git diff | head -20"},
        })
        assert rc == 0

    def test_allows_git_log_pipe_head(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "git log --oneline | head -5"},
        })
        assert rc == 0

    def test_allows_cd_and_ls(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "cd /tmp && ls -la"},
        })
        assert rc == 0

    def test_allows_cat_pipe_grep(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "cat foo | grep bar"},
        })
        assert rc == 0

    def test_allows_count_lines_pipe_head(self):
        """Tier 1 command piped to tier 2 is allowed."""
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "python3 -m pipeline.helpers.count_lines foo.md | head"},
        })
        assert rc == 0

    def test_allows_echo_grades_pipe_cat(self):
        """echo >> .grades.log piped to cat is allowed (both whitelisted)."""
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": 'echo "x" >> .grades.log | cat'},
        })
        assert rc == 0

    # --- blocked commands (not on whitelist) ---

    def test_blocks_python_inline(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": 'python3 -c "print(1)"'},
        })
        assert rc == 2
        assert out["decision"] == "block"

    def test_blocks_other_python_script(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "python3 -m pipeline"},
        })
        assert rc == 2
        assert out["decision"] == "block"

    def test_blocks_serve(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "python3 -m pipeline.helpers.serve"},
        })
        assert rc == 2
        assert out["decision"] == "block"

    def test_blocks_rm(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "rm stages/2_drafts/foo/resume.md"},
        })
        assert rc == 2
        assert out["decision"] == "block"

    def test_blocks_mkdir(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "mkdir -p drafts/foo"},
        })
        assert rc == 2
        assert out["decision"] == "block"

    def test_blocks_sed(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "sed -i 's/foo/bar/' file.txt"},
        })
        assert rc == 2
        assert out["decision"] == "block"

    def test_allows_cp(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "cp IDENTITY.md CLAUDE.md"},
        })
        assert rc == 0

    def test_blocks_curl(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "curl http://example.com"},
        })
        assert rc == 2
        assert out["decision"] == "block"

    def test_blocks_echo_to_other_file(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": 'echo "hello" >> other.log'},
        })
        assert rc == 2
        assert out["decision"] == "block"

    def test_blocks_echo_single_redirect_to_grades_log(self):
        """Single `>` redirect is blocked even to .grades.log (would truncate)."""
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": 'echo "hello" > .grades.log'},
        })
        assert rc == 2
        assert out["decision"] == "block"

    # --- blocked: chaining with non-whitelisted sub-command ---

    def test_blocks_git_and_rm(self):
        """git && rm is blocked because rm is not whitelisted."""
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "git status && rm foo"},
        })
        assert rc == 2
        assert out["decision"] == "block"

    def test_blocks_mv_semicolon_rm(self):
        """mv; rm is blocked because rm is not whitelisted."""
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "mv a b; rm a"},
        })
        assert rc == 2
        assert out["decision"] == "block"

    def test_blocks_git_pipe_rm(self):
        """git | rm is blocked because rm is not whitelisted."""
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "git log | rm foo"},
        })
        assert rc == 2
        assert out["decision"] == "block"

    # --- blocked: command substitution and redirects ---

    def test_blocks_command_substitution(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "mv $(ls) /tmp"},
        })
        assert rc == 2
        assert out["decision"] == "block"

    def test_blocks_backticks(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "mv `ls` /tmp"},
        })
        assert rc == 2
        assert out["decision"] == "block"

    def test_blocks_command_substitution_in_git(self):
        """$() is blocked even in a whitelisted command."""
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "git commit -m $(echo hi)"},
        })
        assert rc == 2
        assert out["decision"] == "block"

    def test_blocks_single_redirect(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "python3 -m pipeline.helpers.count_lines foo.md > out.txt"},
        })
        assert rc == 2
        assert out["decision"] == "block"

    def test_blocks_single_redirect_in_git(self):
        """Single > is blocked even in a whitelisted command."""
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": "git log > out.txt"},
        })
        assert rc == 2
        assert out["decision"] == "block"

    # --- non-exec tools and edge cases ---

    def test_ignores_non_exec_tools(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "write",
            "tool_input": {"file_path": "stages/2_drafts/foo/resume.md"},
        })
        assert rc == 0

    def test_empty_command_allows(self):
        rc, out = run_hook("restrict_exec.py", {
            "tool_name": "exec",
            "tool_input": {"command": ""},
        })
        assert rc == 0

    def test_malformed_input_allows(self):
        rc, out = run_hook("restrict_exec.py", {"unexpected": "data"})
        assert rc == 0
