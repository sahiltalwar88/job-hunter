"""Tests for graph.py — routing functions and full graph topology.

Tests the four routing functions in isolation, then exercises the full
compiled graph with FakeLLM to verify edges and conditional routing work
end-to-end.
"""
import json
from pathlib import Path

import pytest
from langgraph.graph import START, END, StateGraph

from pipeline.infrastructure.graph import (
    build_job_graph,
    route_after_jd_grade,
    route_after_truthfulness,
    route_optimize,
    route_triage,
)
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


# ─── Routing function tests ──────────────────────────────────────────────────


def test_route_after_jd_grade_clearance():
    state = JobState(slug="co", jd_grade=JdGrade(grade=0, is_clearance=True))
    assert route_after_jd_grade(state) == "clearance"


def test_route_after_jd_grade_normal():
    state = JobState(slug="co", jd_grade=JdGrade(grade=8.5))
    assert route_after_jd_grade(state) == "graded"


def test_route_after_jd_grade_no_grade():
    state = JobState(slug="co")
    assert route_after_jd_grade(state) == "graded"


def test_route_triage_trash():
    state = JobState(slug="co", triage=TriageDestination.TRASH)
    assert route_triage(state) == "trash"


def test_route_triage_rejected_job_fit():
    state = JobState(slug="co", triage=TriageDestination.REJECTED_JOB_FIT)
    assert route_triage(state) == "rejected_job_fit"


def test_route_triage_drafts():
    state = JobState(slug="co", triage=TriageDestination.DRAFTS)
    assert route_triage(state) == "drafts"


def test_route_triage_none_defaults_trash():
    state = JobState(slug="co")
    assert route_triage(state) == "trash"


def test_route_optimize_continue():
    state = JobState(slug="co", optimize_can_improve=True)
    assert route_optimize(state) == "continue"


def test_route_optimize_done():
    state = JobState(slug="co", optimize_can_improve=False)
    assert route_optimize(state) == "done"


def test_route_optimize_none_defaults_done():
    state = JobState(slug="co")
    assert route_optimize(state) == "done"


def test_route_after_truthfulness_verified():
    state = JobState(slug="co", verification=Verification(verified=True))
    assert route_after_truthfulness(state) == "verified"


def test_route_after_truthfulness_unverified():
    state = JobState(slug="co", verification=Verification(verified=False))
    assert route_after_truthfulness(state) == "unverified"


def test_route_after_truthfulness_none_defaults_unverified():
    state = JobState(slug="co")
    assert route_after_truthfulness(state) == "unverified"


# ─── Full graph topology tests ───────────────────────────────────────────────
#
# These use FakeLLM with canned responses to exercise the graph end-to-end.
# We use dry_run=True to avoid needing real base resumes / file I/O for
# customize/grade steps, but we DO create the necessary folder structures
# for the terminal nodes to move.


def _make_node_config(tmp_paths, fake_llm, logger, dry_run=True):
    """Build a LangGraph RunnableConfig for full-graph tests."""
    from pipeline.infrastructure.config import PipelineConfig
    config = PipelineConfig(dry_run=dry_run)
    return {
        "configurable": {
            "llm": fake_llm,
            "logger": logger,
            "config": config,
            "paths": tmp_paths,
        }
    }


def _grade_json(grade, assessments=None):
    """Build a grade JSON string for FakeLLM."""
    if assessments is None:
        assessments = [("Python", "core", "DIRECT_HIT", "Strong")]
    per_criterion = [
        {"requirement": req, "tier": tier, "assessment": a, "comment": c}
        for req, tier, a, c in assessments
    ]
    return json.dumps({"grade": grade, "per_criterion": per_criterion, "model": "grader-model"})


def _jd_reasoning_json(assessments=None):
    """Build JD grading reasoning JSON for FakeLLM."""
    if assessments is None:
        assessments = [("Python", "core", "DIRECT_HIT", "Strong")]
    per_criterion = [
        {"requirement": req, "tier": tier, "assessment": a, "comment": c}
        for req, tier, a, c in assessments
    ]
    return json.dumps({"per_criterion": per_criterion})


def _jd_scoring_json(grade, assessments=None, justification="Fit."):
    """Build JD grading scoring JSON for FakeLLM."""
    if assessments is None:
        assessments = [("Python", "core", "DIRECT_HIT", "Strong")]
    per_criterion = [
        {"requirement": req, "tier": tier, "assessment": a, "comment": c}
        for req, tier, a, c in assessments
    ]
    return json.dumps({
        "grade": grade, "ceiling": 10.0, "core_score": grade,
        "preferred_bonus": 0, "quantitative_base": grade,
        "subjective_adjustment": 0,
        "per_criterion": per_criterion,
        "subjective_justification": "No gaps.",
        "justification": justification,
    })


