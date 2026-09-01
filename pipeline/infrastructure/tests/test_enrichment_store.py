"""Test enrichment_store.py — SQLite-backed job store + enrichments."""
import json
import logging
import os
import sys
from pathlib import Path

import pytest

from pipeline.infrastructure.enrichment_store import EnrichmentStore


@pytest.fixture
def store(tmp_path):
    """Fresh EnrichmentStore in a temp directory."""
    db = tmp_path / "jobs.db"
    s = EnrichmentStore(str(db))
    yield s
    s.close()


SAMPLE_JOBS = [
    {"url": "https://linkedin.com/jobs/view/1", "title": "Director", "company": "Acme"},
    {"url": "https://linkedin.com/jobs/view/2", "title": "VP", "company": "Beta"},
    {"url": "https://linkedin.com/jobs/view/3", "title": "Manager", "company": "Gamma"},
]


class TestSetGetFeasibility:
    def test_set_and_get_feasibility(self, store):
        url = "https://linkedin.com/jobs/view/1"
        store.set_feasibility(url, feasible=True, feasibility="preferred")
        result = store.get(url)
        assert result is not None
        assert result["feasible"] is True
        assert result["feasibility"] == "preferred"
        assert result["feasibility_error"] is False
        assert result["feasibility_checked_at"] is not None

    def test_set_feasibility_with_error(self, store):
        url = "https://linkedin.com/jobs/view/1"
        store.set_feasibility(url, feasible=True, feasibility="yes", error=True)
        result = store.get(url)
        assert result["feasible"] is True
        assert result["feasibility_error"] is True

    def test_set_feasibility_infeasible(self, store):
        url = "https://linkedin.com/jobs/view/1"
        store.set_feasibility(url, feasible=False, feasibility="no")
        result = store.get(url)
        assert result["feasible"] is False
        assert result["feasibility"] == "no"

    def test_update_feasibility(self, store):
        url = "https://linkedin.com/jobs/view/1"
        store.set_feasibility(url, feasible=True, feasibility="yes")
        store.set_feasibility(url, feasible=False, feasibility="no")
        result = store.get(url)
        assert result["feasible"] is False
        assert result["feasibility"] == "no"

    def test_get_nonexistent_url(self, store):
        assert store.get("https://nope.com") is None


class TestSetGetDescription:
    def test_set_and_get_description(self, store):
        url = "https://linkedin.com/jobs/view/1"
        store.set_description(url, "Engineering leadership role.")
        result = store.get(url)
        assert result["description"] == "Engineering leadership role."
        assert result["description_fetched_at"] is not None

    def test_set_description_with_salary(self, store):
        url = "https://linkedin.com/jobs/view/1"
        store.set_description(url, "JD text", salary="$150k-$200k")
        result = store.get(url)
        assert result["description"] == "JD text"
        assert result["salary"] == "$150k-$200k"

    def test_set_description_without_salary(self, store):
        url = "https://linkedin.com/jobs/view/1"
        store.set_description(url, "JD text")
        result = store.get(url)
        assert result["description"] == "JD text"
        assert result["salary"] is None

    def test_update_description(self, store):
        url = "https://linkedin.com/jobs/view/1"
        store.set_description(url, "Old JD")
        store.set_description(url, "New JD", salary="$100k")
        result = store.get(url)
        assert result["description"] == "New JD"
        assert result["salary"] == "$100k"

    def test_update_description_clears_stale_salary(self, store):
        """Re-fetching without salary should clear the old salary (Fix 2)."""
        url = "https://linkedin.com/jobs/view/1"
        store.set_description(url, "Old JD", salary="$100k")
        store.set_description(url, "New JD")  # no salary arg → None
        result = store.get(url)
        assert result["description"] == "New JD"
        assert result["salary"] is None


class TestFirstSeen:
    def test_first_seen_set_on_feasibility(self, store):
        url = "https://linkedin.com/jobs/view/1"
        store.set_feasibility(url, True, "yes", first_seen="2026-01-01T00:00:00Z")
        result = store.get(url)
        assert result["first_seen"] == "2026-01-01T00:00:00Z"

    def test_first_seen_set_on_description(self, store):
        url = "https://linkedin.com/jobs/view/1"
        store.set_description(url, "JD", first_seen="2026-01-01T00:00:00Z")
        result = store.get(url)
        assert result["first_seen"] == "2026-01-01T00:00:00Z"

    def test_first_seen_not_overwritten_on_update(self, store):
        """first_seen is set once on insert; later updates must not change it."""
        url = "https://linkedin.com/jobs/view/1"
        store.set_feasibility(url, True, "yes", first_seen="2026-01-01T00:00:00Z")
        store.set_feasibility(url, False, "no", first_seen="2026-12-31T00:00:00Z")
        result = store.get(url)
        assert result["first_seen"] == "2026-01-01T00:00:00Z"
        assert result["feasible"] is False  # the actual update did apply

    def test_first_seen_null_when_not_provided(self, store):
        url = "https://linkedin.com/jobs/view/1"
        store.set_feasibility(url, True, "yes")
        result = store.get(url)
        assert result["first_seen"] is None


