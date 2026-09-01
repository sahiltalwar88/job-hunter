#!/usr/bin/env python3
"""Shared wrapper for calling `devin -p` (Devin CLI in print/non-interactive mode).

Handles:
  - Stripping ACP_BACKEND from the subprocess env (WSL fix — same as the
    scraper's DevinCLIChecker; ACP_BACKEND=windsurf breaks devin -p in subprocess).
  - Writing the prompt to a temp file and passing via --prompt-file (avoids
    shell arg limits and escaping issues for all calls, large or small).
  - --permission-mode dangerous (unattended, needs file read/write).
  - --respect-workspace-trust false (non-interactive can't show trust prompt).
  - Configurable timeout per call.
  - Clean error handling with stdout+stderr captured for debugging.

Usage:
    from llm import call_llm, LLMError

    try:
        output = call_llm("Grade this resume...", model="grader-model", timeout=120)
    except LLMError as e:
        print(f"LLM call failed: {e}")
"""
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


class LLMError(Exception):
    """Raised when a devin -p call fails (timeout, non-zero exit, empty output)."""

    def __init__(self, message, *, stdout="", stderr="", returncode=None):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _find_devin():
    """Locate the devin binary. Returns the path or raises LLMError."""
    devin = shutil.which("devin")
    if devin:
        return devin
    # Check the known install location
    local_bin = os.path.expanduser("~/.local/bin/devin")
    if os.path.isfile(local_bin) and os.access(local_bin, os.X_OK):
        return local_bin
    raise LLMError("devin binary not found on PATH or ~/.local/bin/devin")


def _build_env():
    """Build a clean env for subprocess — strips ACP_BACKEND (WSL fix).

    Sets DEVIN_PIPELINE_MODE=1 so hooks can skip debug-only checks
    (e.g. the Stop hook that blocks on ungraded resumes in drafts/).
    """
    env = dict(os.environ)
    env.pop("ACP_BACKEND", None)
    env["DEVIN_PIPELINE_MODE"] = "1"
    return env


def call_llm(prompt: str, *, model="customizer-model", timeout=120,
             workspace=None, retries=2, retry_delay=5,
             export_path=None, permission_mode="dangerous",
             config_path=None) -> str:
    """Call `devin -p` and return stdout text.

    Args:
        prompt: The prompt text to send to the model.
        model: Model identifier (e.g. "customizer-model", "grader-model").
        timeout: Maximum seconds to wait for the call to complete.
        workspace: Working directory for the devin -p call (default: cwd).
            The grader calls use workspace=.grading/ or .veracity/ respectively,
            so the LLM's relative file references resolve correctly.
        retries: Number of times to retry on failure (default: 2).
        retry_delay: Seconds to wait between retries (default: 5).
        export_path: If set, passes --export <path> to devin -p. The conversation
            (including the agent's thoughts and tool calls) is written to this
            file after each turn. Useful for capturing output before a timeout kill.
        permission_mode: Permission mode for devin -p (default "dangerous" for
            backward compat). Use "normal" for read-only subagents (grader,
            truthfulness reviewer — ADR-0010).
        config_path: If set, passes --config <path> to devin -p. Used for scoped
            permission configs (e.g. customizer — ADR-0010).

    Returns:
        The stdout text from devin -p.

    Raises:
        LLMError: On timeout, non-zero exit, or empty output (after all retries exhausted).
    """
    devin_bin = _find_devin()
    env = _build_env()

    # Write prompt to a temp file — avoids shell escaping and arg length issues.
    # Used for ALL calls (not just large ones) for consistency.
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".md", prefix="devin_prompt_")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            f.write(prompt)

        cmd = [
            devin_bin,
            "-p",
            "--model", model,
            "--permission-mode", permission_mode,
            "--respect-workspace-trust", "false",
            "--prompt-file", tmp_path,
        ]
        if config_path:
            cmd.extend(["--config", str(config_path)])
        if export_path:
            cmd.extend(["--export", str(export_path)])

        last_error = None
        for attempt in range(retries + 1):
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=env,
                    cwd=workspace,
                )
            except (subprocess.TimeoutExpired, OSError) as e:
                # Catch BOTH timeout and OS-level errors (FileNotFoundError,
                # PermissionError, EMFILE/too-many-open-files, etc.). Previously
                # only TimeoutExpired was caught — OSError bypassed the retry
                # loop entirely, propagated uncaught through call_llm_safe
                # (which only caught LLMError), and reached the pipeline's
                # broad except Exception, marking the batch as failed with
                # NO retries. This was the likely cause of the 418-in-10-seconds
                # startup failure pattern observed on 2026-08-19.
                if isinstance(e, subprocess.TimeoutExpired):
                    last_error = LLMError(
                        f"devin -p timed out after {timeout}s (attempt {attempt + 1}/{retries + 1})",
                        returncode=None,
                    )
                else:
                    last_error = LLMError(
                        f"devin -p failed to start: {type(e).__name__}: {e} "
                        f"(attempt {attempt + 1}/{retries + 1})",
                        returncode=None,
                    )
                if attempt < retries:
                    time.sleep(retry_delay)
                continue

            if result.returncode != 0:
                last_error = LLMError(
                    f"devin -p exited with code {result.returncode} (attempt {attempt + 1}/{retries + 1})",
                    stdout=result.stdout,
                    stderr=result.stderr,
                    returncode=result.returncode,
                )
                if attempt < retries:
                    time.sleep(retry_delay)
                continue

            if not result.stdout.strip():
                last_error = LLMError(
                    f"devin -p produced empty output (attempt {attempt + 1}/{retries + 1})",
                    stdout=result.stdout,
                    stderr=result.stderr,
                    returncode=result.returncode,
                )
                if attempt < retries:
                    time.sleep(retry_delay)
                continue

            return result.stdout.strip()

        # All retries exhausted
        raise last_error

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def call_llm_safe(prompt: str, *, model="customizer-model", timeout=120,
                  workspace=None, retries=2, retry_delay=5,
                  export_path=None, permission_mode="dangerous",
                  config_path=None) -> tuple[str | None, LLMError | None]:
    """Like call_llm but returns (output, error) instead of raising.

    Useful when the caller wants to handle errors inline without try/except.
    Catches all exceptions (not just LLMError) so unexpected errors don't
    propagate uncaught — they're wrapped in LLMError with the original
    exception type and message preserved for diagnosis.
    """
    try:
        return call_llm(prompt, model=model, timeout=timeout, workspace=workspace,
                        retries=retries, retry_delay=retry_delay,
                        export_path=export_path,
                        permission_mode=permission_mode,
                        config_path=config_path), None
    except LLMError as e:
        return None, e
    except Exception as e:
        # Wrap unexpected exceptions (OSError, etc. that might escape call_llm's
        # retry loop) in LLMError so callers get a consistent error type.
        # Preserve the original exception info for diagnosis.
        return None, LLMError(
            f"Unexpected {type(e).__name__}: {e}",
            returncode=None,
        )
