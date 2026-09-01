"""Pydantic schema for scraper job entries (W11 — unvalidated scraper input).

Validates each job entry from all_jobs.json before it enters the pipeline.
The security-critical part is URL scheme validation: only http/https URLs
are allowed, rejecting file://, ftp://, javascript:, etc. that could be
passed to fetch_jds.py and read local files or execute malicious schemes.

Validation policy:
- Bad URL scheme → raises ValueError (security-critical, must not proceed)
- Missing required fields (url, title, company) → returns (None, error_msg)
  so the caller can log + skip the entry without crashing the pipeline
  (data-quality issue, not security)

Usage:
    from pipeline.infrastructure.job_schema import validate_job_entry
    schema, err = validate_job_entry(raw_job_dict)
    if err:
        logger.warning(f"Skipping invalid job: {err}")
        continue
    # schema is a JobSchema — use schema.url, schema.title, etc.
"""
from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator


_ALLOWED_URL_SCHEMES = {"http", "https"}


class JobSchema(BaseModel):
    """Validated job entry from the scraper's all_jobs.json.

    Required fields: url, title, company (minimum for pipeline processing).
    All other scraper fields are optional and pass through unchanged.
    """

    model_config = ConfigDict(extra="ignore")

    url: str
    title: str
    company: str
    location: str | None = None
    date_posted: str | None = None
    salary: str | None = None
    ats: str | None = None
    first_seen: str | None = None
    description: str | None = None
    feasible: bool | None = None
    feasibility: str | None = None
    feasibility_error: bool | None = None

    @field_validator("url")
    @classmethod
    def validate_url_scheme(cls, v: str) -> str:
        """Reject any URL scheme other than http/https (W11 security fix)."""
        if not v or not v.strip():
            raise ValueError("url must not be empty")
        parsed = urlparse(v)
        scheme = parsed.scheme.lower()
        if scheme not in _ALLOWED_URL_SCHEMES:
            raise ValueError(
                f"url scheme '{scheme or '(none)'}' is not allowed — "
                f"only http/https permitted (got: {v[:80]})"
            )
        return v


def validate_job_entry(raw: dict) -> tuple[JobSchema | None, str | None]:
    """Validate a raw job dict from all_jobs.json.

    Returns:
        (JobSchema, None) if valid.
        (None, error_msg) if missing required fields (data-quality — caller skips).

    Raises:
        ValueError: if the URL has a disallowed scheme (security-critical —
                    caller must not silently skip, as this indicates a
                    potentially malicious or dangerous entry).
    """
    try:
        schema = JobSchema(**raw)
    except ValidationError as e:
        # Pydantic ValidationError — check if it's a URL scheme issue
        err_str = str(e)
        if "scheme" in err_str.lower() or "url must not be empty" in err_str.lower():
            # Security-critical: bad URL scheme — raise, don't skip
            raise ValueError(f"Security: rejecting job with invalid URL: {e}") from e
        # Missing/invalid fields — data quality, caller skips
        return None, err_str
    return schema, None
