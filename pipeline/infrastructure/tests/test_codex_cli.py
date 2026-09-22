"""Tests for the non-interactive Codex CLI adapter."""
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from pipeline.infrastructure.codex_cli import call_llm, call_llm_safe
from pipeline.infrastructure.devin_cli import LLMError


def _success(stdout="finished"):
    result = MagicMock()
    result.returncode = 0
    result.stdout = stdout
    result.stderr = ""
    return result


def test_call_llm_builds_unattended_workspace_write_command():
    with patch(
        "pipeline.infrastructure.codex_cli.subprocess.run",
        return_value=_success(),
    ) as mock_run, patch(
        "pipeline.infrastructure.codex_cli.shutil.which",
        return_value="/usr/local/bin/codex",
    ):
        assert call_llm("customize this", model="gpt-test") == "finished"

    cmd = mock_run.call_args.args[0]
    assert cmd[:4] == [
        "/usr/local/bin/codex",
        "--ask-for-approval",
        "never",
        "exec",
    ]
    assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"
    assert cmd[cmd.index("--model") + 1] == "gpt-test"
    assert cmd[-1] == "-"
    assert mock_run.call_args.kwargs["input"] == "customize this"


def test_call_llm_normal_mode_is_read_only_and_default_model_is_omitted():
    with patch(
        "pipeline.infrastructure.codex_cli.subprocess.run",
        return_value=_success(),
    ) as mock_run, patch(
        "pipeline.infrastructure.codex_cli.shutil.which",
        return_value="/usr/local/bin/codex",
    ):
        call_llm("grade this", model="default", permission_mode="normal")

    cmd = mock_run.call_args.args[0]
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert "--model" not in cmd


def test_call_llm_prefers_output_last_message_file(tmp_path):
    export_path = tmp_path / "last-message.txt"

    def run_and_write(cmd, **kwargs):
        export_path.write_text("final assistant message\n", encoding="utf-8")
        return _success(stdout="progress output")

    with patch(
        "pipeline.infrastructure.codex_cli.subprocess.run",
        side_effect=run_and_write,
    ), patch(
        "pipeline.infrastructure.codex_cli.shutil.which",
        return_value="/usr/local/bin/codex",
    ):
        output = call_llm("test", export_path=export_path)

    assert output == "final assistant message"


def test_call_llm_rejects_unknown_permission_mode():
    with patch(
        "pipeline.infrastructure.codex_cli.shutil.which",
        return_value="/usr/local/bin/codex",
    ), pytest.raises(LLMError, match="unsupported permission mode"):
        call_llm("test", permission_mode="other")


def test_call_llm_safe_returns_timeout_error():
    with patch(
        "pipeline.infrastructure.codex_cli.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="codex", timeout=1),
    ), patch(
        "pipeline.infrastructure.codex_cli.shutil.which",
        return_value="/usr/local/bin/codex",
    ):
        output, error = call_llm_safe("test", timeout=1, retries=0)

    assert output is None
    assert isinstance(error, LLMError)
    assert "timed out" in str(error)
