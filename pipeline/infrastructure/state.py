"""Pipeline state — Pydantic models + free functions (ADR-0006, ADR-0008).

Rich domain models, FP-style (ADR-0008):
    - Pure data (Pydantic models) with pure query methods (read-only).
    - Free functions co-located here for construction and transformation.
    - No mutation methods. Transforms return new copies via model_copy.

The per-job graph state is JobState. LangGraph merges node return values
(partial dicts) into the state. The resume_versions list uses
Annotated[list, operator.add] so nodes can append by returning a
single-element list.
"""
from __future__ import annotations

import re
from enum import Enum
from operator import add
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field


# ─── Enums (closed vocabularies) ──────────────────────────────────────────


class FeasibilityTier(str, Enum):
    """Feasibility verdict from the LLM feasibility checker."""

    PREFERRED = "preferred"
    YES = "yes"
    NO = "no"


class Assessment(str, Enum):
    """Per-criterion verdict from the resume grader."""

    DIRECT_HIT = "DIRECT_HIT"
    ADDRESSED = "ADDRESSED"
    PARTIAL = "PARTIAL"
    GAP = "GAP"


class TriageDestination(str, Enum):
    """Where a job ends up after the pipeline processes it."""

    TRASH = "trash"
    REJECTED_JOB_FIT = "rejected_job_fit"
    DRAFTS = "drafts"
    READY = "ready"
    REJECTED_RESUME = "rejected_resume"


# ─── Sub-models ────────────────────────────────────────────────────────────


class JdGrade(BaseModel):
    """Result of grading a JD against the candidate's profile."""

    grade: float = Field(ge=0, le=10)
    justification: str = ""
    is_clearance: bool = False


class Criterion(BaseModel):
    """One requirement from the JD, assessed against the resume."""

    requirement: str = ""
    tier: str = ""  # "core", "nice-to-have", etc.
    assessment: Assessment = Assessment.PARTIAL
    comment: str = ""


class ResumeGrade(BaseModel):
    """Result of grading a customized resume against a JD."""

    grade: float = Field(ge=0, le=10)
    per_criterion: list[Criterion] = []
    model: str = ""


class ResumeVersion(BaseModel):
    """One version of a customized resume (file on disk + version number).

    The grade lives in JobState.latest_grade, not here — the grade node
    updates it separately from the customize node that creates the version.
    This avoids list-reducer complexity (append vs replace).
    """

    version: int = Field(ge=1)
    path: Path


class Verification(BaseModel):
    """Result of truthfulness review against the LinkedIn superset."""

    verified: bool = False
    unverifiable_claims: list[str] = []


# ─── JobState (the LangGraph per-job state) ────────────────────────────────


class JobState(BaseModel):
    """State that flows through the per-job LangGraph pipeline.

    Nodes return partial dicts (e.g. {"jd_grade": JdGrade(...)}) which
    LangGraph merges into this model. The resume_versions list uses
    Annotated[list, operator.add] so nodes append by returning a
    single-element list.
    """

    # ── Identity ──
    slug: str
    url: str = ""
    company: str = ""
    title: str = ""

    # ── JD ──
    jd_text: str = ""
    jd_path: Path | None = None
    jd_grade: JdGrade | None = None

    # ── Triage ──
    triage: TriageDestination | None = None

    # ── Resume versions (accumulating — LangGraph concatenates on update) ──
    resume_versions: Annotated[list[ResumeVersion], add] = []

    # ── Grade for the latest version (set by grade node, read by optimize/truthfulness) ──
    latest_grade: ResumeGrade | None = None

    # ── Optimize cycle control ──
    optimize_can_improve: bool | None = None

    # ── Truthfulness ──
    verification: Verification | None = None

    # ── Final destination (set by terminal nodes) ──
    final_destination: TriageDestination | None = None

    # ── Error (populated if a node threw, caught by orchestrator) ──
    error: str | None = None

    # ── Pure query methods (read-only, no mutation) ──

    @property
    def latest_resume(self) -> ResumeVersion | None:
        """The most recent resume version, or None if none exist."""
        return self.resume_versions[-1] if self.resume_versions else None

    @property
    def latest_resume_path(self) -> Path | None:
        """Path to the most recent resume file, or None."""
        latest = self.latest_resume
        return latest.path if latest else None

    @property
    def is_passing(self) -> bool:
        """True if grade >= 9 AND truthfulness verified."""
        return (
            self.latest_grade is not None
            and self.latest_grade.grade >= 9
            and self.verification is not None
            and self.verification.verified
        )

    @property
    def has_core_gaps(self) -> bool:
        """True if the latest grade has any core-tier GAP criteria."""
        if not self.latest_grade:
            return False
        return any(
            c.assessment == Assessment.GAP and c.tier == "core"
            for c in self.latest_grade.per_criterion
        )

    @property
    def optimize_iteration_count(self) -> int:
        """Number of resume versions created (= optimize iterations)."""
        return len(self.resume_versions)

    @property
    def next_resume_version(self) -> int:
        """Version number for the next resume version (1 if none exist)."""
        if not self.resume_versions:
            return 1
        return self.resume_versions[-1].version + 1


