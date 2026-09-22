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
        output = call_llm("Grade this resume...", model=DEFAULT_LLM_MODEL, timeout=120)
    except LLMError as e:
        print(f"LLM call failed: {e}")
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
import structlog
from pathlib import Path

from pipeline.infrastructure.config import DEFAULT_LLM_MODEL
from pipeline.infrastructure.llm_error import LLMError


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


def _extract_thinking_from_export(export_path: str) -> str | None:
    """Parse a devin --export file and extract thinking content if content is empty.

    The export file is a JSON conversation transcript. We look for the last
    assistant message and return its thinking text if the content is empty.

    Returns the thinking text, or None if no thinking is found or the file
    can't be parsed.
    """
    try:
        with open(export_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    # The export format may vary — try common structures.
    # Format 1: {"messages": [{"role": "assistant", "content": "...", "thinking": {"thinking": "..."}}]}
    # Format 2: {"nodes": [{"chat_message": "{\"role\": \"assistant\", ...}"}]}
    messages = None
    if isinstance(data, dict):
        if "messages" in data:
            messages = data["messages"]
        elif "nodes" in data:
            # Nodes have chat_message as JSON strings
            messages = []
            for node in data["nodes"]:
                if isinstance(node, dict) and "chat_message" in node:
                    try:
                        msg = json.loads(node["chat_message"])
                        messages.append(msg)
                    except (json.JSONDecodeError, TypeError):
                        pass

    if not messages:
        return None

    # Find the last assistant message
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        # Skip system prefix messages
        if content and isinstance(content, str) and content.startswith("You are Devin"):
            continue
        # If content is non-empty, no need for thinking fallback
        if content:
            return None
        # Content is empty — extract thinking
        thinking = msg.get("thinking", {})
        if isinstance(thinking, dict):
            thinking_text = thinking.get("thinking", "")
        else:
            thinking_text = str(thinking)
        if thinking_text:
            return thinking_text
    return None


def call_llm(prompt: str, *, model=DEFAULT_LLM_MODEL, timeout=120,
             workspace=None, retries=2, retry_delay=5,
             export_path=None, permission_mode="dangerous",
             config_path=None, alive_check_seconds=10) -> str:
    """Call `devin -p` and return stdout text.

    Args:
        prompt: The prompt text to send to the model.
        model: Model identifier, or "default" to use Devin CLI's configured
            account-default model.
        timeout: Maximum seconds to wait for the call to complete.
        workspace: Working directory for the devin -p call (default: cwd).
            The grader calls use workspace=.grading/ or .veracity/ respectively,
            so the LLM's relative file references resolve correctly.
        retries: Number of times to retry on failure (default: 2).
        retry_delay: Seconds to wait between retries (default: 5).
        export_path: If set, passes --export <path> to devin -p. The conversation
            (including the agent's thoughts and tool calls) is written to this
            file after each turn. Useful for capturing output before a timeout kill.
        permission_mode: Provider-neutral permission mode (default "dangerous"
            for backward compatibility). The legacy read-only value "normal"
            is translated to Devin CLI's current "auto" mode.
        config_path: If set, passes --config <path> to devin -p. Used for scoped
            permission configs (e.g. customizer — ADR-0010).
        alive_check_seconds: If > 0, kill the process and retry if no stdout
            is produced within this many seconds (Q4 — catches backend failures
            where the model never responds). Default: 10.

    Returns:
        The stdout text from devin -p.

    Raises:
        LLMError: On timeout, non-zero exit, or empty output (after all retries exhausted).
    """
    devin_bin = _find_devin()
    env = _build_env()
    logger = structlog.get_logger(__name__)

    # Write prompt to a temp file — avoids shell escaping and arg length issues.
    # Used for ALL calls (not just large ones) for consistency.
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".md", prefix="devin_prompt_")
    # Always use a temp export file for thinking-only fallback (Q3).
    # If the caller provides export_path, use that instead.
    own_export = None
    if not export_path:
        own_export_fd, own_export = tempfile.mkstemp(
            suffix=".json", prefix="devin_export_"
        )
        os.close(own_export_fd)
    effective_export = export_path or own_export

    try:
        with os.fdopen(tmp_fd, "w") as f:
            f.write(prompt)

        devin_permission_mode = (
            "auto" if permission_mode == "normal" else permission_mode
        )
        cmd = [
            devin_bin,
            "-p",
            "--permission-mode", devin_permission_mode,
            "--respect-workspace-trust", "false",
            "--prompt-file", tmp_path,
            "--export", str(effective_export),
        ]
        if model and model != "default":
            cmd.extend(["--model", model])
        if config_path:
            cmd.extend(["--config", str(config_path)])

        last_error = None
        for attempt in range(retries + 1):
            try:
                # Q4: Alive check — on the first attempt, use a shorter
                # timeout (alive_check_seconds) to catch backend failures
                # where the model never responds. If the first attempt
                # times out, subsequent attempts use the full timeout.
                if (attempt == 0 and alive_check_seconds
                        and alive_check_seconds < timeout):
                    effective_timeout = alive_check_seconds
                else:
                    effective_timeout = timeout

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=effective_timeout,
                    env=env,
                    cwd=workspace,
                )
            except (subprocess.TimeoutExpired, OSError) as e:
                if isinstance(e, subprocess.TimeoutExpired):
                    if attempt == 0 and alive_check_seconds and alive_check_seconds < timeout:
                        last_error = LLMError(
                            f"devin -p alive check timed out after "
                            f"{alive_check_seconds}s (attempt {attempt + 1}/{retries + 1})",
                            returncode=None,
                        )
                    else:
                        last_error = LLMError(
                            f"devin -p timed out after {effective_timeout}s "
                            f"(attempt {attempt + 1}/{retries + 1})",
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
                # Q3: Thinking-only fallback — if stdout is empty but the
                # export file has thinking content, extract it.
                thinking = _extract_thinking_from_export(str(effective_export))
                if thinking and thinking.strip():
                    logger.warning(
                        "devin -p produced empty stdout — recovered JSON "
                        "from thinking field (thinking-only fallback)",
                        attempt=attempt + 1,
                        thinking_len=len(thinking),
                    )
                    return thinking.strip()

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
        if own_export:
            try:
                os.unlink(own_export)
            except OSError:
                pass


def call_llm_safe(prompt: str, *, model=DEFAULT_LLM_MODEL, timeout=120,
                  workspace=None, retries=2, retry_delay=5,
                  export_path=None, permission_mode="dangerous",
                  config_path=None, alive_check_seconds=10) -> tuple[str | None, LLMError | None]:
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
                        config_path=config_path,
                        alive_check_seconds=alive_check_seconds), None
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
