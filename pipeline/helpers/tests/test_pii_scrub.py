"""Test pii_scrub.py — PII scrubbing before Pushover notifications.

Tests that emails, phone numbers, and candidate names are redacted from
text before it's sent to external services.
"""
import pytest

from pipeline.helpers.pii_scrub import scrub_pii, DEFAULT_CANDIDATE_NAMES


# ─── Email scrubbing ────────────────────────────────────────────────────────


def test_scrubs_email():
    """Email addresses should be replaced with [EMAIL]."""
    text = "Contact squall@example.com for details"
    result = scrub_pii(text)
    assert "squall@example.com" not in result
    assert "[EMAIL]" in result


def test_scrubs_multiple_emails():
    """Multiple emails should all be scrubbed."""
    text = "From: a@b.com, To: c@d.org"
    result = scrub_pii(text)
    assert "a@b.com" not in result
    assert "c@d.org" not in result
    assert result.count("[EMAIL]") == 2


def test_scrubs_email_with_plus_sign():
    """Emails with + signs should be scrubbed."""
    text = "squall.leonhart+jobs@gmail.com"
    result = scrub_pii(text)
    assert "squall.leonhart+jobs@gmail.com" not in result
    assert "[EMAIL]" in result


# ─── Phone scrubbing ────────────────────────────────────────────────────────


def test_scrubs_us_phone_with_dashes():
    """US phone numbers with dashes should be scrubbed."""
    text = "Call me at 555-123-4567"
    result = scrub_pii(text)
    assert "555-123-4567" not in result
    assert "[PHONE]" in result


def test_scrubs_us_phone_with_parens():
    """US phone numbers with parentheses should be scrubbed."""
    text = "Phone: (555) 123-4567"
    result = scrub_pii(text)
    assert "(555) 123-4567" not in result
    assert "[PHONE]" in result


def test_scrubs_phone_with_country_code():
    """Phone numbers with +1 country code should be scrubbed."""
    text = "Reach me at +1-555-123-4567"
    result = scrub_pii(text)
    assert "+1-555-123-4567" not in result
    assert "[PHONE]" in result


def test_scrubs_dots_phone():
    """Phone numbers with dots should be scrubbed."""
    text = "555.123.4567"
    result = scrub_pii(text)
    assert "555.123.4567" not in result
    assert "[PHONE]" in result


# ─── Name scrubbing ─────────────────────────────────────────────────────────


def test_scrubs_candidate_full_name():
    """The candidate's full name should be scrubbed."""
    text = "Squall Leonhart has 10 years of experience"
    result = scrub_pii(text)
    assert "Squall Leonhart" not in result
    assert "[NAME]" in result


def test_scrubs_candidate_first_name():
    """The candidate's first name should be scrubbed when it appears as a standalone word."""
    text = "Squall is a strong candidate"
    result = scrub_pii(text)
    assert "Squall" not in result
    assert "[NAME]" in result


def test_scrubs_candidate_last_name():
    """The candidate's last name should be scrubbed when it appears as a standalone word."""
    text = "Leonhart previously worked at Oracle"
    result = scrub_pii(text)
    assert "Leonhart" not in result
    assert "[NAME]" in result


def test_scrubs_case_insensitive_name():
    """Name scrubbing should be case-insensitive."""
    text = "squall leonhart applied for the role"
    result = scrub_pii(text)
    assert "squall leonhart" not in result.lower()
    assert "[NAME]" in result


def test_does_not_scrub_partial_matches():
    """Names inside other words should not be scrubbed."""
    text = "The battalion marched forward"
    result = scrub_pii(text)
    # "Tal" in "battalion" should NOT be scrubbed
    assert "battalion" in result


# ─── Combined scrubbing ─────────────────────────────────────────────────────


def test_scrubs_all_pii_types():
    """All PII types in a single message should be scrubbed."""
    text = "Squall Leonhart can be reached at squall@example.com or 555-123-4567"
    result = scrub_pii(text)
    assert "Squall Leonhart" not in result
    assert "squall@example.com" not in result
    assert "555-123-4567" not in result
    assert "[NAME]" in result
    assert "[EMAIL]" in result
    assert "[PHONE]" in result


# ─── Non-PII text passes through ────────────────────────────────────────────


def test_preserves_job_text():
    """Legitimate job-related text should pass through unchanged."""
    text = "Google is hiring a Director of Engineering in Mountain View, CA"
    result = scrub_pii(text)
    assert result == text


def test_preserves_error_messages():
    """Error messages without PII should pass through unchanged."""
    text = "LLM timeout after 120s on attempt 2/3"
    result = scrub_pii(text)
    assert result == text


def test_preserves_company_names():
    """Company names should not be scrubbed (only contact info + candidate name)."""
    text = "Oracle, Google, and Stripe are all hiring"
    result = scrub_pii(text)
    assert "Oracle" in result
    assert "Google" in result
    assert "Stripe" in result


# ─── Custom names ───────────────────────────────────────────────────────────


def test_custom_names():
    """Custom candidate names should be scrubbed."""
    text = "John Doe is applying"
    result = scrub_pii(text, candidate_names=["John Doe", "John", "Doe"])
    assert "John Doe" not in result
    assert "[NAME]" in result


def test_default_names_constant():
    """DEFAULT_CANDIDATE_NAMES should contain the expected values."""
    assert "Squall Leonhart" in DEFAULT_CANDIDATE_NAMES
    assert "Squall" in DEFAULT_CANDIDATE_NAMES
    assert "Leonhart" in DEFAULT_CANDIDATE_NAMES
