"""Filesystem operations for the pipeline.

Pure functions extracted from pipeline_cli.py. These operate on the
workspace filesystem (listings/, drafts/, ready/, etc.) and are gated
by dry_run at the call site, not here.

Functions take Paths as an argument (not module-level constants) so they
work with tmp directories in tests.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from pipeline.infrastructure.paths import Paths


# ─── Folder naming ─────────────────────────────────────────────────────────


def company_role_slug(company: str, title: str) -> str:
    """Create a hyphenated <company-role> folder name.

    e.g. ("Google", "Director of Engineering") → "google-director-of-engineering"
    """
    combined = f"{company}-{title}"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", combined).strip("-").lower()
    return slug


# ─── File renaming ─────────────────────────────────────────────────────────


def rename_with_grade(file_path: Path, grade: float) -> Path:
    """Rename a file from [TBD] or [old_score] to [new_score].

    e.g. "[TBD] resume-v1.md" → "[9.5] resume-v1.md"
         "[7.0] resume-v1.md" → "[9.5] resume-v1.md"
    """
    name = file_path.name
    new_name = re.sub(r"^\[TBD\]|\[[\d.]+\]", f"[{grade}]", name)
    new_path = file_path.parent / new_name
    if new_path != file_path:
        file_path.rename(new_path)
    return new_path


def parse_grade_from_filename(filename: str) -> float | None:
    """Extract the numeric grade from a [score] filename prefix.

    e.g. "[8.5] resume-v1.md" → 8.5, "[TBD] resume-v1.md" → None
    """
    m = re.match(r"\[([\d.]+)\]", filename)
    return float(m.group(1)) if m else None


def parse_version_from_filename(filename: str) -> int | None:
    """Extract the version number from a resume-vN.md filename.

    e.g. "[8.5] resume-v2.md" → 2, "[TBD] job-description.md" → None
    """
    m = re.search(r"resume-v(\d+)", filename)
    return int(m.group(1)) if m else None


# ─── Directory moves ───────────────────────────────────────────────────────


def move_dir(src: Path, dest_base: Path, prefix: str = "") -> Path:
    """Move a job directory to a new location with optional prefix.

    If dest already exists, merges contents into it (for re-runs where
    a folder already exists from a previous attempt).
    """
    dest_base.mkdir(parents=True, exist_ok=True)
    name = src.name
    if prefix:
        name = f"{prefix} {name}"
    dest = dest_base / name
    if dest.exists():
        # Merge — move contents
        for item in src.iterdir():
            shutil.move(str(item), str(dest / item.name))
        src.rmdir()
    else:
        shutil.move(str(src), str(dest))
    return dest


# ─── URL deduplication ────────────────────────────────────────────────────


def collect_existing_urls(paths: Paths) -> set[str]:
    """Collect all URLs from existing workspace folders.

    Scans listings/, trash/, drafts/, rejected/, ready/ for
    *job-description.md files and extracts URLs from metadata comments
    or the JD body.
    """
    urls: set[str] = set()
    for base_dir in [
        paths.listings,
        paths.trash,
        paths.drafts,
        paths.rejected,
        paths.ready,
    ]:
        if not base_dir.exists():
            continue
        for job_dir in base_dir.iterdir():
            if not job_dir.is_dir():
                continue
            for jd_file in job_dir.glob("*job-description.md"):
                try:
                    content = jd_file.read_text(encoding="utf-8")
                    url_match = re.search(r"https?://[^\s\)]+", content)
                    if url_match:
                        urls.add(url_match.group(0))
                except Exception:
                    pass
    return urls


def collect_existing_urls_from_metadata(paths: Paths) -> set[str]:
    """Collect URLs from .devin/pipeline-url-index.json if it exists."""
    index_file = paths.devin_dir / "pipeline-url-index.json"
    if index_file.exists():
        try:
            with open(index_file) as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_url_index(urls: set[str], paths: Paths) -> None:
    """Save the URL index for future dedup."""
    paths.devin_dir.mkdir(parents=True, exist_ok=True)
    index_file = paths.devin_dir / "pipeline-url-index.json"
    with open(index_file, "w") as f:
        json.dump(sorted(urls), f, indent=2)


def discover_new_jobs(
    feasible_jobs: list[dict], paths: Paths
) -> list[dict]:
    """Filter to only jobs whose URLs are not already in the workspace.

    Combines the URL index (fast) with a filesystem scan (catches manual
    additions). Updates the URL index with all seen URLs.
    """
    existing_urls = collect_existing_urls_from_metadata(paths)
    existing_urls |= collect_existing_urls(paths)

    new_jobs = []
    for job in feasible_jobs:
        url = job.get("url", "")
        if url and url not in existing_urls:
            new_jobs.append(job)
        if url:
            existing_urls.add(url)

    save_url_index(existing_urls, paths)
    return new_jobs


# ─── JD file creation ──────────────────────────────────────────────────────


def create_jd_file(
    slug: str,
    jd_text: str,
    url: str,
    dest_dir: Path,
    dry_run: bool = False,
) -> Path:
    """Create a [TBD] job-description.md file in dest_dir/<slug>/.

    Includes the URL as a metadata comment for dedup. Does not overwrite
    if a JD file already exists (from a previous run or duplicate slug).

    Returns the path to the JD file (even in dry_run, for state tracking).
    """
    job_dir = dest_dir / slug
    jd_path = job_dir / "[TBD] job-description.md"

    if dry_run:
        return jd_path

    job_dir.mkdir(parents=True, exist_ok=True)

    # Don't overwrite if a JD file already exists
    if jd_path.exists():
        return jd_path

    # Check if a graded version exists too
    existing = [
        f
        for f in job_dir.iterdir()
        if f.name.endswith(" job-description.md") and f.name.startswith("[")
    ]
    if existing:
        return existing[0]

    content = f"<!-- url: {url} -->\n\n{jd_text}"
    jd_path.write_text(content, encoding="utf-8")
    return jd_path


def strip_url_metadata(jd_text: str) -> str:
    """Strip the <!-- url: ... --> metadata comment from JD text."""
    return re.sub(r"<!-- url: [^>]+ -->\n*", "", jd_text)


def extract_url_from_jd(jd_text: str) -> str:
    """Extract the URL from a JD's metadata comment, or empty string."""
    m = re.search(r"<!-- url: ([^\s]+) -->", jd_text)
    return m.group(1) if m else ""
