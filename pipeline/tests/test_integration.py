"""Integration tests for the full per-job graph flow.

These tests exercise the complete pipeline end-to-end with FakeLLM,
covering scenarios that the unit tests in test_graph.py don't reach:
  - Multiple jobs in one run
  - Optimize cycle with max iterations hit
  - Error boundary (node throws, other jobs continue)
  - State accumulation across the optimize cycle
"""
import json
from pathlib import Path

import pytest

from pipeline.infrastructure.config import PipelineConfig
from pipeline.infrastructure.graph import build_job_graph
from pipeline.infrastructure.state import (
    JobState,
    TriageDestination,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


class SequentialFakeLLM:
    """FakeLLM that returns responses in sequence (first-match, advancing).

    Unlike FakeLLM (which always returns the first match), this advances
    past consumed responses so successive calls to the same prompt substring
    get different responses.
    """

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
        return None, "SequentialFakeLLM: no matching response"


def _grade_json(grade, assessments=None):
    if assessments is None:
        assessments = [("Python", "core", "DIRECT_HIT", "Strong")]
    per_criterion = [
        {"requirement": req, "tier": tier, "assessment": a, "comment": c}
        for req, tier, a, c in assessments
    ]
    return json.dumps({"grade": grade, "per_criterion": per_criterion, "model": "grader-model"})


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


def _make_config(tmp_paths, llm, logger, dry_run=False):
    """Build a LangGraph RunnableConfig for integration tests."""
    return {
        "configurable": {
            "llm": llm,
            "logger": logger,
            "config": PipelineConfig(dry_run=dry_run),
            "paths": tmp_paths,
        }
    }


def _setup_listing(tmp_paths, slug, jd_text="We need Python."):
    """Create listings/<slug>/ with a [TBD] JD file."""
    job_dir = tmp_paths.listings / slug
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "[TBD] job-description.md").write_text(
        f"<!-- url: https://linkedin.com/{slug} -->\n\n{jd_text}",
        encoding="utf-8",
    )
    return job_dir


def _setup_base_resume(tmp_paths):
    """Create a minimal base resume + LinkedIn for prompt inlining."""
    tmp_paths.base_resume.parent.mkdir(parents=True, exist_ok=True)
    tmp_paths.base_resume.write_text("# Squall Leonhart\n\nSoftware Engineer\n", encoding="utf-8")
    tmp_paths.linkedin_experience.parent.mkdir(parents=True, exist_ok=True)
    tmp_paths.linkedin_experience.write_text("# Squall Leonhart — Full Experience\n\n## Experience\n", encoding="utf-8")


# ─── Integration tests ────────────────────────────────────────────────────────


def test_integration_multiple_jobs(tmp_paths, logger):
    """Two jobs in one run: one trashed (grade < 6), one rejected_job_fit (grade 6-7.9).

    Both exit before the customize step, so no base resume is needed.
    """
    _setup_base_resume(tmp_paths)

    llm = SequentialFakeLLM()
    # good-co: JD grading two-call → 7.0 (rejected_job_fit)
    llm.add_response("JD Grading Reasoning", '{"per_criterion": [{"requirement": "Python", "tier": "core", "assessment": "ADDRESSED", "comment": "Has Python"}]}')
    llm.add_response("JD Grading Scoring", '{"grade": 7.0, "ceiling": 9.0, "core_score": 5.0, "preferred_bonus": 0, "quantitative_base": 5.0, "subjective_adjustment": -0.5, "per_criterion": [{"requirement": "Python", "tier": "core", "assessment": "ADDRESSED", "comment": "Has Python"}], "subjective_justification": "Core PARTIAL.", "justification": "Decent fit."}')
    # bad-co: JD grading two-call → 3.0 (trash)
    llm.add_response("JD Grading Reasoning", '{"per_criterion": [{"requirement": "Rust", "tier": "core", "assessment": "GAP", "comment": "No Rust"}]}')
    llm.add_response("JD Grading Scoring", '{"grade": 3.0, "ceiling": 8.0, "core_score": 0.0, "preferred_bonus": 0, "quantitative_base": 0.0, "subjective_adjustment": -1.0, "per_criterion": [{"requirement": "Rust", "tier": "core", "assessment": "GAP", "comment": "No Rust"}], "subjective_justification": "Core GAP.", "justification": "Poor fit."}')

    _setup_listing(tmp_paths, "good-co", "We need a Python engineer.")
    _setup_listing(tmp_paths, "bad-co", "We need a Rust engineer with 20 years experience.")

    config = _make_config(tmp_paths, llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)

    results = []
    for slug in ("good-co", "bad-co"):
        state = JobState(slug=slug, url=f"https://linkedin.com/{slug}",
                         jd_text="We need an engineer.")
        result = graph.invoke(state, config=config)
        results.append(JobState(**result))

    # good-co: rejected_job_fit (grade 7.0, between 6 and 8)
    assert results[0].final_destination == TriageDestination.REJECTED_JOB_FIT
    assert (tmp_paths.rejected / "[JOB-FIT] good-co").exists()

    # bad-co: trashed (grade 3.0 < 6)
    assert results[1].final_destination == TriageDestination.TRASH
    assert (tmp_paths.trash / "bad-co").exists()


