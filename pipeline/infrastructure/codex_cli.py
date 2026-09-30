#!/usr/bin/env python3
"""Wrapper for calling ``codex exec`` in non-interactive mode.

Prompts are passed on stdin to avoid command-line length and escaping issues.
The assistant's final message is captured with ``--output-last-message`` so
progress output never leaks into the value returned to pipeline parsers.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from pipeline.infrastructure.llm_error import LLMError


def _find_codex() -> str:
    """Locate the Codex CLI binary or raise a provider-neutral LLM error."""
    codex = shutil.which("codex")
    if codex:
        return codex
    local_bin = Path.home() / ".local" / "bin" / "codex"
    if local_bin.is_file() and os.access(local_bin, os.X_OK):
        return str(local_bin)
    raise LLMError("codex binary not found on PATH or ~/.local/bin/codex")


def _sandbox_for(permission_mode: str) -> str:
    """Translate the pipeline permission vocabulary to Codex sandboxes."""
    if permission_mode == "normal":
        return "read-only"
    if permission_mode == "dangerous":
        # The customizer only needs to edit files and run the line counter in
        # the repository. Keep it inside the workspace sandbox rather than
        # disabling Codex's sandbox entirely.
        return "workspace-write"
    raise LLMError(f"unsupported permission mode for codex: {permission_mode}")


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
    """Call ``codex exec`` and return the assistant's final message.

    ``config_path`` is accepted for compatibility with the shared LLM
    protocol. It points at Devin's JSON permission file and therefore has no
    Codex equivalent; Codex enforcement comes from ``--sandbox`` instead.
    Set ``model`` to ``"default"`` to use the model configured by Codex.
    """
    del config_path, alive_check_seconds
    codex_bin = _find_codex()
    sandbox = _sandbox_for(permission_mode)

    owned_output = export_path is None
    if owned_output:
        output_fd, output_name = tempfile.mkstemp(
            suffix=".txt", prefix="codex_last_message_"
        )
        os.close(output_fd)
        output_path = Path(output_name)
    else:
        output_path = Path(export_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        codex_bin,
        "--ask-for-approval",
        "never",
        "exec",
        "--sandbox",
        sandbox,
        "--output-last-message",
        str(output_path),
    ]
    if model and model != "default":
        cmd.extend(["--model", model])
    cmd.append("-")

    try:
        last_error = None
        for attempt in range(retries + 1):
            try:
                output_path.write_text("", encoding="utf-8")
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
                        f"codex exec timed out after {timeout}s "
                        f"(attempt {attempt + 1}/{retries + 1})"
                    )
                else:
                    message = (
                        f"codex exec failed to start: {type(exc).__name__}: {exc} "
                        f"(attempt {attempt + 1}/{retries + 1})"
                    )
                last_error = LLMError(message)
            else:
                if result.returncode != 0:
                    last_error = LLMError(
                        f"codex exec exited with code {result.returncode} "
                        f"(attempt {attempt + 1}/{retries + 1})",
                        stdout=result.stdout,
                        stderr=result.stderr,
                        returncode=result.returncode,
                    )
                else:
                    output = output_path.read_text(encoding="utf-8").strip()
                    if not output:
                        # Older Codex builds may still emit the final response
                        # only on stdout. Keep that behavior compatible.
                        output = result.stdout.strip()
                    if output:
                        return output
                    last_error = LLMError(
                        f"codex exec produced empty output "
                        f"(attempt {attempt + 1}/{retries + 1})",
                        stdout=result.stdout,
                        stderr=result.stderr,
                        returncode=result.returncode,
                    )

            if attempt < retries:
                time.sleep(retry_delay)

        raise last_error
    finally:
        if owned_output:
            try:
                output_path.unlink()
            except OSError:
                pass


def call_llm_safe(prompt: str, **kwargs) -> tuple[str | None, LLMError | None]:
    """Return ``(output, error)`` instead of raising provider failures."""
    try:
        return call_llm(prompt, **kwargs), None
    except LLMError as exc:
        return None, exc
    except Exception as exc:
        return None, LLMError(f"Unexpected {type(exc).__name__}: {exc}")
