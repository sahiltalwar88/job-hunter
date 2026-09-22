#!/usr/bin/env python3
"""Wrapper for calling Claude Code in print/non-interactive mode."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from pipeline.infrastructure.llm_error import LLMError


def _find_claude() -> str:
    """Locate the Claude CLI binary or raise a provider-neutral LLM error."""
    claude = shutil.which("claude")
    if claude:
        return claude
    local_bin = Path.home() / ".local" / "bin" / "claude"
    if local_bin.is_file() and os.access(local_bin, os.X_OK):
        return str(local_bin)
    raise LLMError("claude binary not found on PATH or ~/.local/bin/claude")


def _permission_args(permission_mode: str) -> list[str]:
    """Translate pipeline permissions to Claude Code CLI flags."""
    if permission_mode == "normal":
        # Grading and verification only need repository reads. Restricting the
        # tool list also removes Bash and all file-writing tools from context.
        return [
            "--permission-mode",
            "dontAsk",
            "--tools",
            "Read,Glob,Grep",
        ]
    if permission_mode == "dangerous":
        # The customizer must edit draft resumes and invoke count_lines without
        # an interactive prompt. dontAsk turns every unlisted action into a
        # denial, so this is narrower than bypassPermissions.
        return [
            "--permission-mode",
            "dontAsk",
            "--tools",
            "Read,Glob,Grep,Edit,Write,Bash",
            "--allowedTools",
            "Read",
            "Edit(/stages/2_drafts/**)",
            "Write(/stages/2_drafts/**)",
            "Bash(python3 -m pipeline.helpers.count_lines *)",
        ]
    raise LLMError(f"unsupported permission mode for claude: {permission_mode}")


def call_llm(
    prompt: str,
    *,
    model: str = "default",
    timeout: int = 120,
    workspace=None,
    retries: int = 2,
    retry_delay: int = 5,
    export_path=None,
    permission_mode: str = "dangerous",
    config_path=None,
    alive_check_seconds=None,
) -> str:
    """Call ``claude -p`` and return its text response.

    ``config_path`` is accepted for compatibility with the shared LLM
    protocol. It contains Devin-specific permission JSON; Claude permissions
    are supplied directly on the command line. Set ``model`` to ``"default"``
    to use Claude Code's configured model.
    """
    del config_path, alive_check_seconds
    claude_bin = _find_claude()
    cmd = [
        claude_bin,
        "--print",
        "--output-format",
        "text",
        "--no-session-persistence",
        *_permission_args(permission_mode),
    ]
    if model and model != "default":
        cmd.extend(["--model", model])

    last_error = None
    for attempt in range(retries + 1):
        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=dict(os.environ),
                cwd=workspace,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                message = (
                    f"claude -p timed out after {timeout}s "
                    f"(attempt {attempt + 1}/{retries + 1})"
                )
            else:
                message = (
                    f"claude -p failed to start: {type(exc).__name__}: {exc} "
                    f"(attempt {attempt + 1}/{retries + 1})"
                )
            last_error = LLMError(message)
        else:
            if result.returncode != 0:
                last_error = LLMError(
                    f"claude -p exited with code {result.returncode} "
                    f"(attempt {attempt + 1}/{retries + 1})",
                    stdout=result.stdout,
                    stderr=result.stderr,
                    returncode=result.returncode,
                )
            elif not result.stdout.strip():
                last_error = LLMError(
                    f"claude -p produced empty output "
                    f"(attempt {attempt + 1}/{retries + 1})",
                    stdout=result.stdout,
                    stderr=result.stderr,
                    returncode=result.returncode,
                )
            else:
                output = result.stdout.strip()
                if export_path:
                    destination = Path(export_path)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(output + "\n", encoding="utf-8")
                return output

        if attempt < retries:
            time.sleep(retry_delay)

    raise last_error


def call_llm_safe(prompt: str, **kwargs) -> tuple[str | None, LLMError | None]:
    """Return ``(output, error)`` instead of raising provider failures."""
    try:
        return call_llm(prompt, **kwargs), None
    except LLMError as exc:
        return None, exc
    except Exception as exc:
        return None, LLMError(f"Unexpected {type(exc).__name__}: {exc}")
