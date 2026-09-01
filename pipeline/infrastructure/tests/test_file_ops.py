"""Tests for pipeline.file_ops."""
from pathlib import Path

import pytest

from pipeline.infrastructure.file_ops import (
    collect_existing_urls,
    collect_existing_urls_from_metadata,
    company_role_slug,
    create_jd_file,
    discover_new_jobs,
    extract_url_from_jd,
    move_dir,
    parse_grade_from_filename,
    parse_version_from_filename,
    rename_with_grade,
    save_url_index,
    strip_url_metadata,
)
from pipeline.infrastructure.paths import Paths


# ─── company_role_slug ─────────────────────────────────────────────────────


def test_slug_basic():
    assert company_role_slug("Google", "Director of Engineering") == "google-director-of-engineering"


def test_slug_special_chars():
    assert company_role_slug("J.P. Morgan", "VP, Engineering") == "j-p-morgan-vp-engineering"


def test_slug_lowercase():
    assert company_role_slug("Meta", "Staff Engineer") == "meta-staff-engineer"


# ─── rename_with_grade ─────────────────────────────────────────────────────


def test_rename_tbd_to_score(tmp_path):
    p = tmp_path / "[TBD] resume-v1.md"
    p.write_text("test")
    new_p = rename_with_grade(p, 9.5)
    assert new_p.name == "[9.5] resume-v1.md"
    assert new_p.exists()
    assert not p.exists()


def test_rename_old_score_to_new(tmp_path):
    p = tmp_path / "[7.0] resume-v2.md"
    p.write_text("test")
    new_p = rename_with_grade(p, 9.0)
    assert new_p.name == "[9.0] resume-v2.md"


# ─── parse_grade_from_filename ─────────────────────────────────────────────


def test_parse_grade_scored():
    assert parse_grade_from_filename("[8.5] resume-v1.md") == 8.5


def test_parse_grade_tbd():
    assert parse_grade_from_filename("[TBD] resume-v1.md") is None


def test_parse_grade_no_prefix():
    assert parse_grade_from_filename("resume-v1.md") is None


# ─── parse_version_from_filename ───────────────────────────────────────────


def test_parse_version_resume():
    assert parse_version_from_filename("[8.5] resume-v2.md") == 2


def test_parse_version_tbd():
    assert parse_version_from_filename("[TBD] resume-v1.md") == 1


def test_parse_version_jd():
    assert parse_version_from_filename("[TBD] job-description.md") is None


# ─── move_dir ──────────────────────────────────────────────────────────────


def test_move_dir_basic(tmp_path):
    src = tmp_path / "listings" / "google-engineer"
    src.mkdir(parents=True)
    (src / "[TBD] job-description.md").write_text("JD")
    dest_base = tmp_path / "drafts"
    result = move_dir(src, dest_base)
    assert result == dest_base / "google-engineer"
    assert result.exists()
    assert not src.exists()


def test_move_dir_with_prefix(tmp_path):
    src = tmp_path / "listings" / "google-engineer"
    src.mkdir(parents=True)
    (src / "[TBD] job-description.md").write_text("JD")
    dest_base = tmp_path / "rejected"
    result = move_dir(src, dest_base, prefix="[JOB-FIT]")
    assert result == dest_base / "[JOB-FIT] google-engineer"


def test_move_dir_merge(tmp_path):
    src = tmp_path / "listings" / "google-engineer"
    src.mkdir(parents=True)
    (src / "new-file.md").write_text("new")
    dest_base = tmp_path / "drafts"
    dest = dest_base / "google-engineer"
    dest.mkdir(parents=True)
    (dest / "existing-file.md").write_text("existing")
    move_dir(src, dest_base)
    assert (dest / "existing-file.md").exists()
    assert (dest / "new-file.md").exists()
    assert not src.exists()


# ─── URL dedup ──────────────────────────────────────────────────────────────


def test_collect_existing_urls(tmp_paths):
    job_dir = tmp_paths.listings / "google-engineer"
    job_dir.mkdir(parents=True)
    (job_dir / "[TBD] job-description.md").write_text(
        "<!-- url: https://linkedin.com/jobs/123 -->\n\nJD text"
    )
    urls = collect_existing_urls(tmp_paths)
    assert "https://linkedin.com/jobs/123" in urls


def test_save_and_load_url_index(tmp_paths):
    save_url_index({"https://example.com/1"}, tmp_paths)
    urls = collect_existing_urls_from_metadata(tmp_paths)
    assert "https://example.com/1" in urls


def test_discover_new_jobs_filters_existing(tmp_paths):
    job_dir = tmp_paths.listings / "google-engineer"
    job_dir.mkdir(parents=True)
    (job_dir / "[TBD] job-description.md").write_text(
        "<!-- url: https://linkedin.com/jobs/123 -->\n\nJD"
    )
    save_url_index({"https://linkedin.com/jobs/123"}, tmp_paths)

    feasible = [
        {"url": "https://linkedin.com/jobs/123", "company": "Google", "title": "Engineer"},
        {"url": "https://linkedin.com/jobs/456", "company": "Meta", "title": "Engineer"},
    ]
    new = discover_new_jobs(feasible, tmp_paths)
    assert len(new) == 1
    assert new[0]["url"] == "https://linkedin.com/jobs/456"


# ─── create_jd_file ────────────────────────────────────────────────────────


def test_create_jd_file(tmp_paths):
    jd_path = create_jd_file("test-slug", "JD text", "https://linkedin.com/789", tmp_paths.listings)
    assert jd_path.name == "[TBD] job-description.md"
    assert jd_path.exists()
    content = jd_path.read_text()
    assert "<!-- url: https://linkedin.com/789 -->" in content
    assert "JD text" in content


def test_create_jd_file_dry_run(tmp_paths):
    jd_path = create_jd_file("dry-slug", "JD", "url", tmp_paths.listings, dry_run=True)
    assert not jd_path.exists()


# ─── URL metadata helpers ──────────────────────────────────────────────────


def test_strip_url_metadata():
    text = "<!-- url: https://linkedin.com/123 -->\n\nWe are looking for..."
    stripped = strip_url_metadata(text)
    assert "<!-- url:" not in stripped
    assert "We are looking for" in stripped


def test_extract_url_from_jd():
    text = "<!-- url: https://linkedin.com/123 -->\n\nWe are looking for..."
    assert extract_url_from_jd(text) == "https://linkedin.com/123"


def test_extract_url_no_metadata():
    assert extract_url_from_jd("no metadata here") == ""