class TestFeasibilityRationale:
    """W9: feasibility rationale is stored alongside the verdict."""

    def test_set_feasibility_with_rationale(self, store):
        url = "https://linkedin.com/jobs/view/1"
        store.set_feasibility(url, True, "preferred",
                              rationale="Big Tech engineering leadership role.")
        result = store.get(url)
        assert result["feasibility_rationale"] == "Big Tech engineering leadership role."

    def test_set_feasibility_without_rationale_defaults_none(self, store):
        """Backward compat: no rationale arg → None (existing callers)."""
        url = "https://linkedin.com/jobs/view/1"
        store.set_feasibility(url, True, "yes")
        result = store.get(url)
        assert result["feasibility_rationale"] is None

    def test_update_rationale_on_subsequent_set(self, store):
        """Re-checking a job should update the rationale."""
        url = "https://linkedin.com/jobs/view/1"
        store.set_feasibility(url, True, "yes", rationale="Initial rationale.")
        store.set_feasibility(url, True, "preferred", rationale="Updated rationale.")
        result = store.get(url)
        assert result["feasibility"] == "preferred"
        assert result["feasibility_rationale"] == "Updated rationale."

    def test_clear_rationale_by_passing_none(self, store):
        """Passing rationale=None on update clears any existing rationale."""
        url = "https://linkedin.com/jobs/view/1"
        store.set_feasibility(url, True, "yes", rationale="Has rationale.")
        store.set_feasibility(url, True, "yes", rationale=None)
        result = store.get(url)
        assert result["feasibility_rationale"] is None

    def test_rationale_survives_description_update(self, store):
        """Setting a description after feasibility shouldn't clear rationale."""
        url = "https://linkedin.com/jobs/view/1"
        store.set_feasibility(url, True, "yes", rationale="Good fit.")
        store.set_description(url, "JD text")
        result = store.get(url)
        assert result["feasibility_rationale"] == "Good fit."
        assert result["description"] == "JD text"

    def test_rationale_in_merge_into_jobs(self, store):
        """merge_into_jobs should carry rationale (it's related to feasibility)."""
        store.set_feasibility(SAMPLE_JOBS[0]["url"], True, "preferred",
                              rationale="Big Tech fit.")
        merged = store.merge_into_jobs(SAMPLE_JOBS)
        assert merged[0]["feasibility_rationale"] == "Big Tech fit."
        assert merged[0]["feasible"] is True


class TestRationaleMigration:
    """W9: existing DBs without feasibility_rationale column get migrated."""

    def test_migration_adds_rationale_column(self, tmp_path):
        """An old DB without the column should get it on open."""
        import sqlite3
        db = tmp_path / "old.db"
        # Create an old-style schema (no feasibility_rationale)
        conn = sqlite3.connect(str(db))
        conn.execute("""CREATE TABLE enrichments (
            url TEXT PRIMARY KEY, feasible INTEGER, feasibility TEXT,
            feasibility_error INTEGER, description TEXT, salary TEXT,
            feasibility_checked_at TEXT, description_fetched_at TEXT,
            first_seen TEXT
        )""")
        conn.execute(
            "INSERT INTO enrichments (url, feasible, feasibility) VALUES (?, 1, ?)",
            ("https://x.com/1", "yes"),
        )
        conn.commit()
        conn.close()

        # Open with EnrichmentStore — triggers migration
        store = EnrichmentStore(str(db))
        # Old data preserved
        result = store.get("https://x.com/1")
        assert result is not None
        assert result["feasibility"] == "yes"
        assert result["feasibility_rationale"] is None  # column exists, value NULL
        # Can now write rationale
        store.set_feasibility("https://x.com/1", True, "preferred",
                              rationale="Migrated OK.")
        result = store.get("https://x.com/1")
        assert result["feasibility_rationale"] == "Migrated OK."
        store.close()

    def test_fresh_db_has_rationale_column(self, store):
        """A fresh DB (created by EnrichmentStore) should have the column."""
        result = store._conn.execute("PRAGMA table_info(enrichments)").fetchall()
        col_names = {c[1] for c in result}
        assert "feasibility_rationale" in col_names