def _create_source_files(tmp_paths):
    """Create minimal base resume + LinkedIn for prompt inlining."""
    tmp_paths.base_resume.parent.mkdir(parents=True, exist_ok=True)
    tmp_paths.base_resume.write_text("# Squall Leonhart\n\nSoftware Engineer\n", encoding="utf-8")
    tmp_paths.linkedin_experience.parent.mkdir(parents=True, exist_ok=True)
    tmp_paths.linkedin_experience.write_text("# Squall Leonhart — Full Experience\n\n## Experience\n", encoding="utf-8")


def _verification_json(verified=True, claims=None):
    return json.dumps({"verified": verified, "unverifiable_claims": claims or []})


def _reasoning_json(assessments=None):
    """Build a reasoning JSON string for FakeLLM Call 1."""
    if assessments is None:
        assessments = [("Python", "core", "DIRECT_HIT", "Strong")]
    per_criterion = [
        {"requirement": req, "tier": tier, "assessment": a, "comment": c}
        for req, tier, a, c in assessments
    ]
    return json.dumps({"per_criterion": per_criterion})


def _classification_json(claims=None):
    """Build a classification JSON string for FakeLLM Call 1."""
    return json.dumps({"per_claim": claims or []})


def test_graph_compiles_without_checkpointer():
    """build_job_graph(None) should compile successfully."""
    graph = build_job_graph(checkpointer=None)
    assert graph is not None


def test_full_graph_clearance_to_trash(tmp_paths, fake_llm, logger):
    """A clearance-required JD -> trashed at step4."""
    _create_source_files(tmp_paths)
    fake_llm.add_response("JD Grading Reasoning", "CLEARANCE")
    # Create the listing folder (step3_ingest would do this, but we need it
    # for the trash node to move it)
    (tmp_paths.listings / "defense-co").mkdir(parents=True)
    (tmp_paths.listings / "defense-co" / "[TBD] job-description.md").write_text("TS/SCI required")

    config = _make_node_config(tmp_paths, fake_llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)
    state = JobState(
        slug="defense-co",
        url="https://linkedin.com/123",
        jd_text="Must hold active TS/SCI clearance",
    )
    result = graph.invoke(state, config=config)
    final = JobState(**result)
    assert final.final_destination == TriageDestination.TRASH
    assert final.jd_grade.is_clearance is True


def test_full_graph_low_jd_grade_to_trash(tmp_paths, fake_llm, logger):
    """JD grade < 6 -> trashed at step5."""
    _create_source_files(tmp_paths)
    fake_llm.add_response("JD Grading Reasoning", _jd_reasoning_json([("Rust", "core", "GAP", "No Rust")]))
    fake_llm.add_response("JD Grading Scoring", _jd_scoring_json(3.0, [("Rust", "core", "GAP", "No Rust")], "Poor fit."))
    (tmp_paths.listings / "bad-co").mkdir(parents=True)
    (tmp_paths.listings / "bad-co" / "[TBD] job-description.md").write_text("JD text")

    config = _make_node_config(tmp_paths, fake_llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)
    state = JobState(slug="bad-co", url="https://x.com", jd_text="JD text")
    result = graph.invoke(state, config=config)
    final = JobState(**result)
    assert final.final_destination == TriageDestination.TRASH
    assert final.jd_grade.grade == 3.0


def test_full_graph_mid_jd_grade_to_rejected_job_fit(tmp_paths, fake_llm, logger):
    """JD grade 6-7.9 -> rejected/[JOB-FIT]."""
    _create_source_files(tmp_paths)
    fake_llm.add_response("JD Grading Reasoning", _jd_reasoning_json([("Python", "core", "ADDRESSED", "Has Python")]))
    fake_llm.add_response("JD Grading Scoring", _jd_scoring_json(7.0, [("Python", "core", "ADDRESSED", "Has Python")], "Decent fit."))
    (tmp_paths.listings / "mid-co").mkdir(parents=True)
    (tmp_paths.listings / "mid-co" / "[TBD] job-description.md").write_text("JD text")

    config = _make_node_config(tmp_paths, fake_llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)
    state = JobState(slug="mid-co", url="https://x.com", jd_text="JD text")
    result = graph.invoke(state, config=config)
    final = JobState(**result)
    assert final.final_destination == TriageDestination.REJECTED_JOB_FIT


