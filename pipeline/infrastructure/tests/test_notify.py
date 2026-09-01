"""Test notify.py — Pushover notification helper.

Uses mocked urllib — no real Pushover calls.
"""
from unittest.mock import patch, MagicMock

from pipeline.infrastructure import notify
from pipeline.infrastructure.notify import notify, notify_pipeline_summary, notify_ready, notify_error


def test_notify_no_credentials():
    """notify should return False and not send when creds are not set."""
    with patch("pipeline.infrastructure.notify.os.environ", {}):
        result = notify("Test", "Message")
    assert result is False


def test_notify_success():
    """notify should return True on successful Pushover response."""
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.infrastructure.notify.os.environ", {"PUSHOVER_TOKEN": "token", "PUSHOVER_USER": "user"}), \
         patch("pipeline.infrastructure.notify.urllib.request.urlopen", return_value=mock_response):
        result = notify("Test Title", "Test message")

    assert result is True


def test_notify_http_error():
    """notify should return False on HTTP error."""
    import urllib.error
    error = urllib.error.HTTPError(
        url="https://api.pushover.net/1/messages.json",
        code=400,
        msg="Bad Request",
        hdrs={},
        fp=MagicMock(),
    )

    with patch("pipeline.infrastructure.notify.os.environ", {"PUSHOVER_TOKEN": "bad", "PUSHOVER_USER": "bad"}), \
         patch("pipeline.infrastructure.notify.urllib.request.urlopen", side_effect=error):
        result = notify("Test", "Message")

    assert result is False


def test_notify_pipeline_summary_no_creds():
    """notify_pipeline_summary should be a no-op when creds are not set."""
    with patch("pipeline.infrastructure.notify.os.environ", {}):
        # Should not raise
        notify_pipeline_summary({
            "processed": 5, "trashed": 2, "ready": 1,
            "rejected_job_fit": 1, "rejected_resume": 1, "errors": 0
        })


def test_notify_pipeline_summary_sends():
    """notify_pipeline_summary should send a notification with stats."""
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.infrastructure.notify.os.environ", {"PUSHOVER_TOKEN": "token", "PUSHOVER_USER": "user"}), \
         patch("pipeline.infrastructure.notify.urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
        notify_pipeline_summary({
            "processed": 5, "trashed": 2, "ready": 1,
            "rejected_job_fit": 1, "rejected_resume": 1, "errors": 0
        })

    # Verify the request was made
    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]
    # The data is URL-encoded — decode it to check content
    import urllib.parse
    data = urllib.parse.unquote_plus(req.data.decode())
    assert "Processed: 5" in data
    assert "Ready: 1" in data


def test_notify_ready_sends_high_priority():
    """notify_ready should send with priority 1 (high)."""
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.infrastructure.notify.os.environ", {"PUSHOVER_TOKEN": "token", "PUSHOVER_USER": "user"}), \
         patch("pipeline.infrastructure.notify.urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
        notify_ready("google-director-of-engineering", "Director of Engineering")

    req = mock_urlopen.call_args[0][0]
    data = req.data.decode()
    assert "priority=1" in data
    assert "google-director-of-engineering" in data


def test_notify_error_sends_high_priority():
    """notify_error should send with priority 1 (high)."""
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.infrastructure.notify.os.environ", {"PUSHOVER_TOKEN": "token", "PUSHOVER_USER": "user"}), \
         patch("pipeline.infrastructure.notify.urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
        notify_error("grade_resume", "LLM timeout", "stripe-vp-engineering")

    req = mock_urlopen.call_args[0][0]
    data = req.data.decode()
    assert "priority=1" in data
    assert "grade_resume" in data
    assert "stripe-vp-engineering" in data


def test_notify_error_writes_to_log_file(tmp_path):
    """notify_error should write to a persistent log file before Pushover."""
    log_file = tmp_path / "logs" / "run-2026-08-25.log"

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.infrastructure.notify.os.environ", {"PUSHOVER_TOKEN": "token", "PUSHOVER_USER": "user"}), \
         patch("pipeline.infrastructure.notify.urllib.request.urlopen", return_value=mock_response):
        notify_error("grade_resume", "LLM timeout", "stripe-vp-engineering",
                     log_file=log_file)

    # Log file should exist and contain the error
    assert log_file.exists()
    content = log_file.read_text()
    assert "grade_resume" in content
    assert "stripe-vp-engineering" in content
    assert "LLM timeout" in content


def test_notify_error_creates_log_dir(tmp_path):
    """notify_error should create parent directories for the log file."""
    log_file = tmp_path / "logs" / "subdir" / "run.log"

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.infrastructure.notify.os.environ", {"PUSHOVER_TOKEN": "token", "PUSHOVER_USER": "user"}), \
         patch("pipeline.infrastructure.notify.urllib.request.urlopen", return_value=mock_response):
        notify_error("step1", "some error", log_file=log_file)

    assert log_file.exists()
    assert "some error" in log_file.read_text()


def test_notify_error_falls_back_to_stderr_without_log_file(capsys):
    """notify_error should print to stderr when no log_file is provided."""
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.infrastructure.notify.os.environ", {"PUSHOVER_TOKEN": "token", "PUSHOVER_USER": "user"}), \
         patch("pipeline.infrastructure.notify.urllib.request.urlopen", return_value=mock_response):
        notify_error("step1", "some error", "some-job")

    captured = capsys.readouterr()
    assert "some error" in captured.out
    assert "step1" in captured.out


def test_notify_error_log_file_write_failure_falls_back_to_stderr(tmp_path, capsys):
    """If the log file can't be written, fall back to stderr."""
    # Use a path that can't be written to (root-level path)
    log_file = "/dev/null/cannot-create-logs/run.log"

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pipeline.infrastructure.notify.os.environ", {"PUSHOVER_TOKEN": "token", "PUSHOVER_USER": "user"}), \
         patch("pipeline.infrastructure.notify.urllib.request.urlopen", return_value=mock_response):
        notify_error("step1", "some error", "some-job", log_file=log_file)

    captured = capsys.readouterr()
    # Should have fallen back to stderr
    assert "some error" in captured.out
