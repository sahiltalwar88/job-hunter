#!/usr/bin/env python3
"""Pushover push notifications for the job-hunter pipeline.

Reuses the same PUSHOVER_TOKEN / PUSHOVER_USER env vars as the scraper's
notify.py. No-op if credentials are not set (so local runs without Pushover
are unaffected).

Usage:
    from pipeline.infrastructure.notify import notify, notify_pipeline_summary, notify_ready, notify_error
"""
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from pipeline.helpers.pii_scrub import scrub_pii

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


def _credentials() -> tuple[str | None, str | None]:
    """Return (token, user) from env, or (None, None) if not set."""
    return os.environ.get("PUSHOVER_TOKEN"), os.environ.get("PUSHOVER_USER")


def _send(token: str, user: str, *, title: str, message: str,
          url: str = "", priority: int = 0) -> bool:
    """Send a Pushover notification. Returns True on success.

    PII (emails, phone numbers, candidate names) is scrubbed from title
    and message before transmission.
    """
    title = scrub_pii(title)
    message = scrub_pii(message)
    body = {
        "token": token,
        "user": user,
        "title": title[:250],
        "message": message[:1024],
        "priority": priority,
    }
    if url:
        body["url"] = url
    data = urllib.parse.urlencode(body).encode()
    try:
        req = urllib.request.Request(PUSHOVER_URL, data=data)
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "ignore")
        except Exception:
            detail = ""
        print(f"  ⚠️  Pushover HTTP {e.code}: {detail[:300]}")
        return False
    except Exception as e:
        print(f"  ⚠️  Pushover send failed: {e}")
        return False


def notify(title: str, message: str, *, url: str = "",
           priority: int = 0) -> bool:
    """Send a Pushover notification. No-op if creds not set.

    Args:
        title: Notification title (truncated to 250 chars by Pushover).
        message: Notification body (truncated to 1024 chars by Pushover).
        url: Optional URL to attach.
        priority: Pushover priority (-2 to 2). 0 = normal, 1 = high, 2 = emergency.

    Returns:
        True if sent successfully, False if failed or creds not set.
    """
    token, user = _credentials()
    if not token or not user:
        return False  # notifications disabled — no creds
    return _send(token, user, title=title, message=message, url=url,
                 priority=priority)


def notify_pipeline_summary(stats: dict) -> None:
    """Send an end-of-run pipeline summary.

    Args:
        stats: Dict with keys: processed, trashed, rejected_job_fit,
               rejected_resume, ready, errors (all ints).
    """
    processed = stats.get("processed", 0)
    trashed = stats.get("trashed", 0)
    rejected_job = stats.get("rejected_job_fit", 0)
    rejected_res = stats.get("rejected_resume", 0)
    ready = stats.get("ready", 0)
    errors = stats.get("errors", 0)

    message = (
        f"Processed: {processed}\n"
        f"Ready: {ready}\n"
        f"Trashed: {trashed}\n"
        f"Rejected (job-fit): {rejected_job}\n"
        f"Rejected (resume): {rejected_res}\n"
        f"Errors: {errors}"
    )
    notify("Job Hunter Pipeline Run", message)


def notify_ready(company_role: str, jd_title: str = "") -> None:
    """Notify that a resume reached ready/. High priority."""
    title = f"Resume Ready: {company_role}"
    message = f"A resume for {company_role} passed grading and truthfulness review."
    if jd_title:
        message += f"\nRole: {jd_title}"
    message += "\nReady for manual submission."
    notify(title, message, priority=1)


def notify_error(step: str, error: str, company_role: str = "",
                 log_file: str | Path | None = None) -> None:
    """Notify about a pipeline error. Priority +1 (high).

    Writes the error to a persistent log file BEFORE attempting the Pushover
    notification, so fatal errors are always recorded even if Pushover is
    down or credentials are missing.

    Args:
        step: Pipeline step name where the error occurred.
        error: Error message text.
        company_role: Optional job slug for context.
        log_file: Path to a log file for persistent error recording.
            If provided, the error is written here before Pushover is
            attempted. If None, falls back to stderr (print).
    """
    title = f"Pipeline Error: {step}"
    message = f"Step: {step}\n"
    if company_role:
        message += f"Job: {company_role}\n"
    message += f"Error: {error[:500]}"

    # Persist to log file first (before Pushover) so errors are never lost.
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_line = f"{timestamp} | step={step} | job={company_role or 'N/A'} | error={error[:500]}\n"
    if log_file:
        try:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a") as f:
                f.write(log_line)
        except OSError as e:
            # Log file write failed — fall back to stderr so the error
            # is at least visible in the terminal.
            print(f"  ⚠️  Could not write to log file {log_file}: {e}", flush=True)
            print(f"  ⚠️  {log_line.strip()}", flush=True)
    else:
        # No log file provided — fall back to stderr (original behavior).
        print(f"  ⚠️  {log_line.strip()}", flush=True)

    notify(title, message, priority=1)