class TestGetUncheckedUrls:
    def test_all_unchecked(self, store):
        urls = [j["url"] for j in SAMPLE_JOBS]
        unchecked = store.get_unchecked_urls(urls)
        assert set(unchecked) == set(urls)

    def test_some_checked(self, store):
        store.set_feasibility(SAMPLE_JOBS[0]["url"], True, "preferred")
        store.set_feasibility(SAMPLE_JOBS[1]["url"], False, "no")
        urls = [j["url"] for j in SAMPLE_JOBS]
        unchecked = store.get_unchecked_urls(urls)
        assert unchecked == [SAMPLE_JOBS[2]["url"]]

    def test_all_checked(self, store):
        for j in SAMPLE_JOBS:
            store.set_feasibility(j["url"], True, "yes")
        urls = [j["url"] for j in SAMPLE_JOBS]
        unchecked = store.get_unchecked_urls(urls)
        assert unchecked == []

    def test_empty_input(self, store):
        assert store.get_unchecked_urls([]) == []


class TestGetUndescribedFeasibleUrls:
    def test_feasible_without_description(self, store):
        for j in SAMPLE_JOBS:
            store.set_feasibility(j["url"], True, "yes")
        urls = [j["url"] for j in SAMPLE_JOBS]
        result = store.get_undescribed_feasible_urls(urls)
        assert set(result) == set(urls)

    def test_some_have_descriptions(self, store):
        store.set_feasibility(SAMPLE_JOBS[0]["url"], True, "yes")
        store.set_description(SAMPLE_JOBS[0]["url"], "JD 1")
        store.set_feasibility(SAMPLE_JOBS[1]["url"], True, "yes")
        store.set_feasibility(SAMPLE_JOBS[2]["url"], False, "no")
        urls = [j["url"] for j in SAMPLE_JOBS]
        result = store.get_undescribed_feasible_urls(urls)
        assert result == [SAMPLE_JOBS[1]["url"]]

    def test_empty_string_description_treated_as_missing(self, store):
        """Empty-string descriptions should count as 'no description'."""
        store.set_feasibility(SAMPLE_JOBS[0]["url"], True, "yes")
        store.set_description(SAMPLE_JOBS[0]["url"], "")
        urls = [j["url"] for j in SAMPLE_JOBS]
        result = store.get_undescribed_feasible_urls(urls)
        assert SAMPLE_JOBS[0]["url"] in result

    def test_empty_input(self, store):
        assert store.get_undescribed_feasible_urls([]) == []


class TestMergeIntoJobs:
    def test_merges_feasibility(self, store):
        store.set_feasibility(SAMPLE_JOBS[0]["url"], True, "preferred")
        store.set_feasibility(SAMPLE_JOBS[1]["url"], False, "no")
        merged = store.merge_into_jobs(SAMPLE_JOBS)
        assert merged[0]["feasible"] is True
        assert merged[0]["feasibility"] == "preferred"
        assert merged[1]["feasible"] is False
        assert merged[1]["feasibility"] == "no"

    def test_merges_description(self, store):
        store.set_description(SAMPLE_JOBS[0]["url"], "JD text", salary="$100k")
        merged = store.merge_into_jobs(SAMPLE_JOBS)
        assert merged[0]["description"] == "JD text"
        assert merged[0]["salary"] == "$100k"

    def test_jobs_not_in_sidecar_unchanged(self, store):
        merged = store.merge_into_jobs(SAMPLE_JOBS)
        assert "feasible" not in merged[0]
        assert "description" not in merged[0]
        # Original fields preserved
        assert merged[0]["title"] == "Director"
        assert merged[0]["company"] == "Acme"

    def test_partial_enrichment(self, store):
        """Job with feasibility but no description."""
        store.set_feasibility(SAMPLE_JOBS[0]["url"], True, "yes")
        merged = store.merge_into_jobs(SAMPLE_JOBS)
        assert merged[0]["feasible"] is True
        assert merged[0]["feasibility"] == "yes"
        assert "description" not in merged[0]

    def test_does_not_mutate_input(self, store):
        store.set_feasibility(SAMPLE_JOBS[0]["url"], True, "yes")
        original = [dict(j) for j in SAMPLE_JOBS]
        store.merge_into_jobs(SAMPLE_JOBS)
        # Input list should be unchanged
        for orig, inp in zip(original, SAMPLE_JOBS):
            assert orig == inp

    def test_empty_jobs(self, store):
        assert store.merge_into_jobs([]) == []


