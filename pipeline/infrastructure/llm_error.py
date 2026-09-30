"""Shared error type for command-line LLM provider failures."""


class LLMError(Exception):
    """A timeout, process failure, or invalid empty response from an LLM CLI."""

    def __init__(self, message, *, stdout="", stderr="", returncode=None):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