# ─── Free functions: constructors ─────────────────────────────────────────


def _parse_grade_from_filename(filename: str) -> float | None:
    """Extract the numeric grade from a [score] filename prefix.

    e.g. "[8.5] resume-v1.md" → 8.5, "[TBD] resume-v1.md" → None
    """
    m = re.match(r"\[([\d.]+)\]", filename)
    return float(m.group(1)) if m else None


def _parse_version_from_filename(filename: str) -> int | None:
    """Extract the version number from a resume-vN.md filename.

    e.g. "[8.5] resume-v2.md" → 2, "[TBD] job-description.md" → None
    """
    m = re.search(r"resume-v(\d+)", filename)
    return int(m.group(1)) if m else None


def build_job_state_from_filesystem(slug: str, paths) -> JobState:
    """Construct a JobState from existing files on disk.

    Reads the JD from listings/ or drafts/, finds resume versions, parses
    grades from filenames. Used by --step and --job modes for debugging
    individual steps without running the full graph.

    Args:
        slug: The company-role slug (e.g. "google-staff-engineer").
        paths: Workspace Paths object (from pipeline.infrastructure.paths).

    Returns:
        A JobState populated with whatever was found on disk. Fields not
        found remain at their defaults.
    """
    # Find the job directory — could be in listings, drafts, ready, or rejected
    job_dir = None
    for base in (paths.drafts, paths.listings, paths.ready, paths.rejected, paths.trash):
        candidate = base / slug
        if candidate.is_dir():
            job_dir = candidate
            break

    if not job_dir:
        # Return a minimal state with just the slug
        return JobState(slug=slug)

    # Find the JD file (any *job-description.md)
    jd_files = list(job_dir.glob("*job-description.md"))
    jd_text = ""
    jd_path = None
    jd_grade = None
    if jd_files:
        jd_path = jd_files[0]
        jd_text = jd_path.read_text(encoding="utf-8")
        # Strip URL metadata comment
        jd_text = re.sub(r"<!-- url: [^>]+ -->\n*", "", jd_text)
        # Parse grade from filename
        grade_val = _parse_grade_from_filename(jd_path.name)
        if grade_val is not None:
            jd_grade = JdGrade(grade=grade_val)

    # Find resume versions (any *resume-vN.md)
    resume_versions = []
    latest_grade = None
    resume_files = sorted(
        job_dir.glob("*resume-v*.md"),
        key=lambda p: _parse_version_from_filename(p.name) or 0,
    )
    for rf in resume_files:
        vnum = _parse_version_from_filename(rf.name)
        if vnum is None:
            continue
        resume_versions.append(ResumeVersion(version=vnum, path=rf))

    # Find the highest-graded resume for latest_grade
    if resume_files:
        best_resume = max(
            resume_files,
            key=lambda p: _parse_grade_from_filename(p.name) or -1,
        )
        best_grade_val = _parse_grade_from_filename(best_resume.name)
        if best_grade_val is not None:
            latest_grade = ResumeGrade(grade=best_grade_val)

    # Extract URL from JD metadata comment if present
    url = ""
    if jd_path and jd_path.exists():
        raw = jd_path.read_text(encoding="utf-8")
        url_match = re.search(r"<!-- url: ([^\s]+) -->", raw)
        if url_match:
            url = url_match.group(1)

    return JobState(
        slug=slug,
        url=url,
        jd_text=jd_text,
        jd_path=jd_path,
        jd_grade=jd_grade,
        resume_versions=resume_versions,
        latest_grade=latest_grade,
    )


# ─── Free functions: transforms (return new copies, no mutation) ──────────


def with_jd_grade(state: JobState, grade: JdGrade) -> JobState:
    """Return a new JobState with the JD grade set."""
    return state.model_copy(update={"jd_grade": grade})


def with_resume_version(state: JobState, version: ResumeVersion) -> JobState:
    """Return a new JobState with a resume version appended.

    Note: in graph mode, nodes return {"resume_versions": [v]} and LangGraph
    concatenates via the Annotated[list, add] reducer. This transform is
    for --step and test mode where we construct state manually.
    """
    return state.model_copy(
        update={"resume_versions": state.resume_versions + [version]}
    )


def with_latest_grade(state: JobState, grade: ResumeGrade) -> JobState:
    """Return a new JobState with the latest resume grade set."""
    return state.model_copy(update={"latest_grade": grade})


def with_verification(state: JobState, v: Verification) -> JobState:
    """Return a new JobState with the truthfulness verification set."""
    return state.model_copy(update={"verification": v})


def with_final_destination(
    state: JobState, d: TriageDestination
) -> JobState:
    """Return a new JobState with the final destination set."""
    return state.model_copy(update={"final_destination": d})


def with_triage(state: JobState, d: TriageDestination) -> JobState:
    """Return a new JobState with the triage destination set."""
    return state.model_copy(update={"triage": d})


def with_optimize_can_improve(state: JobState, can_improve: bool) -> JobState:
    """Return a new JobState with the optimize_can_improve flag set."""
    return state.model_copy(update={"optimize_can_improve": can_improve})