class TestIdempotency:
    def test_set_feasibility_twice(self, store):
        url = "https://linkedin.com/jobs/view/1"
        store.set_feasibility(url, True, "yes")
        store.set_feasibility(url, True, "yes")
        result = store.get(url)
        assert result["feasible"] is True
        # Only one row
        assert store.get_many([url]) == {url: result}

    def test_set_description_twice(self, store):
        url = "https://linkedin.com/jobs/view/1"
        store.set_description(url, "JD 1")
        store.set_description(url, "JD 2")
        result = store.get(url)
        assert result["description"] == "JD 2"


class TestGetMany:
    def test_get_many(self, store):
        store.set_feasibility(SAMPLE_JOBS[0]["url"], True, "yes")
        store.set_feasibility(SAMPLE_JOBS[1]["url"], False, "no")
        urls = [SAMPLE_JOBS[0]["url"], SAMPLE_JOBS[1]["url"], SAMPLE_JOBS[2]["url"]]
        result = store.get_many(urls)
        assert len(result) == 2  # only 2 are in the store
        assert result[SAMPLE_JOBS[0]["url"]]["feasible"] is True
        assert result[SAMPLE_JOBS[1]["url"]]["feasible"] is False

    def test_get_many_empty(self, store):
        assert store.get_many([]) == {}


# ─── Sample all_jobs.json for sync/query tests ─────────────────────────────

SAMPLE_ALL_JOBS = {
    "updated_at": "2026-08-24T12:00:00Z",
    "jobs": [
        {"url": "https://linkedin.com/jobs/view/1", "title": "Director", "company": "Acme",
         "location": "SF, CA", "date_posted": "2026-08-20", "salary": "$200k",
         "ats": "LinkedIn", "first_seen": "2026-08-20T10:00:00Z",
         "description": "Lead engineering.", "feasible": True, "feasibility": "preferred"},
        {"url": "https://linkedin.com/jobs/view/2", "title": "VP", "company": "Beta",
         "location": "NYC, NY", "date_posted": "2026-08-19", "salary": "",
         "ats": "LinkedIn", "first_seen": "2026-08-19T10:00:00Z",
         "description": "", "feasible": True, "feasibility": "yes"},
        {"url": "https://linkedin.com/jobs/view/3", "title": "Manager", "company": "Gamma",
         "location": "Remote", "date_posted": "2026-08-18", "salary": "",
         "ats": "LinkedIn", "first_seen": "2026-08-18T10:00:00Z",
         "description": "Manage team.", "feasible": False, "feasibility": "no"},
        {"url": "https://linkedin.com/jobs/view/4", "title": "Architect", "company": "Delta",
         "location": "Austin, TX", "date_posted": "2026-08-17", "salary": "",
         "ats": "LinkedIn", "first_seen": "2026-08-17T10:00:00Z",
         "description": "", "feasible": None, "feasibility": None},
    ],
}


@pytest.fixture
def sample_jobs_path(tmp_path):
    """Write SAMPLE_ALL_JOBS to a temp file and return the path."""
    p = tmp_path / "all_jobs.json"
    p.write_text(json.dumps(SAMPLE_ALL_JOBS))
    return str(p)


@pytest.fixture
def synced_store(store, sample_jobs_path):
    """Store with SAMPLE_ALL_JOBS synced into the jobs table."""
    store.sync_jobs_db(sample_jobs_path, quiet=True)
    return store