def test_full_graph_pass_to_ready(tmp_paths, fake_llm, logger):
    """Full pass: JD grade 8+ -> customize -> grade 9+ -> verified -> ready.

    Uses dry_run=True for the customize/grade steps (no real base resume),
    but we pre-create the drafts folder and resume file so step9 and step10
    can find them. We use dry_run=False for the terminal move.

    Actually, we can't mix dry_run within a single graph run. So we use
    dry_run=False throughout and pre-create all needed files.
    """
    # FakeLLM responses:
    # 1. JD grading two-call -> grade 8.5
    # 2. Customize -> "Done" (we pre-create the resume file)
    # 3. Resume grading -> grade 9.5 JSON
    # 4. Optimize "can improve?" -> NO (exit loop)
    # 5. Truthfulness -> verified=True JSON
    fake_llm.add_response("JD Grading Reasoning", _jd_reasoning_json())
    fake_llm.add_response("JD Grading Scoring", _jd_scoring_json(8.5, justification="Good fit."))
    fake_llm.add_response("customizing", "Done")
    fake_llm.add_response("Be realistically harsh", _reasoning_json())
    fake_llm.add_response("You are grading resume v", _grade_json(9.5))
    fake_llm.add_response("YES or NO", "NO")
    fake_llm.add_response("You are a truthfulness", _classification_json([]))
    fake_llm.add_response("You are verifying resume", _verification_json(verified=True))

    # Pre-create base resume + LinkedIn (needed by step4 and step6)
    _create_source_files(tmp_paths)

    # Pre-create the listing folder (step3 creates it, but we need it before)
    job_dir = tmp_paths.listings / "good-co"
    job_dir.mkdir(parents=True)
    (job_dir / "[TBD] job-description.md").write_text("<!-- url: https://x.com -->\n\nWe need Python.")

    config = _make_node_config(tmp_paths, fake_llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)
    state = JobState(
        slug="good-co",
        url="https://x.com",
        jd_text="We need Python.",
    )
    result = graph.invoke(state, config=config)
    final = JobState(**result)

    assert final.final_destination == TriageDestination.READY
    assert final.jd_grade.grade == 8.5
    assert final.latest_grade is not None
    assert final.latest_grade.grade == 9.5
    assert final.verification is not None
    assert final.verification.verified is True
    assert len(final.resume_versions) == 1
    # Folder moved to ready/
    assert (tmp_paths.ready / "good-co").exists()


def test_full_graph_optimize_cycle(tmp_paths, fake_llm, logger):
    """Optimize cycle: grade 7 -> improve YES -> grade 9 -> ready.

    The FakeLLM needs to return different responses on successive calls
    to the same prompt substring. We use ordered responses: the first
    "Be realistically harsh" returns grade 7, the second returns grade 9.

    FakeLLM checks keys in insertion order and returns the first match.
    To return different responses on successive calls, we need to use
    distinct prompt substrings or a custom FakeLLM. Since the grading
    prompt is the same both times, we'll use a stateful fake.
    """
    from pipeline.infrastructure.llm_interface import FakeLLM

    class SequentialFakeLLM:
        """Returns canned responses in sequence, matching by substring."""

        def __init__(self):
            self.responses: list[tuple[str, str]] = []
            self.calls: list[str] = []
            self._idx = 0

        def add_response(self, key: str, response: str):
            self.responses.append((key, response))

        def __call__(self, prompt, *, model, timeout, workspace, retries=2, retry_delay=5,
                     export_path=None, permission_mode="dangerous", config_path=None,
                     job_slug=None, step=None):
            self.calls.append(prompt)
            for i, (key, resp) in enumerate(self.responses):
                if i < self._idx:
                    continue
                if key in prompt:
                    self._idx = i + 1
                    return resp, None
            return None, "No matching response"

    seq_llm = SequentialFakeLLM()
    # 1. JD grading two-call -> 8.5
    seq_llm.add_response("JD Grading Reasoning", _jd_reasoning_json())
    seq_llm.add_response("JD Grading Scoring", _jd_scoring_json(8.5, justification="Good fit."))
    # 2. Customize v1 -> "Done"
    seq_llm.add_response("customizing", "Done")
    # 3. Resume grading v1 -> reasoning + scoring (grade 7.0, below threshold)
    seq_llm.add_response("Be realistically harsh", _reasoning_json([("Rust", "core", "GAP", "Missing")]))
    seq_llm.add_response("You are grading resume v", _grade_json(7.0, [("Rust", "core", "GAP", "Missing")]))
    # 4. Optimize "can improve?" -> YES
    seq_llm.add_response("YES or NO", "YES")
    # 5. Optimize v2 -> "Done"
    seq_llm.add_response("improving", "Done")
    # 6. Resume grading v2 -> reasoning + scoring (grade 9.5)
    seq_llm.add_response("Be realistically harsh", _reasoning_json())
    seq_llm.add_response("You are grading resume v", _grade_json(9.5))
    # 7. Optimize "can improve?" -> NO (exit)
    seq_llm.add_response("YES or NO", "NO")
    # 8. Truthfulness -> classification + synthesis (verified)
    seq_llm.add_response("You are a truthfulness", _classification_json([]))
    seq_llm.add_response("You are verifying resume", _verification_json(verified=True))

    # Pre-create base resume + LinkedIn
    _create_source_files(tmp_paths)

    # Pre-create listing
    job_dir = tmp_paths.listings / "cycle-co"
    job_dir.mkdir(parents=True)
    (job_dir / "[TBD] job-description.md").write_text("<!-- url: https://x.com -->\n\nWe need Python + Rust.")

    # We need to pre-create the v2 resume file (FakeLLM doesn't write files)
    # But step6 runs before we can intercept. We'll use a node_config with
    # dry_run=False and pre-create the drafts folder with the v2 file after
    # step6 would have run. Actually, we can't intercept mid-graph.
    #
    # Solution: pre-create both resume files in drafts/. Step6_customize
    # for v1 pre-copies base resume (overwrites if exists). For v2, it
    # checks if the file exists after the LLM call. We pre-create v2.
    drafts_dir = tmp_paths.drafts / "cycle-co"
    drafts_dir.mkdir(parents=True)
    (drafts_dir / "[TBD] resume-v2.md").write_text("# Resume v2\n", encoding="utf-8")

    from pipeline.infrastructure.config import PipelineConfig
    config = {
        "configurable": {
            "llm": seq_llm,
            "logger": logger,
            "config": PipelineConfig(dry_run=False),
            "paths": tmp_paths,
        }
    }
    graph = build_job_graph(checkpointer=None)
    state = JobState(slug="cycle-co", url="https://x.com", jd_text="We need Python + Rust.")
    result = graph.invoke(state, config=config)
    final = JobState(**result)

    assert final.final_destination == TriageDestination.READY
    assert len(final.resume_versions) == 2
    assert final.resume_versions[0].version == 1
    assert final.resume_versions[1].version == 2
    assert final.latest_grade.grade == 9.5


