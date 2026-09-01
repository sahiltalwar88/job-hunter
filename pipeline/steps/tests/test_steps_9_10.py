"""Tests for step9_veracity, step10_finalize, and terminal nodes."""
import json
from pathlib import Path

import pytest

from pipeline.infrastructure.state import (
    Assessment,
    Criterion,
    JdGrade,
    JobState,
    ResumeGrade,
    ResumeVersion,
    TriageDestination,
    Verification,
)
from pipeline.infrastructure.state_store import JobStatus, StateStore
from pipeline.steps.step9_veracity import (
    parse_verification_json,
    step9_veracity_node,
)
from pipeline.steps.step10_finalize import (
    rejected_job_fit_node,
    rejected_resume_node,
    step10_ready_node,
    trash_node,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _setup_drafts(tmp_paths, slug="test-co", resume_grade_prefix="[9.5]"):
    """Create drafts/<slug>/ with a graded JD + graded resume + source files. Returns job_dir."""
    job_dir = tmp_paths.drafts / slug
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "[8.5] job-description.md").write_text(
        "<!-- url: https://linkedin.com/123 -->\n\nWe are looking for a Python engineer.",
        encoding="utf-8",
    )
    (job_dir / f"{resume_grade_prefix} resume-v1.md").write_text(
        "# Squall Leonhart\n\nSoftware Engineer\n", encoding="utf-8"
    )
    # Create source files needed for prompt inlining
    tmp_paths.base_resume.parent.mkdir(parents=True, exist_ok=True)
    tmp_paths.base_resume.write_text("# Squall Leonhart\n\nSoftware Engineer\n", encoding="utf-8")
    tmp_paths.linkedin_experience.parent.mkdir(parents=True, exist_ok=True)
    tmp_paths.linkedin_experience.write_text("# Squall Leonhart — Full Experience\n\n## Experience\n", encoding="utf-8")
    return job_dir


def _setup_listings(tmp_paths, slug="test-co", jd_grade_prefix="[3.0]"):
    """Create listings/<slug>/ with a graded JD. Returns job_dir."""
    job_dir = tmp_paths.listings / slug
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / f"{jd_grade_prefix} job-description.md").write_text("JD text", encoding="utf-8")
    return job_dir


def _make_verification_json(verified=True, claims=None):
    """Build a verification JSON dict for FakeLLM responses."""
    return json.dumps({
        "verified": verified,
        "unverifiable_claims": claims or [],
    })


# ─── parse_verification_json ─────────────────────────────────────────────────


def test_parse_verification_json_valid(tmp_path):
    f = tmp_path / "verification.json"
    f.write_text(json.dumps({"verified": True, "unverifiable_claims": []}))
    data = parse_verification_json(f)
    assert data["verified"] is True


def test_parse_verification_json_missing(tmp_path):
    assert parse_verification_json(tmp_path / "nope.json") is None


def test_parse_verification_json_invalid(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("not json")
    assert parse_verification_json(f) is None


# ─── step9_veracity_node ─────────────────────────────────────────────────


def test_step9_verified(node_config, fake_llm, tmp_paths):
    """Grade >= 9, two-call truthfulness returns verified=True."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    fake_llm.add_response("You are a truthfulness", _make_classification_json([]))
    fake_llm.add_response("You are verifying resume", _make_verification_json(verified=True))

    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.5, per_criterion=[]),
    )
    result = step9_veracity_node(state, node_config)
    assert result["verification"].verified is True
    assert result["verification"].unverifiable_claims == []


def test_step9_unverified(node_config, fake_llm, tmp_paths):
    """Grade >= 9, two-call truthfulness returns verified=False with claims."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.0]")
    fake_llm.add_response("You are a truthfulness", _make_classification_json([
        {"claim": "Claim 1", "location": "x", "bucket": "FABRICATED",
         "source_checked": "both", "reason": "r1"},
        {"claim": "Claim 2", "location": "y", "bucket": "MATERIAL_OVERSTATEMENT",
         "source_checked": "LinkedIn", "reason": "r2"},
    ]))
    fake_llm.add_response("You are verifying resume", _make_verification_json(
        verified=False, claims=["Claim 1", "Claim 2"]
    ))

    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.0] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.0, per_criterion=[]),
    )
    result = step9_veracity_node(state, node_config)
    assert result["verification"].verified is False
    assert len(result["verification"].unverifiable_claims) == 2