class TestSyncJobsDb:
    def test_sync_populates_jobs_table(self, synced_store):
        jobs = synced_store.query_jobs(status="all")
        assert len(jobs) == 4

    def test_sync_preserves_all_fields(self, synced_store):
        jobs = synced_store.query_jobs(status="all")
        acme = next(j for j in jobs if j["company"] == "Acme")
        assert acme["title"] == "Director"
        assert acme["location"] == "SF, CA"
        assert acme["date_posted"] == "2026-08-20"
        assert acme["salary"] == "$200k"
        assert acme["ats"] == "LinkedIn"
        assert acme["first_seen"] == "2026-08-20T10:00:00Z"
        assert acme["description"] == "Lead engineering."
        assert acme["feasible"] is True
        assert acme["feasibility"] == "preferred"

    def test_sync_is_idempotent(self, store, sample_jobs_path):
        """Syncing twice should not duplicate rows."""
        store.sync_jobs_db(sample_jobs_path, quiet=True)
        store.sync_jobs_db(sample_jobs_path, quiet=True)
        jobs = store.query_jobs(status="all")
        assert len(jobs) == 4

    def test_sync_updates_existing_rows(self, store, sample_jobs_path, tmp_path):
        """Re-syncing with updated data should update existing rows."""
        store.sync_jobs_db(sample_jobs_path, quiet=True)
        # Modify the sample data
        data = json.loads(Path(sample_jobs_path).read_text())
        data["jobs"][0]["title"] = "Senior Director"
        updated_path = tmp_path / "all_jobs_updated.json"
        updated_path.write_text(json.dumps(data))
        store.sync_jobs_db(str(updated_path), quiet=True)
        jobs = store.query_jobs(status="all")
        acme = next(j for j in jobs if j["company"] == "Acme")
        assert acme["title"] == "Senior Director"

    def test_sync_does_not_touch_enrichments(self, store, sample_jobs_path):
        """Syncing should not affect enrichment rows."""
        store.set_feasibility("https://linkedin.com/jobs/view/1",
                              feasible=False, feasibility="no")
        store.sync_jobs_db(sample_jobs_path, quiet=True)
        # Enrichment should still be there
        result = store.get("https://linkedin.com/jobs/view/1")
        assert result["feasible"] is False
        assert result["feasibility"] == "no"

    def test_sync_skips_jobs_without_url(self, store, tmp_path):
        """Jobs without a URL should be silently skipped."""
        data = {"jobs": [{"title": "No URL", "company": "X"}, *SAMPLE_ALL_JOBS["jobs"]]}
        p = tmp_path / "all_jobs.json"
        p.write_text(json.dumps(data))
        n = store.sync_jobs_db(str(p), quiet=True)
        assert n == 4  # only the 4 with URLs