def test_full_graph_unverified_to_rejected_resume(tmp_paths, fake_llm, logger):
    """Grade 9+ but truthfulness fails -> rejected/[RESUME]."""
    _create_source_files(tmp_paths)
    fake_llm.add_response("JD Grading Reasoning", _jd_reasoning_json())
    fake_llm.add_response("JD Grading Scoring", _jd_scoring_json(8.5, justification="Good fit."))
    fake_llm.add_response("customizing", "Done")
    fake_llm.add_response("Be realistically harsh", _reasoning_json())
    fake_llm.add_response("You are grading resume v", _grade_json(9.5))
    fake_llm.add_response("YES or NO", "NO")
    fake_llm.add_response("You are a truthfulness", _classification_json([
        {"claim": "Fabricated skill", "location": "Skills", "bucket": "FABRICATED",
         "source_checked": "both", "reason": "Not in either source."}
    ]))
    fake_llm.add_response("You are verifying resume", _verification_json(verified=False, claims=["Fabricated skill"]))

    job_dir = tmp_paths.listings / "unverified-co"
    job_dir.mkdir(parents=True)
    (job_dir / "[TBD] job-description.md").write_text("<!-- url: https://x.com -->\n\nJD text")

    config = _make_node_config(tmp_paths, fake_llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)
    state = JobState(slug="unverified-co", url="https://x.com", jd_text="JD text")
    result = graph.invoke(state, config=config)
    final = JobState(**result)

    assert final.final_destination == TriageDestination.REJECTED_RESUME
    assert final.verification.verified is False
    assert len(final.verification.unverifiable_claims) == 1


def test_full_graph_dry_run_no_moves(tmp_paths, fake_llm, logger):
    """Dry run: graph executes but no files are moved."""
    _create_source_files(tmp_paths)
    fake_llm.add_response("JD Grading Reasoning", _jd_reasoning_json([("Rust", "core", "GAP", "No Rust")]))
    fake_llm.add_response("JD Grading Scoring", _jd_scoring_json(3.0, [("Rust", "core", "GAP", "No Rust")], "Poor fit."))
    (tmp_paths.listings / "dry-co").mkdir(parents=True)
    (tmp_paths.listings / "dry-co" / "[TBD] job-description.md").write_text("JD text")

    config = _make_node_config(tmp_paths, fake_llm, logger, dry_run=True)
    graph = build_job_graph(checkpointer=None)
    state = JobState(slug="dry-co", url="https://x.com", jd_text="JD text")
    result = graph.invoke(state, config=config)
    final = JobState(**result)

    # In dry_run, step5_triage sets triage but doesn't move.
    # The trash node also doesn't move. But final_destination is still set.
    assert final.final_destination == TriageDestination.TRASH
    # Listing not moved (dry_run)
    assert (tmp_paths.listings / "dry-co").exists()
    assert not (tmp_paths.trash / "dry-co").exists()
