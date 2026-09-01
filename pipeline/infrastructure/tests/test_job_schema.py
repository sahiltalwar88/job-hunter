"""Tests for job_schema.py — Pydantic validation of scraper job entries (W11).

Validates that job entries from all_jobs.json are schema-conformant before
entering the pipeline. URL scheme validation is the security-critical part
(reject file://, ftp://, javascript: etc.). Missing required fields are a
data-quality concern — logged and skipped, not raised.
"""
import pytest
from pydantic import ValidationError

from pipeline.infrastructure.job_schema import JobSchema, validate_job_entry


class TestJobSchemaValid:
    def test_minimal_valid_job(self):
        """Minimum required fields: url, title, company."""
        job = JobSchema(url="https://linkedin.com/jobs/1", title="Director", company="Acme")
        assert job.url == "https://linkedin.com/jobs/1"
        assert job.title == "Director"
        assert job.company == "Acme"

    def test_full_valid_job(self):
        """All fields populated."""
        job = JobSchema(
            url="https://linkedin.com/jobs/1",
            title="Director",
            company="Acme",
            location="SF, CA",
            date_posted="2026-08-20",
            salary="$200k",
            ats="LinkedIn",
            first_seen="2026-08-20T10:00:00Z",
            description="Lead engineering.",
            feasible=True,
            feasibility="preferred",
        )
        assert job.location == "SF, CA"
        assert job.feasible is True

    def test_http_url_passes(self):
        job = JobSchema(url="http://example.com/jobs/1", title="Dev", company="Co")
        assert job.url.startswith("http://")

    def test_https_url_passes(self):
        job = JobSchema(url="https://example.com/jobs/1", title="Dev", company="Co")
        assert job.url.startswith("https://")


class TestJobSchemaMissingFields:
    """Missing required fields raise ValidationError (Pydantic behavior)."""

    def test_missing_url_raises(self):
        with pytest.raises(ValidationError):
            JobSchema(title="Director", company="Acme")

    def test_missing_title_raises(self):
        with pytest.raises(ValidationError):
            JobSchema(url="https://x.com/1", company="Acme")

    def test_missing_company_raises(self):
        with pytest.raises(ValidationError):
            JobSchema(url="https://x.com/1", title="Director")


class TestJobSchemaUrlSchemeValidation:
    """Security-critical: reject non-http(s) URL schemes (W11)."""

    @pytest.mark.parametrize("bad_url", [
        "file:///etc/passwd",
        "ftp://ftp.example.com/jobs/1",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ssh://git@github.com/repo",
        "gopher://gopher.example.com/",
        "dict://dict.org/",
    ])
    def test_bad_scheme_raises(self, bad_url):
        with pytest.raises(ValidationError) as exc_info:
            JobSchema(url=bad_url, title="Dev", company="Co")
        assert "url" in str(exc_info.value).lower() or "scheme" in str(exc_info.value).lower()

    def test_empty_url_raises(self):
        with pytest.raises(ValidationError):
            JobSchema(url="", title="Dev", company="Co")

    def test_url_without_scheme_raises(self):
        """Bare paths like '/etc/passwd' must not pass."""
        with pytest.raises(ValidationError):
            JobSchema(url="/etc/passwd", title="Dev", company="Co")


class TestValidateJobEntry:
    """validate_job_entry wraps JobSchema validation and returns the model or None.

    Returns (JobSchema | None, error_msg | None):
    - Valid job → (JobSchema, None)
    - Missing required field → (None, error_msg) — data quality, caller skips
    - Bad URL scheme → raises ValueError — security-critical, caller must raise
    """

    def test_valid_entry_returns_schema(self):
        raw = {"url": "https://linkedin.com/jobs/1", "title": "Director", "company": "Acme"}
        schema, err = validate_job_entry(raw)
        assert schema is not None
        assert err is None
        assert schema.url == "https://linkedin.com/jobs/1"

    def test_missing_url_returns_none_with_error(self):
        raw = {"title": "Director", "company": "Acme"}
        schema, err = validate_job_entry(raw)
        assert schema is None
        assert err is not None
        assert "url" in err.lower()

    def test_missing_title_returns_none_with_error(self):
        raw = {"url": "https://x.com/1", "company": "Acme"}
        schema, err = validate_job_entry(raw)
        assert schema is None
        assert err is not None
        assert "title" in err.lower()

    def test_missing_company_returns_none_with_error(self):
        raw = {"url": "https://x.com/1", "title": "Director"}
        schema, err = validate_job_entry(raw)
        assert schema is None
        assert err is not None
        assert "company" in err.lower()

    def test_bad_url_scheme_raises_value_error(self):
        """Security-critical: bad URL schemes raise, not return None."""
        raw = {"url": "file:///etc/passwd", "title": "Dev", "company": "Co"}
        with pytest.raises(ValueError) as exc_info:
            validate_job_entry(raw)
        assert "scheme" in str(exc_info.value).lower() or "url" in str(exc_info.value).lower()

    def test_bad_url_scheme_raises_not_returns_none(self):
        """Ensure the security case raises, not silently skips."""
        raw = {"url": "javascript:alert(1)", "title": "Dev", "company": "Co"}
        with pytest.raises(ValueError):
            validate_job_entry(raw)

    def test_extra_fields_ignored(self):
        """Unknown fields from the scraper are silently ignored (not rejected)."""
        raw = {
            "url": "https://x.com/1", "title": "Dev", "company": "Co",
            "unknown_field": "whatever", "duplicate_urls": ["https://x.com/2"],
        }
        schema, err = validate_job_entry(raw)
        assert schema is not None
        assert err is None