def test_step9_from_file(node_config, fake_llm, tmp_paths):
    """Verification JSON read from file (fallback after two-call)."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    # Pre-create the verification JSON file (fallback path)
    veracity_dir = tmp_paths.veracity / "test-co"
    veracity_dir.mkdir(parents=True, exist_ok=True)
    (veracity_dir / "verification.json").write_text(_make_verification_json(verified=True))
    # Call 1 returns classification, Call 2 returns non-JSON (falls back to file)
    fake_llm.add_response("You are a truthfulness", _make_classification_json([]))
    fake_llm.add_response("You are verifying resume", "Done. Verification written to file.")

    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.5, per_criterion=[]),
    )
    result = step9_veracity_node(state, node_config)
    assert result["verification"].verified is True


def test_step9_grade_below_threshold_skips_llm(node_config, fake_llm, tmp_paths):
    """Grade < 9 -> no LLM call, verification.verified = False."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[7.5]")
    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[7.5] resume-v1.md")],
        latest_grade=ResumeGrade(grade=7.5, per_criterion=[]),
    )
    result = step9_veracity_node(state, node_config)
    assert result["verification"].verified is False
    assert len(fake_llm.calls) == 0  # no LLM call


def test_step9_no_grade_skips_llm(node_config, fake_llm, tmp_paths):
    """No latest_grade -> no LLM call, verification.verified = False."""
    _setup_drafts(tmp_paths)
    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md")],
    )
    result = step9_veracity_node(state, node_config)
    assert result["verification"].verified is False
    assert len(fake_llm.calls) == 0


def test_step9_dry_run(dry_run_node_config, tmp_paths):
    """Dry run: no LLM call, verification.verified = False."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.5, per_criterion=[]),
    )
    result = step9_veracity_node(state, dry_run_node_config)
    assert result["verification"].verified is False


def test_step9_llm_error_raises(node_config, fake_llm, tmp_paths):
    """LLM failure on Call 1 (classification) should raise."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.5, per_criterion=[]),
    )
    with pytest.raises(RuntimeError, match="classification"):
        step9_veracity_node(state, node_config)


def test_step9_parse_error_raises(node_config, fake_llm, tmp_paths):
    """Unparseable verification from Call 2 (no file, no JSON) should raise."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    # Call 1 returns valid classification, Call 2 returns unparseable
    fake_llm.add_response("You are a truthfulness", _make_classification_json([]))
    fake_llm.add_response("You are verifying resume", "I could not verify this resume.")

    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.5, per_criterion=[]),
    )
    with pytest.raises(RuntimeError, match="could not parse verification JSON"):
        step9_veracity_node(state, node_config)


def test_step9_no_resume_raises(node_config, fake_llm, tmp_paths):
    """No resume file in drafts should raise (when grade >= threshold)."""
    # Create drafts dir but no resume file
    (tmp_paths.drafts / "test-co").mkdir(parents=True)
    (tmp_paths.drafts / "test-co" / "[8.5] job-description.md").write_text("JD")
    fake_llm.add_response("You are a truthfulness", _make_verification_json(verified=True))

    state = JobState(
        slug="test-co",
        jd_text="JD text",
        latest_grade=ResumeGrade(grade=9.5, per_criterion=[]),
    )
    with pytest.raises(RuntimeError, match="no resume found"):
        step9_veracity_node(state, node_config)


# ─── two-call truthfulness (ADR-0011) ────────────────────────────────────────


def _make_classification_json(claims=None):
    """Build a classification JSON dict for FakeLLM Call 1 responses."""
    return json.dumps({
        "per_claim": claims or [],
    })


def test_step9_two_call_verified(node_config, fake_llm, tmp_paths):
    """Two-call truthfulness: Call 1 classifies → Call 2 synthesizes verified=True."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    fake_llm.add_response("You are a truthfulness", _make_classification_json([]))
    fake_llm.add_response("You are verifying resume", _make_verification_json(verified=True))

    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.5, per_criterion=[]),
    )
    result = step9_veracity_node(state, node_config)
    assert result["verification"].verified is True