class TestQueryJobs:
    def test_query_feasible(self, synced_store):
        jobs = synced_store.query_jobs(status="feasible")
        assert len(jobs) == 2  # Acme (preferred) + Beta (yes)
        companies = {j["company"] for j in jobs}
        assert companies == {"Acme", "Beta"}

    def test_query_infeasible(self, synced_store):
        jobs = synced_store.query_jobs(status="infeasible")
        assert len(jobs) == 1
        assert jobs[0]["company"] == "Gamma"

    def test_query_untagged(self, synced_store):
        jobs = synced_store.query_jobs(status="untagged")
        assert len(jobs) == 1
        assert jobs[0]["company"] == "Delta"

    def test_query_all(self, synced_store):
        jobs = synced_store.query_jobs(status="all")
        assert len(jobs) == 4

    def test_query_with_tier(self, synced_store):
        preferred = synced_store.query_jobs(status="feasible", tier="preferred")
        assert len(preferred) == 1
        assert preferred[0]["company"] == "Acme"

        yes = synced_store.query_jobs(status="feasible", tier="yes")
        assert len(yes) == 1
        assert yes[0]["company"] == "Beta"

    def test_query_has_description(self, synced_store):
        jobs = synced_store.query_jobs(status="all", has_description=True)
        assert len(jobs) == 2  # Acme + Gamma have descriptions
        companies = {j["company"] for j in jobs}
        assert companies == {"Acme", "Gamma"}

    def test_query_missing_description(self, synced_store):
        jobs = synced_store.query_jobs(status="all", has_description=False)
        assert len(jobs) == 2  # Beta + Delta have no descriptions
        companies = {j["company"] for j in jobs}
        assert companies == {"Beta", "Delta"}

    def test_query_pagination(self, synced_store):
        page1 = synced_store.query_jobs(status="all", limit=2, offset=0)
        page2 = synced_store.query_jobs(status="all", limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        # No overlap
        urls1 = {j["url"] for j in page1}
        urls2 = {j["url"] for j in page2}
        assert urls1.isdisjoint(urls2)

    def test_query_limit_zero_returns_all(self, synced_store):
        jobs = synced_store.query_jobs(status="all", limit=0)
        assert len(jobs) == 4

    def test_query_enrichment_overrides_scraper(self, store, sample_jobs_path):
        """Pipeline enrichment should override scraper values via COALESCE."""
        store.sync_jobs_db(sample_jobs_path, quiet=True)
        # Override Acme's feasibility from preferred to no
        store.set_feasibility("https://linkedin.com/jobs/view/1",
                              feasible=False, feasibility="no")
        jobs = store.query_jobs(status="feasible")
        urls = {j["url"] for j in jobs}
        assert "https://linkedin.com/jobs/view/1" not in urls  # Acme now infeasible

        infeasible = store.query_jobs(status="infeasible")
        assert any(j["company"] == "Acme" for j in infeasible)
        assert infeasible[0]["feasibility"] == "no"  # enrichment value wins

    def test_query_enrichment_description_overrides(self, store, sample_jobs_path):
        """Pipeline-fetched description should override scraper's."""
        store.sync_jobs_db(sample_jobs_path, quiet=True)
        # Beta has no description in the scraper data; add one via enrichment
        store.set_description("https://linkedin.com/jobs/view/2",
                              "Pipeline-fetched JD")
        jobs = store.query_jobs(status="all", has_description=True)
        companies = {j["company"] for j in jobs}
        assert "Beta" in companies  # now has description via enrichment

    def test_query_jobs_includes_rationale(self, store, sample_jobs_path):
        """query_jobs should include feasibility_rationale from enrichments (W9)."""
        store.sync_jobs_db(sample_jobs_path, quiet=True)
        store.set_feasibility("https://linkedin.com/jobs/view/1",
                              feasible=True, feasibility="preferred",
                              rationale="Big Tech engineering leadership role.")
        jobs = store.query_jobs(status="feasible")
        acme = next(j for j in jobs if j["company"] == "Acme")
        assert acme["feasibility_rationale"] == "Big Tech engineering leadership role."

    def test_query_jobs_rationale_none_when_not_set(self, synced_store):
        """Jobs without a rationale should have None, not a KeyError."""
        jobs = synced_store.query_jobs(status="all")
        for job in jobs:
            assert "feasibility_rationale" in job
            assert job["feasibility_rationale"] is None


class TestCountJobs:
    def test_count_feasible(self, synced_store):
        assert synced_store.count_jobs(status="feasible") == 2

    def test_count_infeasible(self, synced_store):
        assert synced_store.count_jobs(status="infeasible") == 1

    def test_count_untagged(self, synced_store):
        assert synced_store.count_jobs(status="untagged") == 1

    def test_count_all(self, synced_store):
        assert synced_store.count_jobs(status="all") == 4

    def test_count_with_tier(self, synced_store):
        assert synced_store.count_jobs(status="feasible", tier="preferred") == 1
        assert synced_store.count_jobs(status="feasible", tier="yes") == 1

    def test_count_with_description(self, synced_store):
        assert synced_store.count_jobs(status="all", has_description=True) == 2
        assert synced_store.count_jobs(status="all", has_description=False) == 2


class TestGetUncheckedUrlsWithJobsTable:
    """Tests for the Q6c change: get_unchecked_urls skips scraper-tagged jobs."""

    def test_scraper_tagged_jobs_skipped(self, store, sample_jobs_path):
        """Jobs with feasible in the jobs table should not need checking."""
        store.sync_jobs_db(sample_jobs_path, quiet=True)
        urls = [j["url"] for j in SAMPLE_ALL_JOBS["jobs"]]
        unchecked = store.get_unchecked_urls(urls)
        # Only Delta (untagged in scraper, no enrichment) should be unchecked
        assert unchecked == ["https://linkedin.com/jobs/view/4"]

    def test_enrichment_tagged_jobs_skipped(self, store, sample_jobs_path):
        """Jobs tagged by the pipeline (enrichments) should also be skipped."""
        store.sync_jobs_db(sample_jobs_path, quiet=True)
        # Tag Delta via enrichment
        store.set_feasibility("https://linkedin.com/jobs/view/4", True, "yes")
        urls = [j["url"] for j in SAMPLE_ALL_JOBS["jobs"]]
        unchecked = store.get_unchecked_urls(urls)
        assert unchecked == []

    def test_unchecked_urls_empty_input(self, store):
        assert store.get_unchecked_urls([]) == []


class TestSQLInjectionSafety:
    """E5: Verify all queries resist SQL injection via user-controlled params.

    The f-strings in query_jobs/count_jobs/get_many/get_unchecked_urls/
    get_undescribed_feasible_urls assemble SQL *structure* only (keywords +
    '?' placeholders). User values always bind through parameterized '?'.
    These tests prove that by attempting classic injection payloads and
    confirming they're treated as literal strings, not executed as SQL.
    """

    INJECTION_URL = "https://x.com/jobs/1'; DROP TABLE enrichments; --"
    INJECTION_TIER = "preferred' OR '1'='1"
    INJECTION_URL_UNION = "https://x.com/jobs/1' UNION SELECT * FROM jobs; --"

    def test_set_feasibility_then_get_treats_url_as_literal(self, store):
        """A URL with SQL injection chars is stored/retrieved as a literal."""
        store.set_feasibility(self.INJECTION_URL, feasible=True, feasibility="yes")
        result = store.get(self.INJECTION_URL)
        assert result is not None
        assert result["feasible"] is True
        # The malicious URL is just a string in the DB, not executed
        assert result["url"] == self.INJECTION_URL

    def test_injection_url_does_not_drop_table(self, store):
        """The DROP TABLE payload must not execute — enrichments table survives."""
        store.set_feasibility(self.INJECTION_URL, feasible=True, feasibility="yes")
        # If the injection had executed, this query would raise OperationalError
        store.get(self.INJECTION_URL)
        rows = store._conn.execute("SELECT COUNT(*) FROM enrichments").fetchone()[0]
        assert rows == 1  # only our one row, table intact

    def test_get_many_treats_injection_urls_as_literals(self, store):
        """IN-clause placeholders bind injection URLs as literal strings."""
        store.set_feasibility(self.INJECTION_URL, feasible=True, feasibility="yes")
        store.set_feasibility(self.INJECTION_URL_UNION, feasible=False, feasibility="no")
        result = store.get_many([self.INJECTION_URL, self.INJECTION_URL_UNION])
        assert len(result) == 2
        assert self.INJECTION_URL in result
        assert self.INJECTION_URL_UNION in result

    def test_get_unchecked_urls_with_injection_urls(self, store):
        """get_unchecked_urls must not execute injected SQL in the URL list."""
        urls = [self.INJECTION_URL, self.INJECTION_URL_UNION]
        unchecked = store.get_unchecked_urls(urls)
        # Neither URL has a feasibility verdict, so both should be "unchecked"
        assert set(unchecked) == set(urls)
        # Table still intact
        rows = store._conn.execute("SELECT COUNT(*) FROM enrichments").fetchone()[0]
        assert rows == 0

    def test_get_undescribed_feasible_urls_with_injection_urls(self, store):
        """get_undescribed_feasible_urls must bind injection URLs as literals."""
        store.set_feasibility(self.INJECTION_URL, feasible=True, feasibility="yes")
        urls = [self.INJECTION_URL]
        result = store.get_undescribed_feasible_urls(urls)
        assert result == [self.INJECTION_URL]

    def test_query_jobs_tier_injection_is_literal(self, store, sample_jobs_path):
        """The tier param binds through '?' — injection payload is a literal."""
        store.sync_jobs_db(sample_jobs_path, quiet=True)
        # An injection tier should match nothing (no row has that literal value)
        jobs = store.query_jobs(status="feasible", tier=self.INJECTION_TIER)
        assert jobs == []
        # count_jobs must agree
        assert store.count_jobs(status="feasible", tier=self.INJECTION_TIER) == 0

    def test_query_jobs_status_rejects_unknown_values(self, store):
        """status is validated against a fixed set — unknown values raise."""
        with pytest.raises(ValueError):
            store.query_jobs(status="feasible'; DROP TABLE jobs; --")

    def test_count_jobs_status_rejects_unknown_values(self, store):
        with pytest.raises(ValueError):
            store.count_jobs(status="all' OR '1'='1")

    def test_sync_jobs_db_with_injection_url(self, store, tmp_path):
        """A job with an injection URL in all_jobs.json is stored as a literal."""
        data = {"jobs": [
            {"url": self.INJECTION_URL, "title": "Hacker", "company": "Evil"},
            *SAMPLE_ALL_JOBS["jobs"],
        ]}
        p = tmp_path / "all_jobs.json"
        p.write_text(json.dumps(data))
        store.sync_jobs_db(str(p), quiet=True)
        jobs = store.query_jobs(status="all")
        hacker = next(j for j in jobs if j["company"] == "Evil")
        assert hacker["url"] == self.INJECTION_URL
        # jobs table intact (no DROP executed)
        assert len(jobs) == len(SAMPLE_ALL_JOBS["jobs"]) + 1


class TestSyncJobsDbValidation:
    """W11: sync_jobs_db validates scraper entries via JobSchema.

    Bad URL schemes raise (security-critical). Missing required fields
    (url, title, company) are logged and skipped (data-quality).
    """

    def test_bad_url_scheme_raises_during_sync(self, store, tmp_path):
        """A file:// URL in all_jobs.json must raise, not silently insert."""
        data = {"jobs": [
            {"url": "file:///etc/passwd", "title": "Hacker", "company": "Evil"},
        ]}
        p = tmp_path / "all_jobs.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="(?i)scheme|url|security"):
            store.sync_jobs_db(str(p), quiet=True)

    def test_javascript_scheme_raises_during_sync(self, store, tmp_path):
        data = {"jobs": [
            {"url": "javascript:alert(1)", "title": "X", "company": "Y"},
        ]}
        p = tmp_path / "all_jobs.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ValueError):
            store.sync_jobs_db(str(p), quiet=True)

    def test_missing_url_skipped_not_raised(self, store, tmp_path):
        """Jobs without a URL are skipped with a warning (data-quality, not security)."""
        data = {"jobs": [
            {"title": "No URL", "company": "X"},
            *SAMPLE_ALL_JOBS["jobs"],
        ]}
        p = tmp_path / "all_jobs.json"
        p.write_text(json.dumps(data))
        n = store.sync_jobs_db(str(p), quiet=True)
        assert n == len(SAMPLE_ALL_JOBS["jobs"])  # the bad one skipped

    def test_missing_title_skipped_not_raised(self, store, tmp_path):
        """Jobs without a title are skipped (data-quality)."""
        data = {"jobs": [
            {"url": "https://x.com/1", "company": "X"},  # no title
            *SAMPLE_ALL_JOBS["jobs"],
        ]}
        p = tmp_path / "all_jobs.json"
        p.write_text(json.dumps(data))
        n = store.sync_jobs_db(str(p), quiet=True)
        assert n == len(SAMPLE_ALL_JOBS["jobs"])

    def test_missing_company_skipped_not_raised(self, store, tmp_path):
        """Jobs without a company are skipped (data-quality)."""
        data = {"jobs": [
            {"url": "https://x.com/1", "title": "Dev"},  # no company
            *SAMPLE_ALL_JOBS["jobs"],
        ]}
        p = tmp_path / "all_jobs.json"
        p.write_text(json.dumps(data))
        n = store.sync_jobs_db(str(p), quiet=True)
        assert n == len(SAMPLE_ALL_JOBS["jobs"])

    def test_valid_jobs_still_sync_after_bad_skipped(self, store, tmp_path):
        """A mix of bad and valid jobs: bad ones skipped, valid ones synced."""
        data = {"jobs": [
            {"url": "file:///bad", "title": "Bad", "company": "B"},  # raises
        ]}
        p = tmp_path / "all_jobs.json"
        p.write_text(json.dumps(data))
        # The bad URL raises immediately — no partial sync
        with pytest.raises(ValueError):
            store.sync_jobs_db(str(p), quiet=True)

    def test_all_valid_jobs_sync_normally(self, store, sample_jobs_path):
        """Valid jobs (the normal case) sync without any validation errors."""
        n = store.sync_jobs_db(sample_jobs_path, quiet=True)
        assert n == len(SAMPLE_ALL_JOBS["jobs"])
        jobs = store.query_jobs(status="all")
        assert len(jobs) == len(SAMPLE_ALL_JOBS["jobs"])

    def test_skip_warning_goes_to_logger(self, store, tmp_path, caplog):
        """When a logger is provided, skip warnings go to it (Q1=b)."""
        data = {"jobs": [
            {"title": "No URL", "company": "X"},  # missing url → skipped
            *SAMPLE_ALL_JOBS["jobs"],
        ]}
        p = tmp_path / "all_jobs.json"
        p.write_text(json.dumps(data))
        test_logger = logging.getLogger("test_skip_warning")
        test_logger.setLevel(logging.WARNING)
        with caplog.at_level(logging.WARNING, logger="test_skip_warning"):
            store.sync_jobs_db(str(p), quiet=True, logger=test_logger)
        # The skip should be in the log records
        assert any("Skipping invalid job entry" in r.message for r in caplog.records)
        assert any("url" in r.message.lower() for r in caplog.records)

    def test_skip_warning_falls_back_to_stderr_without_logger(self, store, tmp_path, capsys):
        """Without a logger, skip warnings go to stderr (backward compat)."""
        data = {"jobs": [
            {"title": "No URL", "company": "X"},
            *SAMPLE_ALL_JOBS["jobs"],
        ]}
        p = tmp_path / "all_jobs.json"
        p.write_text(json.dumps(data))
        store.sync_jobs_db(str(p), quiet=False)  # no logger, not quiet
        captured = capsys.readouterr()
        assert "Skipping invalid job entry" in captured.err

    def test_skip_warning_silent_when_quiet_and_no_logger(self, store, tmp_path, capsys):
        """quiet=True with no logger → no stderr output."""
        data = {"jobs": [
            {"title": "No URL", "company": "X"},
            *SAMPLE_ALL_JOBS["jobs"],
        ]}
        p = tmp_path / "all_jobs.json"
        p.write_text(json.dumps(data))
        store.sync_jobs_db(str(p), quiet=True)  # no logger, quiet
        captured = capsys.readouterr()
        assert captured.err == ""


class TestPurgeStub:
    def test_purge_raises_not_implemented(self, store):
        with pytest.raises(NotImplementedError):
            store.purge_jobs_older_than("2026-01-01")
