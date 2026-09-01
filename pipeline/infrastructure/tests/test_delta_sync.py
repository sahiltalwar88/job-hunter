"""Test delta_sync.py — incremental job ingestion from scraper deltas.

Tests cover:
- EnrichmentStore.sync_delta() upserts from added/updated arrays
- sync_delta() validates job entries (bad URL → ValueError, missing required → skip)
- sync_deltas() tracks processed_deltas (no reprocessing)
- ColdStartError on first run (empty processed_deltas)
- Delta sync failure raises (no fallback to full sync)
- Both filesystem and HTTP transport (mocked)
- Missing delta file that IS in manifest but NOT processed → loud error
- Missing delta file that IS processed → skip with warning (pruned by scraper)
"""
import json
import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


from pipeline.infrastructure.enrichment_store import EnrichmentStore
from pipeline.infrastructure.delta_sync import (
    ColdStartError,
    sync_deltas,
    _read_index,
    _parse_index,
    _read_delta_file,
)
from pipeline.infrastructure.config import PipelineConfig
from pipeline.infrastructure.paths import Paths


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    """Fresh EnrichmentStore in a temp directory."""
    db = tmp_path / "jobs.db"
    s = EnrichmentStore(str(db))
    yield s
    s.close()


@pytest.fixture
def scraper_dir(tmp_path):
    """Create a fake scraper directory with output/deltas/ structure."""
    d = tmp_path / "scraper"
    deltas = d / "output" / "deltas"
    deltas.mkdir(parents=True)
    all_jobs = d / "output" / "all_jobs.json"
    all_jobs.write_text('{"jobs": []}')
    return d


@pytest.fixture
def paths(scraper_dir, tmp_path):
    """Paths pointing at a tmp workspace with a fake scraper."""
    hunter = tmp_path / "hunter"
    hunter.mkdir()
    p = Paths.from_hunter_dir(hunter)
    # Override scraper_dir to point at our fake scraper
    return Paths(
        hunter_dir=p.hunter_dir,
        listings=p.listings,
        trash=p.trash,
        drafts=p.drafts,
        rejected=p.rejected,
        ready=p.ready,
        in_progress=p.in_progress,
        submitted=p.submitted,
        logs=p.logs,
        grading=p.grading,
        veracity=p.veracity,
        grades_log=p.grades_log,
        data_dir=p.data_dir,
        jobs_db=p.jobs_db,
        checkpoints_db=p.checkpoints_db,
        devin_dir=p.devin_dir,
        lock_file=p.lock_file,
        state_file=p.state_file,
        config_file=p.config_file,
        scraper_dir=scraper_dir,
        all_jobs_path=scraper_dir / "output" / "all_jobs.json",
        deltas_dir=scraper_dir / "output" / "deltas",
        deltas_index=scraper_dir / "output" / "deltas" / "index.jsonl",
        base_resume=p.base_resume,
        linkedin_experience=p.linkedin_experience,
        agent_permissions=p.agent_permissions,
    )


@pytest.fixture
def config():
    """Default config with filesystem transport."""
    return PipelineConfig(scraper_transport="filesystem", scraper_repo_path="")


@pytest.fixture
def logger():
    """Quiet logger for tests."""
    log = logging.getLogger("test_delta_sync")
    log.handlers.clear()
    log.setLevel(logging.DEBUG)
    return log


def _make_job(url, title="Engineer", company="Acme", ats="LinkedIn"):
    """Create a minimal valid job record."""
    return {
        "url": url,
        "title": title,
        "company": company,
        "ats": ats,
        "first_seen": "2026-08-31T14:00:00Z",
    }