def test_step9_two_call_unverified(node_config, fake_llm, tmp_paths):
    """Two-call truthfulness: Call 2 returns verified=False with claims."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.0]")
    fake_llm.add_response("You are a truthfulness", _make_classification_json([
        {"claim": "Led 100-person org", "location": "Experience > Oracle",
         "bucket": "MATERIAL_OVERSTATEMENT", "source_checked": "LinkedIn",
         "reason": "LinkedIn says 50."}
    ]))
    fake_llm.add_response("You are verifying resume", _make_verification_json(
        verified=False, claims=["Led 100-person org"]
    ))

    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.0] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.0, per_criterion=[]),
    )
    result = step9_veracity_node(state, node_config)
    assert result["verification"].verified is False
    assert len(result["verification"].unverifiable_claims) == 1


def test_step9_two_call_classification_error_raises(node_config, fake_llm, tmp_paths):
    """If Call 1 (classification) fails, the node should raise — not proceed to Call 2."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")

    state = JobState(
        slug="test-co",
        jd_text="JD text",
        resume_versions=[ResumeVersion(version=1, path=tmp_paths.drafts / "test-co" / "[9.5] resume-v1.md")],
        latest_grade=ResumeGrade(grade=9.5, per_criterion=[]),
    )
    with pytest.raises(RuntimeError, match="classification"):
        step9_veracity_node(state, node_config)


# ─── step10_ready_node ───────────────────────────────────────────────────────


def test_step10_ready_moves_to_ready(node_config, tmp_paths):
    """Passing resume: drafts/<slug>/ moved to ready/<slug>/."""
    job_dir = _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    state = JobState(slug="test-co")
    result = step10_ready_node(state, node_config)
    assert result["final_destination"] == TriageDestination.READY
    assert (tmp_paths.ready / "test-co").exists()
    assert not (tmp_paths.drafts / "test-co").exists()


def test_step10_ready_dry_run(dry_run_node_config, tmp_paths):
    """Dry run: no move, but final_destination still set."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    state = JobState(slug="test-co")
    result = step10_ready_node(state, dry_run_node_config)
    assert result["final_destination"] == TriageDestination.READY
    assert (tmp_paths.drafts / "test-co").exists()  # not moved
    assert not (tmp_paths.ready / "test-co").exists()


def test_step10_ready_missing_folder_ok(node_config, tmp_paths):
    """If drafts/<slug>/ doesn't exist, node still succeeds (idempotent)."""
    state = JobState(slug="nonexistent")
    result = step10_ready_node(state, node_config)
    assert result["final_destination"] == TriageDestination.READY


# ─── trash_node ──────────────────────────────────────────────────────────────


def test_trash_node_moves_to_trash(node_config, tmp_paths):
    """Clearance/low-grade job: listings/<slug>/ moved to trash/<slug>/."""
    _setup_listings(tmp_paths, slug="bad-co", jd_grade_prefix="[3.0]")
    state = JobState(slug="bad-co")
    result = trash_node(state, node_config)
    assert result["final_destination"] == TriageDestination.TRASH
    assert (tmp_paths.trash / "bad-co").exists()
    assert not (tmp_paths.listings / "bad-co").exists()


def test_trash_node_dry_run(dry_run_node_config, tmp_paths):
    """Dry run: no move, but final_destination set."""
    _setup_listings(tmp_paths, slug="bad-co", jd_grade_prefix="[3.0]")
    state = JobState(slug="bad-co")
    result = trash_node(state, dry_run_node_config)
    assert result["final_destination"] == TriageDestination.TRASH
    assert (tmp_paths.listings / "bad-co").exists()


def test_trash_node_missing_folder_ok(node_config, tmp_paths):
    """If listings/<slug>/ doesn't exist, node still succeeds."""
    state = JobState(slug="nonexistent")
    result = trash_node(state, node_config)
    assert result["final_destination"] == TriageDestination.TRASH


# ─── rejected_job_fit_node ───────────────────────────────────────────────────


def test_rejected_job_fit_moves_with_prefix(node_config, tmp_paths):
    """Grade 6-7.9: listings/<slug>/ moved to rejected/[JOB-FIT] <slug>/."""
    _setup_listings(tmp_paths, slug="mid-co", jd_grade_prefix="[7.0]")
    state = JobState(slug="mid-co")
    result = rejected_job_fit_node(state, node_config)
    assert result["final_destination"] == TriageDestination.REJECTED_JOB_FIT
    assert (tmp_paths.rejected / "[JOB-FIT] mid-co").exists()
    assert not (tmp_paths.listings / "mid-co").exists()


def test_rejected_job_fit_dry_run(dry_run_node_config, tmp_paths):
    """Dry run: no move, but final_destination set."""
    _setup_listings(tmp_paths, slug="mid-co", jd_grade_prefix="[7.0]")
    state = JobState(slug="mid-co")
    result = rejected_job_fit_node(state, dry_run_node_config)
    assert result["final_destination"] == TriageDestination.REJECTED_JOB_FIT
    assert (tmp_paths.listings / "mid-co").exists()