def test_integration_optimize_max_iterations(tmp_paths, logger):
    """Optimize loop hits max iterations (3) without reaching grade 9."""
    seq_llm = SequentialFakeLLM()
    # JD grading two-call -> 8.5
    seq_llm.add_response("JD Grading Reasoning", _reasoning_json([("Python", "core", "DIRECT_HIT", "Strong")]))
    seq_llm.add_response("JD Grading Scoring", _grade_json(8.5, [("Python", "core", "DIRECT_HIT", "Strong")]))
    # v1 customize
    seq_llm.add_response("customizing", "Done")
    # v1 grade -> reasoning + scoring (7.0, below 9)
    seq_llm.add_response("Be realistically harsh", _reasoning_json([("Rust", "core", "GAP", "Missing")]))
    seq_llm.add_response("You are grading resume v", _grade_json(7.0, [("Rust", "core", "GAP", "Missing")]))
    # optimize "can improve?" -> YES
    seq_llm.add_response("YES or NO", "YES")
    # v2 optimize
    seq_llm.add_response("improving", "Done")
    # v2 grade -> reasoning + scoring (7.5, still below 9)
    seq_llm.add_response("Be realistically harsh", _reasoning_json([("Rust", "core", "GAP", "Still missing")]))
    seq_llm.add_response("You are grading resume v", _grade_json(7.5, [("Rust", "core", "GAP", "Still missing")]))
    # optimize "can improve?" -> YES
    seq_llm.add_response("YES or NO", "YES")
    # v3 optimize
    seq_llm.add_response("improving", "Done")
    # v3 grade -> reasoning + scoring (8.0, still below 9)
    seq_llm.add_response("Be realistically harsh", _reasoning_json([("Rust", "core", "PARTIAL", "Better")]))
    seq_llm.add_response("You are grading resume v", _grade_json(8.0, [("Rust", "core", "PARTIAL", "Better")]))
    # optimize "can improve?" -> YES but max_iter=3, so it exits before this call

    _setup_base_resume(tmp_paths)
    _setup_listing(tmp_paths, "max-iter-co")

    # Pre-create v2 and v3 resume files (FakeLLM doesn't write files)
    drafts_dir = tmp_paths.drafts / "max-iter-co"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    (drafts_dir / "[TBD] resume-v2.md").write_text("# v2\n", encoding="utf-8")
    (drafts_dir / "[TBD] resume-v3.md").write_text("# v3\n", encoding="utf-8")

    config = _make_config(tmp_paths, seq_llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)

    state = JobState(slug="max-iter-co", jd_text="We need Python + Rust.")
    result = graph.invoke(state, config=config)
    final = JobState(**result)

    # After 3 iterations, loop exits via max_iter -> truthfulness
    # Grade is 8.0 < 9, so truthfulness skips (verified=False) -> rejected_resume
    assert len(final.resume_versions) == 3
    assert final.latest_grade.grade == 8.0
    assert final.final_destination == TriageDestination.REJECTED_RESUME


def test_integration_error_boundary(tmp_paths, logger):
    """If a node throws, the graph.invoke raises (orchestrator catches)."""
    from pipeline.infrastructure.llm_interface import FakeLLM

    llm = FakeLLM()
    # No responses set up — any LLM call will fail

    _setup_base_resume(tmp_paths)
    _setup_listing(tmp_paths, "error-co")

    config = _make_config(tmp_paths, llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)

    state = JobState(slug="error-co", jd_text="JD text")
    # step4 will fail (LLM error) — graph.invoke raises
    with pytest.raises(RuntimeError, match="JD grading reasoning call failed"):
        graph.invoke(state, config=config)


