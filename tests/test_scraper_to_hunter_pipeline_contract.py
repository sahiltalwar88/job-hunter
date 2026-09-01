"""Test the pipeline contract between scraper and job-hunter.

Verifies that all_jobs.json has the expected schema and that feasible
filtering works correctly.
"""
import json
import pytest


def test_all_jobs_has_required_top_level_keys(sample_all_jobs):
    """all_jobs.json must have 'updated_at' and 'jobs' keys."""
    assert "updated_at" in sample_all_jobs
    assert "jobs" in sample_all_jobs
    assert isinstance(sample_all_jobs["jobs"], list)


def test_each_job_has_required_fields(sample_all_jobs):
    """Each job must have url, title, company, location."""
    for job in sample_all_jobs["jobs"]:
        assert "url" in job, f"Job missing url: {job}"
        assert "title" in job, f"Job missing title: {job}"
        assert "company" in job, f"Job missing company: {job}"
        assert "location" in job, f"Job missing location: {job}"


def test_feasible_field_is_boolean_when_present(sample_all_jobs):
    """feasible field should be a boolean when present."""
    for job in sample_all_jobs["jobs"]:
        if "feasible" in job:
            assert isinstance(job["feasible"], bool), (
                f"feasible is {type(job['feasible'])} for {job['title']}, expected bool"
            )


def test_feasible_true_filtering(sample_all_jobs):
    """Filtering to feasible:true should work correctly."""
    feasible = [j for j in sample_all_jobs["jobs"] if j.get("feasible") is True]
    assert len(feasible) == 7  # 7 feasible jobs in the fixture


def test_feasible_false_excluded(sample_all_jobs):
    """feasible:false jobs should be excluded from the feasible filter."""
    feasible = [j for j in sample_all_jobs["jobs"] if j.get("feasible") is True]
    infeasible = [j for j in sample_all_jobs["jobs"] if j.get("feasible") is False]
    # No overlap
    feasible_urls = {j["url"] for j in feasible}
    infeasible_urls = {j["url"] for j in infeasible}
    assert feasible_urls.isdisjoint(infeasible_urls)


def test_unchecked_jobs_distinguishable(sample_all_jobs):
    """Jobs without feasible field should be distinguishable from feasible:false."""
    unchecked = [j for j in sample_all_jobs["jobs"] if "feasible" not in j]
    infeasible = [j for j in sample_all_jobs["jobs"] if j.get("feasible") is False]
    assert len(unchecked) == 1  # UnknownCo
    assert len(infeasible) == 2  # SalesForce, HardwareCo
    # They should be different sets
    unchecked_urls = {j["url"] for j in unchecked}
    infeasible_urls = {j["url"] for j in infeasible}
    assert unchecked_urls.isdisjoint(infeasible_urls)


def test_no_duplicate_urls(sample_all_jobs):
    """No two jobs in the sample should have the same URL."""
    urls = [j["url"] for j in sample_all_jobs["jobs"]]
    assert len(urls) == len(set(urls)), "Duplicate URLs found"


def test_linkedin_urls_have_job_ids(sample_all_jobs):
    """LinkedIn URLs should contain a numeric job ID for JD fetching."""
    import re
    for job in sample_all_jobs["jobs"]:
        url = job.get("url", "")
        if "linkedin.com" in url:
            assert re.search(r'/jobs/view/(\d+)', url), (
                f"LinkedIn URL missing job ID: {url}"
            )


def test_feasibility_tier_field_present_when_tagged(sample_all_jobs):
    """Tagged jobs should have a feasibility tier field (preferred/yes/no)."""
    for job in sample_all_jobs["jobs"]:
        if "feasible" in job:
            assert "feasibility" in job, (
                f"Job {job['title']} has feasible but no feasibility tier"
            )
            assert job["feasibility"] in ("preferred", "yes", "no"), (
                f"Job {job['title']} has invalid tier: {job['feasibility']}"
            )


def test_feasibility_tier_consistent_with_feasible(sample_all_jobs):
    """feasibility=preferred/yes → feasible=True; feasibility=no → feasible=False."""
    for job in sample_all_jobs["jobs"]:
        if "feasibility" in job:
            tier = job["feasibility"]
            feasible = job["feasible"]
            if tier in ("preferred", "yes"):
                assert feasible is True, (
                    f"{job['title']}: tier={tier} but feasible={feasible}"
                )
            elif tier == "no":
                assert feasible is False, (
                    f"{job['title']}: tier=no but feasible={feasible}"
                )


def test_list_feasible_jobs_tier_filter(sample_all_jobs, tmp_path, monkeypatch):
    """list_feasible_jobs.py --tier should filter by feasibility tier."""

    # Write sample to a temp file and point the script at it
    tmp_jobs = tmp_path / "all_jobs.json"
    tmp_jobs.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))
    tmp_db = tmp_path / "jobs.db"
    monkeypatch.setenv("ALL_JOBS_PATH", str(tmp_jobs))
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_db))

    # Import the helper module directly
    from pipeline.helpers import list_feasible_jobs
    # Override module-level paths (evaluated at import time, so monkeypatch
    # of env vars alone isn't enough)
    list_feasible_jobs.ALL_JOBS = str(tmp_jobs)
    list_feasible_jobs.JOBS_DB = str(tmp_db)
    list_feasible_jobs._last_sync_mtime = 0.0  # force sync

    from pipeline.helpers.list_feasible_jobs import filter_jobs, load_jobs

    jobs = load_jobs(str(tmp_jobs))

    # --tier preferred → only Acme Corp (Director of Engineering)
    preferred = filter_jobs(jobs, status="feasible", tier="preferred")
    assert len(preferred) == 1
    assert preferred[0]["company"] == "Acme Corp"

    # --tier yes → 6 jobs (all feasible except preferred and the untagged one)
    yes_jobs = filter_jobs(jobs, status="feasible", tier="yes")
    assert len(yes_jobs) == 6

    # --tier no → 0 jobs (infeasible jobs have tier="no" but status=feasible filters them out)
    no_jobs = filter_jobs(jobs, status="feasible", tier="no")
    assert len(no_jobs) == 0

    # --status infeasible --tier no → 2 jobs
    no_infeasible = filter_jobs(jobs, status="infeasible", tier="no")
    assert len(no_infeasible) == 2
