"""Test devin_cli.py — the shared devin -p wrapper.

Uses mocked subprocess — no real devin calls.
"""
import os
import subprocess
from unittest.mock import patch, MagicMock

from pipeline.infrastructure import devin_cli as llm
from pipeline.infrastructure.devin_cli import call_llm, call_llm_safe, LLMError


def test_call_llm_success():
    """call_llm should return stdout when devin -p succeeds."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "GRADE: 9\nJUSTIFICATION: Great fit."
    mock_result.stderr = ""

    with patch("pipeline.infrastructure.devin_cli.subprocess.run", return_value=mock_result) as mock_run, \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}):
        result = call_llm("Grade this resume")

    assert result == "GRADE: 9\nJUSTIFICATION: Great fit."
    # Verify the command was constructed correctly
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "/usr/local/bin/devin"
    assert "-p" in cmd
    assert "--model" in cmd
    assert "customizer-model" in cmd
    assert "--permission-mode" in cmd
    assert "dangerous" in cmd
    assert "--respect-workspace-trust" in cmd
    assert "false" in cmd
    assert "--prompt-file" in cmd


def test_call_llm_custom_model():
    """call_llm should pass the custom model to devin -p."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Result"
    mock_result.stderr = ""

    with patch("pipeline.infrastructure.devin_cli.subprocess.run", return_value=mock_result) as mock_run, \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}):
        call_llm("Grade this", model="grader-model")

    cmd = mock_run.call_args[0][0]
    assert "grader-model" in cmd


def test_call_llm_strips_acp_backend():
    """call_llm should strip ACP_BACKEND from the subprocess env."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Result"
    mock_result.stderr = ""

    with patch("pipeline.infrastructure.devin_cli.subprocess.run", return_value=mock_result) as mock_run, \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin", "ACP_BACKEND": "windsurf"}):
        call_llm("Test")

    env = mock_run.call_args[1]["env"]
    assert "ACP_BACKEND" not in env


def test_call_llm_timeout():
    """call_llm should raise LLMError on timeout."""
    with patch("pipeline.infrastructure.devin_cli.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="devin", timeout=5)), \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}), \
         patch("pipeline.infrastructure.devin_cli.time.sleep"):
        try:
            call_llm("Test", timeout=5, retries=0)
            assert False, "Should have raised LLMError"
        except LLMError as e:
            assert "timed out" in str(e).lower()


def test_call_llm_nonzero_exit():
    """call_llm should raise LLMError on non-zero exit."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = "some output"
    mock_result.stderr = "error message"

    with patch("pipeline.infrastructure.devin_cli.subprocess.run", return_value=mock_result), \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}), \
         patch("pipeline.infrastructure.devin_cli.time.sleep"):
        try:
            call_llm("Test", retries=0)
            assert False, "Should have raised LLMError"
        except LLMError as e:
            assert e.returncode == 1
            assert e.stderr == "error message"


def test_call_llm_empty_output():
    """call_llm should raise LLMError on empty output."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "   \n  "
    mock_result.stderr = ""

    with patch("pipeline.infrastructure.devin_cli.subprocess.run", return_value=mock_result), \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}), \
         patch("pipeline.infrastructure.devin_cli.time.sleep"):
        try:
            call_llm("Test", retries=0)
            assert False, "Should have raised LLMError"
        except LLMError as e:
            assert "empty" in str(e).lower()


def test_call_llm_retries_then_succeeds():
    """call_llm should retry on failure and succeed if a later attempt works."""
    fail_result = MagicMock()
    fail_result.returncode = 1
    fail_result.stdout = ""
    fail_result.stderr = "transient error"

    success_result = MagicMock()
    success_result.returncode = 0
    success_result.stdout = "Success on retry"
    success_result.stderr = ""

    with patch("pipeline.infrastructure.devin_cli.subprocess.run", side_effect=[fail_result, success_result]), \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}), \
         patch("pipeline.infrastructure.devin_cli.time.sleep"):
        result = call_llm("Test", retries=1, retry_delay=0)

    assert result == "Success on retry"


def test_call_llm_retries_exhausted():
    """call_llm should raise after all retries are exhausted."""
    fail_result = MagicMock()
    fail_result.returncode = 1
    fail_result.stdout = ""
    fail_result.stderr = "persistent error"

    with patch("pipeline.infrastructure.devin_cli.subprocess.run", return_value=fail_result), \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}), \
         patch("pipeline.infrastructure.devin_cli.time.sleep"):
        try:
            call_llm("Test", retries=2, retry_delay=0)
            assert False, "Should have raised LLMError"
        except LLMError as e:
            assert e.returncode == 1
            assert "attempt 3/3" in str(e)


def test_call_llm_safe_returns_error():
    """call_llm_safe should return (None, error) instead of raising."""
    with patch("pipeline.infrastructure.devin_cli.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="devin", timeout=5)), \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}), \
         patch("pipeline.infrastructure.devin_cli.time.sleep"):
        output, error = call_llm_safe("Test", timeout=5, retries=0)

    assert output is None
    assert error is not None
    assert isinstance(error, LLMError)


def test_call_llm_safe_returns_output():
    """call_llm_safe should return (output, None) on success."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Success"
    mock_result.stderr = ""

    with patch("pipeline.infrastructure.devin_cli.subprocess.run", return_value=mock_result), \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}):
        output, error = call_llm_safe("Test")

    assert output == "Success"
    assert error is None


