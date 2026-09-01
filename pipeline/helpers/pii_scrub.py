#!/usr/bin/env python3
"""PII scrubbing for text sent to external services (Pushover, etc.).

Redacts contact information (emails, phone numbers) and the candidate's
name from text before it's transmitted to third-party services.

Scope: contact info only (email, phone, candidate name). Does NOT scrub
employer names, job titles, or locations — those are contextually relevant
in job notifications and are not PII in the same sense.

Future extension: consider presidio or scrubadub for NLP-based PII detection
that can catch entities regex misses (physical addresses, SSNs, etc.).
"""
import re

# Candidate's name components — used for name scrubbing.
# These are the synthetic sample candidate's names. Users should override
# via scrub_pii(candidate_names=...) with their own name, or by loading
# from config.json (candidate_name field).
DEFAULT_CANDIDATE_NAMES = ["Squall Leonhart", "Squall", "Leonhart"]

# ─── Regex patterns ────────────────────────────────────────────────────────

# Email: standard email pattern
_EMAIL_RE = re.compile(
    r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b'
)

# Phone: US phone numbers in various formats
# Matches: 555-123-4567, (555) 123-4567, +1-555-123-4567, 555.123.4567,
#           5551234567 (10 digits), 1-555-123-4567
_PHONE_RE = re.compile(
    r'(?:\+?1[-.\s]?)?'           # optional country code
    r'(?:\(\d{3}\)|\d{3})'        # area code: (555) or 555
    r'[-.\s]?'                    # separator
    r'\d{3}'                      # exchange
    r'[-.\s]?'                    # separator
    r'\d{4}'                      # subscriber
    r'\b'
)

# Compiled name patterns are built dynamically from the name list.
# We use word boundaries to avoid partial matches (e.g., "Tal" in "battalion").
# Case-insensitive matching catches "squall", "SQUALL", "Squall".


def _build_name_pattern(names: list[str]) -> re.Pattern:
    """Build a regex pattern that matches any of the given names as whole words."""
    # Sort by length descending so "Squall Leonhart" is matched before "Squall"
    sorted_names = sorted(names, key=len, reverse=True)
    escaped = [re.escape(name) for name in sorted_names]
    # Word boundaries on both sides; case-insensitive
    return re.compile(r'\b(?:' + '|'.join(escaped) + r')\b', re.IGNORECASE)


def scrub_pii(
    text: str,
    candidate_names: list[str] | None = None,
) -> str:
    """Scrub PII (emails, phone numbers, candidate names) from text.

    Args:
        text: The text to scrub.
        candidate_names: Names to redact. Defaults to DEFAULT_CANDIDATE_NAMES.

    Returns:
        The text with PII replaced by [EMAIL], [PHONE], [NAME] placeholders.
    """
    names = candidate_names if candidate_names is not None else DEFAULT_CANDIDATE_NAMES
    name_re = _build_name_pattern(names)

    # Apply in order: emails first, then phones, then names.
    # Names last so that an email like "squall@example.com" doesn't leave
    # a stray "[NAME]@example.com" after name scrubbing (email already
    # replaced it with [EMAIL]).
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    text = name_re.sub("[NAME]", text)

    return text
