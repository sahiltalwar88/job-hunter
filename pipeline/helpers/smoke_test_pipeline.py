#!/usr/bin/env python3
"""Smoke test — run the full per-job pipeline with FakeLLM.

Creates a temp workspace, sets up a fake job, and runs the entire LangGraph
pipeline (ingest -> grade JD -> triage -> customize -> grade resume ->
optimize -> truthfulness -> ready) with canned LLM responses. Real file I/O
(not dry_run) so you can watch folders move through the pipeline.

Usage:
    python3 -m pipeline.helpers.smoke_test_pipeline

This takes ~2 seconds. The 3 slow LLM agents (customizer, grader, veracity)
are faked with instant canned responses.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from pipeline.infrastructure.config import PipelineConfig
from pipeline.infrastructure.graph import build_job_graph
from pipeline.infrastructure.llm_interface import FakeLLM
from pipeline.infrastructure.paths import Paths
from pipeline.infrastructure.state import JobState, TriageDestination


def main():
    tmpdir = Path(tempfile.mkdtemp(prefix="job-hunter-smoke-"))
    print(f"Temp workspace: {tmpdir}")
    print()

    paths = Paths.from_hunter_dir(tmpdir)

    # ── Set up the workspace ──
    # Base resume (needed by step6_customize)
    paths.base_resume.parent.mkdir(parents=True, exist_ok=True)
    paths.base_resume.write_text("# Squall Leonhart\n\nSenior Engineering Leader\n\n- Python, Go, Rust\n- Led teams of 50+\n")

    # LinkedIn experience (referenced by prompts, not read by FakeLLM)
    paths.linkedin_experience.parent.mkdir(parents=True, exist_ok=True)
    paths.linkedin_experience.write_text("# LinkedIn Experience\n\n- Director of Engineering at TechCo\n")

    # Customization protocol (referenced by prompts, not read by FakeLLM)
    config_dir = tmpdir / "_config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "resume-customization-protocol.md").write_text("# Customization Protocol\n")
    (config_dir / "grading-protocol.md").write_text("# Grading Protocol\n")
    (config_dir / "veracity-protocol.md").write_text("# Veracity Protocol\n")

    # ── FakeLLM with canned responses for all 5 LLM calls ──
    # The FakeLLM matches by substring (first match wins).
    # We need responses for:
    # 1. JD grading prompt ("grading a job") -> grade 8.5
    # 2. Customize prompt ("customizing") -> "Done" (we pre-create the resume file)
    # 3. Resume grading prompt ("Be realistically harsh") -> grade 9.5 JSON
    # 4. Optimize "can improve?" prompt ("YES or NO") -> NO (exit loop)
    # 5. Truthfulness prompt ("truthfulness") -> verified=True JSON

    grade_json = json.dumps({
        "grade": 9.5,
        "per_criterion": [
            {"requirement": "Python", "tier": "core", "assessment": "DIRECT_HIT", "comment": "Strong"},
            {"requirement": "Leadership", "tier": "core", "assessment": "DIRECT_HIT", "comment": "Led 50+ engineers"},
        ],
        "model": "grader-model",
    })

    verification_json = json.dumps({
        "verified": True,
        "unverifiable_claims": [],
    })

    llm = FakeLLM(responses={
        "grading a job": "GRADE: 8.5\nJUSTIFICATION: Strong fit — senior engineering leader with Python.",
        "customizing": "Done. Resume customized.",
        "Be realistically harsh": grade_json,
        "YES or NO": "NO",
        "truthfulness": verification_json,
    })

    # ── Config (not dry_run — we want real file moves) ──
    config = PipelineConfig(dry_run=False)

    # ── Logger that prints to stdout ──
    import logging
    logger = logging.getLogger("smoke")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("  %(message)s"))
    logger.addHandler(handler)

    # ── Build the graph ──
    graph = build_job_graph(checkpointer=None)

    # ── Initial state — a fake job ──
    slug = "acme-corp-staff-engineer"
    state = JobState(
        slug=slug,
        url="https://linkedin.com/jobs/123456",
        company="Acme Corp",
        title="Staff Engineer",
        jd_text="We are looking for a Staff Engineer with strong Python experience and engineering leadership background. Must have led teams of 20+ engineers.",
    )

    node_config = {
        "configurable": {
            "llm": llm,
            "logger": logger,
            "config": config,
            "paths": paths,
        }
    }

    # ── Pre-create the listing folder + JD file (step3_ingest would do this,
    # but we need it before the graph runs so step4 can read/rename it) ──
    # Actually, step3_ingest IS the first node — it creates the listing.
    # But step3_ingest needs the JD text from state, which we set above.
    # So the graph should handle it. Let's NOT pre-create and let the graph do it.

    # However, step6_customize pre-copies the base resume and expects the LLM
    # to edit it. FakeLLM doesn't edit files. So we need to pre-create the
    # resume file AFTER step6 runs but BEFORE step7 reads it.
    # Solution: wrap FakeLLM to write the resume file as a side effect.

    class SmokeLLM:
        """FakeLLM that also writes files the real LLM would have written."""
        def __init__(self, base_llm, paths, slug):
            self.base = base_llm
            self.paths = paths
            self.slug = slug
            self.calls = []

        def __call__(self, prompt, *, model, timeout, workspace):
            self.calls.append(prompt)
            resp, err = self.base(prompt, model=model, timeout=timeout, workspace=workspace)

            # If customize prompt: write the resume file (simulating LLM editing it)
            if "customizing" in prompt and resp:
                version = 1  # first customization
                # Extract version from prompt
                import re
                m = re.search(r"resume-v(\d+)", prompt)
                if m:
                    version = int(m.group(1))
                resume_path = self.paths.drafts / self.slug / f"[TBD] resume-v{version}.md"
                resume_path.parent.mkdir(parents=True, exist_ok=True)
                resume_path.write_text(f"# Squall Leonhart\n\nStaff Engineer | Acme Corp\n\n- Python, Go, Rust\n- Led teams of 50+\n- Built distributed systems at scale\n")
                print(f"  [smoke] Wrote resume file: {resume_path.name}")

            # If grading prompt: write grade JSON to .grading/ (production path)
            if "Be realistically harsh" in prompt and resp:
                import re
                m = re.search(r"grade-v(\d+)\.json", prompt)
                v = m.group(1) if m else "1"
                grading_dir = self.paths.grading / self.slug
                grading_dir.mkdir(parents=True, exist_ok=True)
                grade_file = grading_dir / f"grade-v{v}.json"
                grade_file.write_text(resp)
                print(f"  [smoke] Wrote grade JSON: {grade_file.name}")

            # If truthfulness prompt: write verification JSON to .veracity/
            if "truthfulness" in prompt and resp:
                veracity_dir = self.paths.veracity / self.slug
                veracity_dir.mkdir(parents=True, exist_ok=True)
                (veracity_dir / "verification.json").write_text(resp)
                print(f"  [smoke] Wrote verification JSON")

            return resp, err

    smoke_llm = SmokeLLM(llm, paths, slug)
    node_config["configurable"]["llm"] = smoke_llm

    # ── Run the graph ──
    print("=" * 60)
    print(f"Running full pipeline for: {slug}")
    print(f"  JD: Staff Engineer at Acme Corp")
    print(f"  Expected: grade 8.5 -> drafts -> customize -> grade 9.5 -> verified -> ready")
    print("=" * 60)
    print()

    result = graph.invoke(state, config=node_config)

    # ── Print the results ──
    final = JobState(**result)

    print()
    print("=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print()
    print(f"Final state:")
    print(f"  slug:               {final.slug}")
    print(f"  jd_grade:           {final.jd_grade.grade if final.jd_grade else 'None'}")
    print(f"  jd_grade.clearance: {final.jd_grade.is_clearance if final.jd_grade else 'N/A'}")
    print(f"  triage:             {final.triage}")
    print(f"  resume_versions:    {len(final.resume_versions)}")
    for rv in final.resume_versions:
        print(f"    v{rv.version}: {rv.path.name}")
    print(f"  latest_grade:       {final.latest_grade.grade if final.latest_grade else 'None'}")
    print(f"  optimize_can_improve: {final.optimize_can_improve}")
    print(f"  verification:       {final.verification.verified if final.verification else 'None'}")
    print(f"  final_destination:  {final.final_destination}")
    print(f"  is_passing:         {final.is_passing}")
    print()

    # ── Show where files ended up ──
    print("Filesystem state:")
    for label, base in [("listings", paths.listings), ("drafts", paths.drafts),
                         ("ready", paths.ready), ("trash", paths.trash),
                         ("rejected", paths.rejected)]:
        if base.exists():
            for item in sorted(base.rglob("*")):
                if item.is_file():
                    rel = item.relative_to(tmpdir)
                    print(f"  {label}/  {rel}")

    print()
    print(f"Grades log: {'exists' if paths.grades_log.exists() else 'missing'}")
    if paths.grades_log.exists():
        for line in paths.grades_log.read_text().strip().split("\n"):
            print(f"  {line}")

    # ── Verify expectations ──
    print()
    print("Verification:")
    checks = [
        ("JD graded 8.5", final.jd_grade and final.jd_grade.grade == 8.5),
        ("Moved to drafts", final.triage == TriageDestination.DRAFTS),
        ("Resume v1 created", len(final.resume_versions) == 1),
        ("Resume graded 9.5", final.latest_grade and final.latest_grade.grade == 9.5),
        ("Optimize exited (NO)", final.optimize_can_improve is False),
        ("Truthfulness verified", final.verification and final.verification.verified),
        ("Final destination: ready", final.final_destination == TriageDestination.READY),
        ("Folder in ready/", (paths.ready / slug).exists()),
    ]
    all_pass = True
    for label, passed in checks:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  [{status}] {label}")

    print()
    if all_pass:
        print("ALL CHECKS PASSED — pipeline works end-to-end!")
    else:
        print("SOME CHECKS FAILED — see above")

    # ── Cleanup ──
    print()
    print(f"Temp workspace preserved for inspection: {tmpdir}")
    print(f"  rm -rf {tmpdir}  # when done")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