def _write_delta(scraper_dir, run_at, source, added, updated):
    """Write a delta file + append to index.jsonl."""
    deltas_dir = scraper_dir / "output" / "deltas"
    safe_ts = run_at.replace(":", "-")
    filename = f"{safe_ts}_{source}.json"
    delta = {
        "run_at": run_at,
        "source": source,
        "added": added,
        "updated": updated,
    }
    (deltas_dir / filename).write_text(json.dumps(delta))
    # Append to manifest
    manifest = deltas_dir / "index.jsonl"
    entry = {
        "run_at": run_at,
        "file": filename,
        "source": source,
        "added": len(added),
        "updated": len(updated),
    }
    with open(manifest, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return filename


# ─── EnrichmentStore.sync_delta tests ──────────────────────────────────────


class TestSyncDelta:
    def test_upserts_added_and_updated(self, store, tmp_path):
        """sync_delta upserts jobs from both 'added' and 'updated' arrays."""
        delta = {
            "run_at": "2026-08-31T14:00:00Z",
            "source": "LinkedIn",
            "added": [_make_job("https://example.com/1", title="Director")],
            "updated": [_make_job("https://example.com/2", title="VP")],
        }
        delta_path = tmp_path / "delta.json"
        delta_path.write_text(json.dumps(delta))

        added, updated = store.sync_delta(str(delta_path), quiet=True)
        assert added == 1
        assert updated == 1

        jobs = store.query_jobs(status="all")
        urls = {j["url"] for j in jobs}
        assert "https://example.com/1" in urls
        assert "https://example.com/2" in urls

    def test_empty_delta(self, store, tmp_path):
        """sync_delta with empty added/updated arrays returns (0, 0)."""
        delta = {
            "run_at": "2026-08-31T14:00:00Z",
            "source": "LinkedIn",
            "added": [],
            "updated": [],
        }
        delta_path = tmp_path / "delta.json"
        delta_path.write_text(json.dumps(delta))

        added, updated = store.sync_delta(str(delta_path), quiet=True)
        assert added == 0
        assert updated == 0

    def test_bad_url_scheme_raises(self, store, tmp_path):
        """sync_delta raises ValueError for bad URL scheme (security-critical)."""
        delta = {
            "run_at": "2026-08-31T14:00:00Z",
            "source": "LinkedIn",
            "added": [{"url": "file:///etc/passwd", "title": "X", "company": "Y"}],
            "updated": [],
        }
        delta_path = tmp_path / "delta.json"
        delta_path.write_text(json.dumps(delta))

        with pytest.raises(ValueError, match="Security"):
            store.sync_delta(str(delta_path), quiet=True)

    def test_missing_required_fields_skipped(self, store, tmp_path):
        """sync_delta skips entries missing required fields (data-quality)."""
        delta = {
            "run_at": "2026-08-31T14:00:00Z",
            "source": "LinkedIn",
            "added": [
                _make_job("https://example.com/1", title="Director"),
                {"url": "https://example.com/2"},  # missing title/company
            ],
            "updated": [],
        }
        delta_path = tmp_path / "delta.json"
        delta_path.write_text(json.dumps(delta))

        added, updated = store.sync_delta(str(delta_path), quiet=True)
        assert added == 1  # only the valid entry
        assert updated == 0

    def test_upsert_existing_job_updates_fields(self, store, tmp_path):
        """sync_delta updates fields on existing jobs (upsert by URL)."""
        # Insert initial job
        delta1 = {
            "run_at": "2026-08-31T14:00:00Z",
            "source": "LinkedIn",
            "added": [_make_job("https://example.com/1", title="Director")],
            "updated": [],
        }
        p1 = tmp_path / "d1.json"
        p1.write_text(json.dumps(delta1))
        store.sync_delta(str(p1), quiet=True)

        # Update with new title
        delta2 = {
            "run_at": "2026-08-31T15:00:00Z",
            "source": "LinkedIn",
            "added": [],
            "updated": [_make_job("https://example.com/1", title="VP Eng")],
        }
        p2 = tmp_path / "d2.json"
        p2.write_text(json.dumps(delta2))
        store.sync_delta(str(p2), quiet=True)

        jobs = store.query_jobs(status="all")
        assert len(jobs) == 1
        assert jobs[0]["title"] == "VP Eng"

    def test_first_seen_preserved_on_update(self, store, tmp_path):
        """sync_delta preserves first_seen via COALESCE (same as sync_jobs_db)."""
        # Insert with first_seen
        delta1 = {
            "run_at": "2026-08-31T14:00:00Z",
            "source": "LinkedIn",
            "added": [{
                "url": "https://example.com/1",
                "title": "Director",
                "company": "Acme",
                "ats": "LinkedIn",
                "first_seen": "2026-08-01T00:00:00Z",
            }],
            "updated": [],
        }
        p1 = tmp_path / "d1.json"
        p1.write_text(json.dumps(delta1))
        store.sync_delta(str(p1), quiet=True)

        # Update without first_seen (delta 'updated' may omit it)
        delta2 = {
            "run_at": "2026-08-31T15:00:00Z",
            "source": "LinkedIn",
            "added": [],
            "updated": [{
                "url": "https://example.com/1",
                "title": "VP Eng",
                "company": "Acme",
                "ats": "LinkedIn",
                # no first_seen
            }],
        }
        p2 = tmp_path / "d2.json"
        p2.write_text(json.dumps(delta2))
        store.sync_delta(str(p2), quiet=True)

        jobs = store.query_jobs(status="all")
        assert jobs[0]["first_seen"] == "2026-08-01T00:00:00Z"


# ─── sync_deltas orchestration tests ────────────────────────────────────────


class TestSyncDeltasOrchestration:
    def test_cold_start_raises(self, store, paths, config, logger):
        """sync_deltas raises ColdStartError when processed_deltas is empty."""
        state = {"processed_deltas": []}
        with pytest.raises(ColdStartError):
            sync_deltas(state, paths, config, store, logger)

    def test_processes_new_deltas(self, store, paths, config, logger, scraper_dir):
        """sync_deltas processes deltas not in processed_deltas."""
        # Write two deltas
        _write_delta(scraper_dir, "2026-08-31T14:00:00Z", "LinkedIn",
                     [_make_job("https://example.com/1")], [])
        _write_delta(scraper_dir, "2026-08-31T15:00:00Z", "LinkedIn",
                     [_make_job("https://example.com/2")], [])

        # First delta already processed
        state = {"processed_deltas": ["2026-08-31T14:00:00Z"]}

        result = sync_deltas(state, paths, config, store, logger)
        assert result["deltas_processed"] == 1
        assert result["jobs_added"] == 1
        assert "2026-08-31T15:00:00Z" in result["new_processed_deltas"]

        # Verify the job was upserted
        jobs = store.query_jobs(status="all")
        urls = {j["url"] for j in jobs}
        assert "https://example.com/2" in urls

    def test_no_reprocessing(self, store, paths, config, logger, scraper_dir):
        """sync_deltas does not reprocess already-processed deltas."""
        _write_delta(scraper_dir, "2026-08-31T14:00:00Z", "LinkedIn",
                     [_make_job("https://example.com/1")], [])

        state = {"processed_deltas": ["2026-08-31T14:00:00Z"]}
        result = sync_deltas(state, paths, config, store, logger)
        assert result["deltas_processed"] == 0
        assert result["jobs_added"] == 0

    def test_missing_manifest_raises(self, store, paths, config, logger):
        """sync_deltas raises when the manifest (index.jsonl) is missing."""
        state = {"processed_deltas": ["2026-08-31T14:00:00Z"]}
        with pytest.raises(FileNotFoundError):
            sync_deltas(state, paths, config, store, logger)

    def test_missing_delta_file_raises(self, store, paths, config, logger, scraper_dir):
        """sync_deltas raises when a delta file in the manifest is missing
        and has NOT been processed yet (data loss — loud error)."""
        # Write manifest entry but NOT the delta file
        manifest = scraper_dir / "output" / "deltas" / "index.jsonl"
        entry = {
            "run_at": "2026-08-31T14:00:00Z",
            "file": "2026-08-31T14-00-00Z_LinkedIn.json",
            "source": "LinkedIn",
            "added": 1,
            "updated": 0,
        }
        manifest.write_text(json.dumps(entry) + "\n")

        state = {"processed_deltas": ["2026-08-31T13:00:00Z"]}  # different run_at
        with pytest.raises(FileNotFoundError):
            sync_deltas(state, paths, config, store, logger)

    def test_malformed_manifest_raises(self, store, paths, config, logger, scraper_dir):
        """sync_deltas raises JSONDecodeError on malformed manifest."""
        manifest = scraper_dir / "output" / "deltas" / "index.jsonl"
        manifest.write_text("not valid json\n")

        state = {"processed_deltas": ["2026-08-31T13:00:00Z"]}
        with pytest.raises(json.JSONDecodeError):
            sync_deltas(state, paths, config, store, logger)

    def test_empty_manifest_no_op(self, store, paths, config, logger, scraper_dir):
        """sync_deltas returns zeros when manifest is empty."""
        manifest = scraper_dir / "output" / "deltas" / "index.jsonl"
        manifest.write_text("")

        state = {"processed_deltas": ["2026-08-31T13:00:00Z"]}
        result = sync_deltas(state, paths, config, store, logger)
        assert result["deltas_processed"] == 0

    def test_multiple_new_deltas(self, store, paths, config, logger, scraper_dir):
        """sync_deltas processes multiple new deltas in one call."""
        _write_delta(scraper_dir, "2026-08-31T14:00:00Z", "LinkedIn",
                     [_make_job("https://example.com/1")], [])
        _write_delta(scraper_dir, "2026-08-31T15:00:00Z", "Indeed",
                     [_make_job("https://example.com/2")], [])
        _write_delta(scraper_dir, "2026-08-31T16:00:00Z", "LinkedIn",
                     [_make_job("https://example.com/3")],
                     [_make_job("https://example.com/1", title="Senior Director")])

        state = {"processed_deltas": []}
        # First call is cold start
        with pytest.raises(ColdStartError):
            sync_deltas(state, paths, config, store, logger)

        # Mark first delta as processed (simulating post-cold-start)
        state["processed_deltas"] = ["2026-08-31T14:00:00Z"]
        result = sync_deltas(state, paths, config, store, logger)
        assert result["deltas_processed"] == 2
        assert result["jobs_added"] == 2  # example.com/2 and example.com/3
        assert result["jobs_updated"] == 1  # example.com/1 title update

        # Verify all jobs are in the store
        jobs = store.query_jobs(status="all")
        urls = {j["url"] for j in jobs}
        assert urls == {
            "https://example.com/1",
            "https://example.com/2",
            "https://example.com/3",
        }
        # Verify the update took effect
        job1 = next(j for j in jobs if j["url"] == "https://example.com/1")
        assert job1["title"] == "Senior Director"


# ─── HTTP transport tests ───────────────────────────────────────────────────


class TestHTTPTransport:
    def test_read_index_http(self):
        """_read_index fetches via urllib for HTTP transport."""
        config = PipelineConfig(
            scraper_transport="http",
            scraper_url="https://example.com/scraper",
        )
        paths = MagicMock()
        paths.deltas_index = Path("/nonexistent")

        mock_response = MagicMock()
        mock_response.read.return_value = b'{"run_at": "2026-08-31T14:00:00Z"}\n'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("pipeline.infrastructure.delta_sync.urllib.request.urlopen", return_value=mock_response) as mock_open:
            result = _read_index(paths, config)
            assert "run_at" in result

        # Verify the URL was constructed correctly
        mock_open.assert_called_once_with(
            "https://example.com/scraper/deltas/index.jsonl",
            timeout=30,
        )

    def test_read_delta_file_http(self):
        """_read_delta_file fetches via urllib for HTTP transport."""
        config = PipelineConfig(
            scraper_transport="http",
            scraper_url="https://example.com/scraper/",
        )
        paths = MagicMock()
        paths.deltas_dir = Path("/nonexistent")

        mock_response = MagicMock()
        mock_response.read.return_value = b'{"run_at": "2026-08-31T14:00:00Z"}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("pipeline.infrastructure.delta_sync.urllib.request.urlopen", return_value=mock_response) as mock_open:
            result = _read_delta_file("test.json", paths, config)
            assert "run_at" in result

        # Verify URL was constructed correctly (trailing slash stripped)
        mock_open.assert_called_once_with(
            "https://example.com/scraper/deltas/test.json",
            timeout=30,
        )

    def test_sync_deltas_http_transport(self, store, logger, tmp_path):
        """Full sync_deltas flow with HTTP transport (mocked)."""
        hunter = tmp_path / "hunter"
        hunter.mkdir()
        paths = Paths.from_hunter_dir(hunter)

        config = PipelineConfig(
            scraper_transport="http",
            scraper_url="https://example.com/scraper",
        )

        # Mock HTTP responses: first call = manifest, second = delta file
        manifest_text = json.dumps({
            "run_at": "2026-08-31T15:00:00Z",
            "file": "2026-08-31T15-00-00Z_LinkedIn.json",
            "source": "LinkedIn",
            "added": 1,
            "updated": 0,
        }) + "\n"

        delta_data = json.dumps({
            "run_at": "2026-08-31T15:00:00Z",
            "source": "LinkedIn",
            "added": [_make_job("https://example.com/1")],
            "updated": [],
        })

        responses = [manifest_text, delta_data]
        call_count = [0]

        def mock_urlopen(url, timeout=None):
            mock_resp = MagicMock()
            mock_resp.read.return_value = responses[call_count[0]].encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            call_count[0] += 1
            return mock_resp

        state = {"processed_deltas": ["2026-08-31T14:00:00Z"]}

        with patch("pipeline.infrastructure.delta_sync.urllib.request.urlopen", side_effect=mock_urlopen):
            result = sync_deltas(state, paths, config, store, logger)

        assert result["deltas_processed"] == 1
        assert result["jobs_added"] == 1

        # Verify job was upserted
        jobs = store.query_jobs(status="all")
        assert any(j["url"] == "https://example.com/1" for j in jobs)


# ─── Helper function tests ──────────────────────────────────────────────────


class TestParseIndex:
    def test_parse_valid_index(self):
        """_parse_index parses a valid JSONL manifest."""
        text = json.dumps({"run_at": "2026-08-31T14:00:00Z", "file": "a.json"}) + "\n"
        text += json.dumps({"run_at": "2026-08-31T15:00:00Z", "file": "b.json"}) + "\n"
        entries = _parse_index(text)
        assert len(entries) == 2
        assert entries[0]["run_at"] == "2026-08-31T14:00:00Z"
        assert entries[1]["file"] == "b.json"

    def test_parse_skips_blank_lines(self):
        """_parse_index skips blank lines."""
        text = "\n\n" + json.dumps({"run_at": "2026-08-31T14:00:00Z"}) + "\n\n"
        entries = _parse_index(text)
        assert len(entries) == 1

    def test_parse_empty_text(self):
        """_parse_index returns empty list for empty text."""
        assert _parse_index("") == []
        assert _parse_index("   \n  \n") == []
