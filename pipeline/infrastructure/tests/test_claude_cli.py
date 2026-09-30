"""Tests for the non-interactive Claude Code CLI adapter."""
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from pipeline.infrastructure.claude_cli import call_llm, call_llm_safe
from pipeline.infrastructure.devin_cli import LLMError


def _success(stdout="finished"):
    result = MagicMock()
    result.returncode = 0
    result.stdout = stdout
    result.stderr = ""
    return result


def test_call_llm_builds_unattended_customizer_command():
    with patch(
        "pipeline.infrastructure.claude_cli.subprocess.run",
        return_value=_success(),
    ) as mock_run, patch(
        "pipeline.infrastructure.claude_cli.shutil.which",
        return_value="/usr/local/bin/claude",
    ):
        assert call_llm("customize this", model="sonnet") == "finished"

    cmd = mock_run.call_args.args[0]
    assert cmd[0] == "/usr/local/bin/claude"
    assert "--print" in cmd
    assert "--no-session-persistence" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert "Edit(/stages/2_drafts/**)" in cmd
    assert "Write(/stages/2_drafts/**)" in cmd
    assert "Bash(python3 -m pipeline.helpers.count_lines *)" in cmd
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert mock_run.call_args.kwargs["input"] == "customize this"


def test_call_llm_normal_mode_exposes_only_read_tools():
    with patch(
        "pipeline.infrastructure.claude_cli.subprocess.run",
        return_value=_success(),
    ) as mock_run, patch(
        "pipeline.infrastructure.claude_cli.shutil.which",
        return_value="/usr/local/bin/claude",
    ):
        call_llm("grade this", model="default", permission_mode="normal")

    cmd = mock_run.call_args.args[0]
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert cmd[cmd.index("--tools") + 1] == "Read,Glob,Grep"
    assert "--model" not in cmd
    assert "--dangerously-skip-permissions" not in cmd


def test_call_llm_exports_text_response(tmp_path):
    export_path = tmp_path / "claude-output.txt"
    with patch(
        "pipeline.infrastructure.claude_cli.subprocess.run",
        return_value=_success("final assistant message\n"),
    ), patch(
        "pipeline.infrastructure.claude_cli.shutil.which",
        return_value="/usr/local/bin/claude",
    ):
        output = call_llm("test", export_path=export_path)

    assert output == "final assistant message"
    assert export_path.read_text(encoding="utf-8") == "final assistant message\n"


def test_call_llm_rejects_unknown_permission_mode():
    with patch(
        "pipeline.infrastructure.claude_cli.shutil.which",
        return_value="/usr/local/bin/claude",
    ), pytest.raises(LLMError, match="unsupported permission mode"):
        call_llm("test", permission_mode="other")


def test_call_llm_safe_returns_timeout_error():
    with patch(
        "pipeline.infrastructure.claude_cli.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=1),
    ), patch(
        "pipeline.infrastructure.claude_cli.shutil.which",
        return_value="/usr/local/bin/claude",
    ):
        output, error = call_llm_safe("test", timeout=1, retries=0)

    assert output is None
    assert isinstance(error, LLMError)
    assert "timed out" in str(error)