# ─── rejected_resume_node ────────────────────────────────────────────────────


def test_rejected_resume_moves_with_prefix(node_config, tmp_paths):
    """Grade < 9 or unverified: drafts/<slug>/ moved to rejected/[RESUME] <slug>/."""
    _setup_drafts(tmp_paths, slug="fail-co", resume_grade_prefix="[7.5]")
    state = JobState(slug="fail-co")
    result = rejected_resume_node(state, node_config)
    assert result["final_destination"] == TriageDestination.REJECTED_RESUME
    assert (tmp_paths.rejected / "[RESUME] fail-co").exists()
    assert not (tmp_paths.drafts / "fail-co").exists()


def test_rejected_resume_dry_run(dry_run_node_config, tmp_paths):
    """Dry run: no move, but final_destination set."""
    _setup_drafts(tmp_paths, slug="fail-co", resume_grade_prefix="[7.5]")
    state = JobState(slug="fail-co")
    result = rejected_resume_node(state, dry_run_node_config)
    assert result["final_destination"] == TriageDestination.REJECTED_RESUME
    assert (tmp_paths.drafts / "fail-co").exists()


def test_rejected_resume_missing_folder_ok(node_config, tmp_paths):
    """If drafts/<slug>/ doesn't exist, node still succeeds."""
    state = JobState(slug="nonexistent")
    result = rejected_resume_node(state, node_config)
    assert result["final_destination"] == TriageDestination.REJECTED_RESUME


# ─── State transition audit (E2) ──────────────────────────────────────────────


def test_step10_ready_records_transition(node_config, tmp_paths):
    """Move to ready/ records a state_transitions row."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    state = JobState(slug="test-co")
    step10_ready_node(state, node_config)

    store = StateStore(str(tmp_paths.jobs_db))
    history = store.query_history("test-co")
    store.close()
    assert len(history) == 1
    assert history[0]["from_status"] == JobStatus.DRAFTS.value
    assert history[0]["to_status"] == JobStatus.READY.value


def test_trash_node_records_transition(node_config, tmp_paths):
    """Move to trash/ records a state_transitions row."""
    _setup_listings(tmp_paths, slug="bad-co", jd_grade_prefix="[3.0]")
    state = JobState(slug="bad-co")
    trash_node(state, node_config)

    store = StateStore(str(tmp_paths.jobs_db))
    history = store.query_history("bad-co")
    store.close()
    assert len(history) == 1
    assert history[0]["from_status"] == JobStatus.LISTINGS.value
    assert history[0]["to_status"] == JobStatus.TRASH.value


def test_rejected_job_fit_records_transition(node_config, tmp_paths):
    """Move to rejected/[JOB-FIT] records a state_transitions row."""
    _setup_listings(tmp_paths, slug="mid-co", jd_grade_prefix="[7.0]")
    state = JobState(slug="mid-co")
    rejected_job_fit_node(state, node_config)

    store = StateStore(str(tmp_paths.jobs_db))
    history = store.query_history("mid-co")
    store.close()
    assert len(history) == 1
    assert history[0]["from_status"] == JobStatus.LISTINGS.value
    assert history[0]["to_status"] == JobStatus.REJECTED_JOB_FIT.value


def test_rejected_resume_records_transition(node_config, tmp_paths):
    """Move to rejected/[RESUME] records a state_transitions row."""
    _setup_drafts(tmp_paths, slug="fail-co", resume_grade_prefix="[7.5]")
    state = JobState(slug="fail-co")
    rejected_resume_node(state, node_config)

    store = StateStore(str(tmp_paths.jobs_db))
    history = store.query_history("fail-co")
    store.close()
    assert len(history) == 1
    assert history[0]["from_status"] == JobStatus.DRAFTS.value
    assert history[0]["to_status"] == JobStatus.REJECTED_RESUME.value


def test_step10_ready_dry_run_no_transition(dry_run_node_config, tmp_paths):
    """Dry run: no move, no transition recorded."""
    _setup_drafts(tmp_paths, resume_grade_prefix="[9.5]")
    state = JobState(slug="test-co")
    step10_ready_node(state, dry_run_node_config)

    store = StateStore(str(tmp_paths.jobs_db))
    history = store.query_history("test-co")
    store.close()
    assert history == []