def test_integration_full_pass_with_file_based_grading(tmp_paths, logger):
    """Full pass using file-based grade JSON (production path, not LLM output).

    The grader writes grade JSON to .grading/<slug>/grade-vN.json.
    This test pre-creates that file to simulate the grader's file write.
    """
    from pipeline.infrastructure.llm_interface import FakeLLM

    llm = FakeLLM()
    # JD grading two-call
    llm.add_response("JD Grading Reasoning", _reasoning_json())
    llm.add_response("JD Grading Scoring", '{"grade": 8.5, "ceiling": 10.0, "core_score": 10.0, "preferred_bonus": 0, "quantitative_base": 10.0, "subjective_adjustment": 0.3, "per_criterion": [{"requirement": "Python", "tier": "core", "assessment": "DIRECT_HIT", "comment": "Strong"}], "subjective_justification": "No gaps.", "justification": "Good fit."}')
    # Customize
    llm.add_response("customizing", "Done customizing.")
    # Resume grading Call 1 (reasoning) — return valid reasoning JSON
    llm.add_response("Be realistically harsh", _reasoning_json())
    # Resume grading Call 2 (scoring) — return non-JSON (simulates file fallback)
    llm.add_response("You are grading resume v", "Grade written to file.")
    # Optimize "can improve?" -> NO
    llm.add_response("YES or NO", "NO")
    # Truthfulness Call 1 (classification) — return valid classification JSON
    llm.add_response("You are a truthfulness", _classification_json([]))
    # Truthfulness Call 2 (synthesis) — return non-JSON (simulates file fallback)
    llm.add_response("You are verifying resume", "Verification written to file.")

    _setup_base_resume(tmp_paths)
    _setup_listing(tmp_paths, "file-grade-co")

    config = _make_config(tmp_paths, llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)

    state = JobState(slug="file-grade-co", jd_text="We need Python.")

    # We need to intercept the grading step to write the grade JSON file.
    # Since we can't intercept mid-graph, we'll use a wrapper LLM that
    # writes the file as a side effect.
    class FileWritingLLM:
        def __init__(self, base_llm, paths):
            self.base = base_llm
            self.paths = paths
            self.calls = []

        def __call__(self, prompt, *, model, timeout, workspace, retries=2, retry_delay=5,
                     export_path=None, permission_mode="dangerous", config_path=None,
                     job_slug=None, step=None):
            self.calls.append(prompt)
            resp, err = self.base(prompt, model=model, timeout=timeout, workspace=workspace)
            # If this is a scoring prompt and Call 2 returned non-JSON, write the grade file
            if "You are grading resume v" in prompt and resp and "file" in resp.lower():
                grading_dir = self.paths.grading / "file-grade-co"
                grading_dir.mkdir(parents=True, exist_ok=True)
                (grading_dir / "grade-v1.json").write_text(_grade_json(9.5))
            # If this is a synthesis prompt and Call 2 returned non-JSON, write the verification file
            if "You are verifying resume" in prompt and resp and "file" in resp.lower():
                veracity_dir = self.paths.veracity / "file-grade-co"
                veracity_dir.mkdir(parents=True, exist_ok=True)
                (veracity_dir / "verification.json").write_text(
                    _verification_json(verified=True)
                )
            return resp, err

    file_llm = FileWritingLLM(llm, tmp_paths)
    config["configurable"]["llm"] = file_llm

    result = graph.invoke(state, config=config)
    final = JobState(**result)

    assert final.final_destination == TriageDestination.READY
    assert final.latest_grade.grade == 9.5
    assert final.verification.verified is True
    assert (tmp_paths.ready / "file-grade-co").exists()


def test_integration_state_accumulates_versions(tmp_paths, logger):
    """Verify resume_versions accumulates correctly through the optimize cycle."""
    seq_llm = SequentialFakeLLM()
    seq_llm.add_response("JD Grading Reasoning", _reasoning_json([("Python", "core", "DIRECT_HIT", "Strong")]))
    seq_llm.add_response("JD Grading Scoring", _grade_json(8.5, [("Python", "core", "DIRECT_HIT", "Strong")]))
    seq_llm.add_response("customizing", "Done")
    seq_llm.add_response("Be realistically harsh", _reasoning_json([("Rust", "core", "GAP", "Missing")]))
    seq_llm.add_response("You are grading resume v", _grade_json(7.0, [("Rust", "core", "GAP", "Missing")]))
    seq_llm.add_response("YES or NO", "YES")
    seq_llm.add_response("improving", "Done")
    seq_llm.add_response("Be realistically harsh", _reasoning_json())
    seq_llm.add_response("You are grading resume v", _grade_json(9.5))
    seq_llm.add_response("YES or NO", "NO")
    seq_llm.add_response("You are a truthfulness", _classification_json([]))
    seq_llm.add_response("You are verifying resume", _verification_json(verified=True))

    _setup_base_resume(tmp_paths)
    _setup_listing(tmp_paths, "accum-co")

    drafts_dir = tmp_paths.drafts / "accum-co"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    (drafts_dir / "[TBD] resume-v2.md").write_text("# v2\n", encoding="utf-8")

    config = _make_config(tmp_paths, seq_llm, logger, dry_run=False)
    graph = build_job_graph(checkpointer=None)

    state = JobState(slug="accum-co", jd_text="We need Python + Rust.")
    result = graph.invoke(state, config=config)
    final = JobState(**result)

    # Two versions created (v1 + v2), accumulated via Annotated[list, add]
    assert len(final.resume_versions) == 2
    assert final.resume_versions[0].version == 1
    assert final.resume_versions[1].version == 2
    # latest_grade is the grade of the latest version (v2 = 9.5)
    assert final.latest_grade.grade == 9.5
    # is_passing should be True (grade >= 9 + verified)
    assert final.is_passing is True
