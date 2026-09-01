"""Test fetch_jds.py — JD fetching for feasible jobs only.

Uses mocked _linkedin_posting_details — no real LinkedIn calls.
Tests verify that descriptions are written to the enrichment sidecar,
not to all_jobs.json.
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pipeline.helpers import fetch_jds
from pipeline.infrastructure.enrichment_store import EnrichmentStore


def _setup_mock_details(mock_fn):
    """Set the module-level _linkedin_posting_details and delay for testing."""
    fetch_jds._linkedin_posting_details = mock_fn
    fetch_jds._LINKEDIN_REQUEST_DELAY = 0


@pytest.fixture
def store(tmp_path):
    """Fresh EnrichmentStore in a temp directory."""
    db = tmp_path / "jobs.db"
    s = EnrichmentStore(str(db))
    yield s
    s.close()


@pytest.fixture
def enriched_store(store, sample_all_jobs, tmp_path):
    """EnrichmentStore with jobs table synced + enrichments pre-populated."""
    # Sync sample data into the jobs table
    sample_path = tmp_path / "all_jobs.json"
    sample_path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))
    store.sync_jobs_db(str(sample_path), quiet=True)

    for job in sample_all_jobs["jobs"]:
        if "feasible" in job and job["feasible"] is not None:
            store.set_feasibility(
                job["url"], feasible=job["feasible"],
                feasibility=job.get("feasibility", "yes"),
            )
        # Pre-populate descriptions for jobs that already have them
        if job.get("description"):
            store.set_description(job["url"], job["description"],
                                  salary=job.get("salary"))
    return store


def test_fetches_only_feasible_without_description(sample_all_jobs_path, enriched_store):
    """Should only fetch JDs for feasible jobs without descriptions."""
    def mock_details(job_id):
        return ("$100k", f"JD for job {job_id}")

    _setup_mock_details(mock_details)
    with patch("pipeline.helpers.fetch_jds.time.sleep"):
        fetched, failed = fetch_jds.fetch_jds(sample_all_jobs_path, store=enriched_store)

    # 4 feasible jobs have descriptions already, 3 feasible don't
    assert fetched == 3
    assert failed == 0

    # Verify descriptions are in the sidecar, not all_jobs.json
    import json as _json
    data = _json.loads(open(sample_all_jobs_path).read())
    # all_jobs.json should NOT have been modified — feasible jobs without desc
    # should still lack descriptions in the file
    feasible_without_desc_in_file = [
        j for j in data["jobs"]
        if j.get("feasible") and not j.get("description")
    ]
    # The file still has the original data (3 feasible without desc)
    assert len(feasible_without_desc_in_file) == 3

    # But the sidecar should now have descriptions for all feasible jobs
    all_urls = [j["url"] for j in data["jobs"]]
    enriched = enriched_store.merge_into_jobs(data["jobs"])
    feasible_without_desc_in_store = [
        j for j in enriched if j.get("feasible") and not j.get("description")
    ]
    assert len(feasible_without_desc_in_store) == 0


def test_does_not_fetch_infeasible(sample_all_jobs_path, enriched_store):
    """Infeasible jobs should NOT be fetched."""
    _setup_mock_details(lambda job_id: ("$100k", "Should not be called for infeasible"))
    with patch("pipeline.helpers.fetch_jds.time.sleep"):
        fetch_jds.fetch_jds(sample_all_jobs_path, store=enriched_store)

    # Check that infeasible jobs were NOT given new descriptions in the sidecar
    data = json.loads(open(sample_all_jobs_path).read())
    infeasible = [j for j in data["jobs"] if j.get("feasible") is False]
    for job in infeasible:
        result = enriched_store.get(job["url"])
        # Their descriptions should be unchanged from the fixture
        assert result["description"] != "Should not be called for infeasible"


def test_does_not_refetch_existing_descriptions(sample_all_jobs_path, enriched_store):
    """Jobs with existing descriptions should NOT be re-fetched."""
    _setup_mock_details(lambda job_id: ("$999k", "New JD that should not overwrite"))
    with patch("pipeline.helpers.fetch_jds.time.sleep"):
        fetch_jds.fetch_jds(sample_all_jobs_path, store=enriched_store)

    data = json.loads(open(sample_all_jobs_path).read())
    acme = next(j for j in data["jobs"] if j["company"] == "Acme Corp")
    result = enriched_store.get(acme["url"])
    assert result["description"] == "Lead our engineering team building cloud infrastructure."


def test_handles_fetch_failure_gracefully(sample_all_jobs_path, enriched_store):
    """A fetch failure should not abort the entire run."""
    call_count = 0

    def mock_details(job_id):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Network error")
        return ("$100k", f"JD for job {job_id}")

    _setup_mock_details(mock_details)
    with patch("pipeline.helpers.fetch_jds.time.sleep"):
        fetched, failed = fetch_jds.fetch_jds(sample_all_jobs_path, store=enriched_store)

    assert failed == 1
    assert fetched == 2  # 3 to fetch, 1 failed


def test_skips_malformed_urls(sample_all_jobs_path, enriched_store):
    """Jobs with non-LinkedIn URLs should be skipped."""
    data = json.loads(open(sample_all_jobs_path).read())
    data["jobs"].append({
        "company": "BadURL", "title": "Director of Engineering",
        "location": "SF, CA", "url": "https://example.com/not-linkedin",
        "description": "", "feasible": True,
    })
    # Add feasibility for the new job to the store
    enriched_store.set_feasibility("https://example.com/not-linkedin", True, "yes")
    with open(sample_all_jobs_path, "w") as f:
        json.dump(data, f)

    _setup_mock_details(lambda job_id: ("$100k", "JD"))
    with patch("pipeline.helpers.fetch_jds.time.sleep"):
        fetch_jds.fetch_jds(sample_all_jobs_path, store=enriched_store)

    # The malformed URL job should not have been fetched
    result = enriched_store.get("https://example.com/not-linkedin")
    assert result is None or result.get("description") is None


def test_populates_salary_and_description(sample_all_jobs_path, enriched_store):
    """Both salary and description should be populated when available."""
    _setup_mock_details(lambda job_id: ("$150k-$200k", "Engineering leadership role."))
    with patch("pipeline.helpers.fetch_jds.time.sleep"):
        fetch_jds.fetch_jds(sample_all_jobs_path, store=enriched_store)

    data = json.loads(open(sample_all_jobs_path).read())
    startupx = next(j for j in data["jobs"] if j["company"] == "StartupX")
    result = enriched_store.get(startupx["url"])
    assert result["description"] == "Engineering leadership role."
    assert result["salary"] == "$150k-$200k"


def test_idempotent_on_rerun(sample_all_jobs_path, enriched_store):
    """Running twice should fetch 0 jobs on the second run."""
    _setup_mock_details(lambda job_id: ("$100k", "JD text"))
    with patch("pipeline.helpers.fetch_jds.time.sleep"):
        fetch_jds.fetch_jds(sample_all_jobs_path, store=enriched_store)

    # Second run — all feasible jobs now have descriptions in the sidecar
    _setup_mock_details(lambda job_id: ("$100k", "Should not be called"))
    with patch("pipeline.helpers.fetch_jds.time.sleep"):
        fetched, failed = fetch_jds.fetch_jds(sample_all_jobs_path, store=enriched_store)

    assert fetched == 0
    assert failed == 0


def test_no_feasible_jobs(sample_all_jobs_path, tmp_path):
    """If no feasible jobs need fetching, should return (0, 0) immediately."""
    store = EnrichmentStore(str(tmp_path / "test.db"))
    # Sync jobs into the DB table
    store.sync_jobs_db(sample_all_jobs_path, quiet=True)
    # Mark all jobs as infeasible in the sidecar
    data = json.loads(open(sample_all_jobs_path).read())
    for job in data["jobs"]:
        store.set_feasibility(job["url"], False, "no")

    mock_fn = MagicMock(return_value=("$100k", "Should not be called"))
    _setup_mock_details(mock_fn)
    with patch("pipeline.helpers.fetch_jds.time.sleep"):
        fetched, failed = fetch_jds.fetch_jds(sample_all_jobs_path, store=store)

    assert fetched == 0
    assert failed == 0
    mock_fn.assert_not_called()
    store.close()


def test_does_not_mutate_all_jobs_json(sample_all_jobs_path, enriched_store):
    """all_jobs.json should NOT be modified by fetch_jds."""
    original_content = open(sample_all_jobs_path).read()
    _setup_mock_details(lambda job_id: ("$100k", "JD text"))
    with patch("pipeline.helpers.fetch_jds.time.sleep"):
        fetch_jds.fetch_jds(sample_all_jobs_path, store=enriched_store)

    after_content = open(sample_all_jobs_path).read()
    assert original_content == after_content, "all_jobs.json was modified!"


def test_does_not_mutate_infeasible_or_unchecked(sample_all_jobs_path, enriched_store):
    """Infeasible and unchecked jobs should not get descriptions."""
    _setup_mock_details(lambda job_id: ("$100k", "JD text"))
    with patch("pipeline.helpers.fetch_jds.time.sleep"):
        fetch_jds.fetch_jds(sample_all_jobs_path, store=enriched_store)

    data = json.loads(open(sample_all_jobs_path).read())
    # The unchecked job (no feasible field) should still not have a description
    unknown = next(j for j in data["jobs"] if j["company"] == "UnknownCo")
    result = enriched_store.get(unknown["url"])
    assert result is None or result.get("description") is None