def test_call_llm_workspace():
    """call_llm should pass the workspace as cwd to subprocess."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Result"
    mock_result.stderr = ""

    with patch("pipeline.infrastructure.devin_cli.subprocess.run", return_value=mock_result) as mock_run, \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}):
        call_llm("Test", workspace="/tmp/some-workspace")

    assert mock_run.call_args[1]["cwd"] == "/tmp/some-workspace"


# ─── OSError retry tests (Fix: Path A) ────────────────────────────────────
# Previously, only subprocess.TimeoutExpired was caught in the retry loop.
# OSError (FileNotFoundError, PermissionError, EMFILE, etc.) bypassed retries
# entirely, propagating uncaught through call_llm_safe. These tests verify
# that OSError is now caught and retried.

def test_call_llm_oserror_retried():
    """call_llm should retry on OSError (e.g. EMFILE) and succeed if retry works."""
    success_result = MagicMock()
    success_result.returncode = 0
    success_result.stdout = "Success after retry"
    success_result.stderr = ""

    with patch("pipeline.infrastructure.devin_cli.subprocess.run",
               side_effect=[OSError("[Errno 24] Too many open files"), success_result]), \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}), \
         patch("pipeline.infrastructure.devin_cli.time.sleep"):
        result = call_llm("Test", retries=1, retry_delay=0)

    assert result == "Success after retry"


def test_call_llm_oserror_exhausted():
    """call_llm should raise LLMError (not OSError) after retries exhausted."""
    with patch("pipeline.infrastructure.devin_cli.subprocess.run",
               side_effect=OSError("[Errno 24] Too many open files")), \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}), \
         patch("pipeline.infrastructure.devin_cli.time.sleep"):
        try:
            call_llm("Test", retries=1, retry_delay=0)
            assert False, "Should have raised LLMError"
        except LLMError as e:
            assert "Too many open files" in str(e) or "OSError" in str(e)
        except OSError:
            assert False, "OSError should be wrapped in LLMError, not propagated"


def test_call_llm_filenotfounderror_retried():
    """FileNotFoundError (devin binary disappeared mid-run) should be retried."""
    with patch("pipeline.infrastructure.devin_cli.subprocess.run",
               side_effect=FileNotFoundError("[Errno 2] No such file or directory: 'devin'")), \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}), \
         patch("pipeline.infrastructure.devin_cli.time.sleep"):
        try:
            call_llm("Test", retries=0, retry_delay=0)
            assert False, "Should have raised LLMError"
        except LLMError as e:
            assert "FileNotFoundError" in str(e) or "No such file" in str(e)


def test_call_llm_safe_catches_unexpected_exception():
    """call_llm_safe should wrap non-LLMError exceptions in LLMError."""
    # Simulate an exception that escapes call_llm's retry loop
    # (e.g. a bug in temp file handling)
    with patch("pipeline.infrastructure.devin_cli.tempfile.mkstemp",
               side_effect=OSError("Disk full")), \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}):
        output, error = call_llm_safe("Test")

    assert output is None
    assert error is not None
    assert isinstance(error, LLMError)
    assert "OSError" in str(error) or "Disk full" in str(error)


# ─── permission_mode tests (ADR-0010) ──────────────────────────────────────


def test_call_llm_permission_mode_normal():
    """call_llm should pass permission_mode='normal' to devin -p."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Result"
    mock_result.stderr = ""

    with patch("pipeline.infrastructure.devin_cli.subprocess.run", return_value=mock_result) as mock_run, \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}):
        call_llm("Test", permission_mode="normal")

    cmd = mock_run.call_args[0][0]
    assert "--permission-mode" in cmd
    idx = cmd.index("--permission-mode")
    assert cmd[idx + 1] == "normal"


def test_call_llm_permission_mode_defaults_dangerous():
    """call_llm should default to 'dangerous' permission mode (backward compat)."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Result"
    mock_result.stderr = ""

    with patch("pipeline.infrastructure.devin_cli.subprocess.run", return_value=mock_result) as mock_run, \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}):
        call_llm("Test")

    cmd = mock_run.call_args[0][0]
    idx = cmd.index("--permission-mode")
    assert cmd[idx + 1] == "dangerous"


def test_call_llm_safe_passes_permission_mode():
    """call_llm_safe should pass permission_mode through to call_llm."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Result"
    mock_result.stderr = ""

    with patch("pipeline.infrastructure.devin_cli.subprocess.run", return_value=mock_result) as mock_run, \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}):
        call_llm_safe("Test", permission_mode="normal")

    cmd = mock_run.call_args[0][0]
    idx = cmd.index("--permission-mode")
    assert cmd[idx + 1] == "normal"


def test_call_llm_config_path():
    """call_llm should pass --config <path> when config_path is set."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Result"
    mock_result.stderr = ""

    with patch("pipeline.infrastructure.devin_cli.subprocess.run", return_value=mock_result) as mock_run, \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}):
        call_llm("Test", config_path="/path/to/config.json")

    cmd = mock_run.call_args[0][0]
    assert "--config" in cmd
    idx = cmd.index("--config")
    assert cmd[idx + 1] == "/path/to/config.json"


def test_call_llm_no_config_path_by_default():
    """call_llm should NOT pass --config when config_path is not set."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Result"
    mock_result.stderr = ""

    with patch("pipeline.infrastructure.devin_cli.subprocess.run", return_value=mock_result) as mock_run, \
         patch("pipeline.infrastructure.devin_cli.shutil.which", return_value="/usr/local/bin/devin"), \
         patch("pipeline.infrastructure.devin_cli.os.environ", {"PATH": "/usr/bin"}):
        call_llm("Test")

    cmd = mock_run.call_args[0][0]
    assert "--config" not in cmd
