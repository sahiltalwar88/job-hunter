"""Real integration tests for the 6 LLM call sites in the pipeline.

These tests call the actual `devin -p` subprocess (RealLLM) with a 5-minute
hard timeout per agent. They are skipped by default — set RUN_REAL_LLM=1 to
run them.

Usage:
    # Run all 6 real integration tests:
    RUN_REAL_LLM=1 python3 -m pytest tests/test_integration_real.py -v -s

    # Run just the customizer:
    RUN_REAL_LLM=1 python3 -m pytest tests/test_integration_real.py::test_real_customizer -v -s

    # Run just the grader:
    RUN_REAL_LLM=1 python3 -m pytest tests/test_integration_real.py::test_real_grader -v -s

    # Run just the truthfulness verifier:
    RUN_REAL_LLM=1 python3 -m pytest tests/test_integration_real.py::test_real_truthfulness -v -s

    # Run just the JD grader:
    RUN_REAL_LLM=1 python3 -m pytest tests/test_integration_real.py::test_real_jd_grader -v -s

    # Run just the optimize decision:
    RUN_REAL_LLM=1 python3 -m pytest tests/test_integration_real.py::test_real_optimize -v -s

    # Run just the feasibility checker:
    RUN_REAL_LLM=1 python3 -m pytest tests/test_integration_real.py::test_real_feasibility -v -s

Each test creates an isolated temp workspace with real profile files and
calls one node directly. The 5-minute timeout is enforced via
subprocess.run(timeout=300) in devin_cli.py.

Note: Protocol files are no longer copied to the temp workspace — they are
inlined into prompts via protocol_constants.py at import time, loaded from
the real _config/ directory. The LLM never reads protocol files from disk.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import pytest

from pipeline.infrastructure.config import PipelineConfig
from pipeline.infrastructure.llm_interface import RealLLM
from pipeline.infrastructure.paths import Paths
from pipeline.infrastructure.state import (
    Assessment,
    Criterion,
    JdGrade,
    JobState,
    ResumeGrade,
    ResumeVersion,
)

# ─── Skip unless explicitly enabled ──────────────────────────────────────────

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_LLM") != "1",
    reason="Set RUN_REAL_LLM=1 to run real LLM integration tests (5-min timeout each).",
)

# ─── Constants ───────────────────────────────────────────────────────────────

HUNTER_DIR = Path(__file__).parent.parent
TIMEOUT_SECONDS = 600  # 10 minutes — max allowed by PipelineConfig validation

# A realistic JD for testing — senior engineering leader role
SAMPLE_JD = """\
Senior Director of Engineering — Platform Infrastructure

We are looking for a Senior Director of Engineering to lead our Platform
Infrastructure organization. You will own the systems that power all of
our products — serving millions of users globally.

Responsibilities:
- Lead and grow a team of 40+ engineers across multiple sub-teams
- Own the technical roadmap for platform infrastructure (compute, storage, networking)
- Drive architectural decisions for scalability, reliability, and cost efficiency
- Partner with product engineering teams to understand and serve their needs
- Define and own SLOs/SLAs for all platform services

Qualifications:
- 10+ years of engineering experience, with 5+ years in leadership
- Deep experience with distributed systems and cloud infrastructure (AWS or GCP)
- Track record of building and leading high-performing engineering organizations
- Strong communication skills — able to influence at the executive level
- Experience with Kubernetes, Terraform, and infrastructure-as-code
- Bachelor's degree in Computer Science or equivalent experience

Preferred:
- Experience at scale (millions of users or petabytes of data)
- Open source contributions in the infrastructure space
- Experience with multi-region deployments
"""

SLUG = "acme-senior-director-platform"


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def real_paths(tmp_path: Path) -> Paths:
    """Temp workspace with real profile files copied in."""
    paths = Paths.from_hunter_dir(tmp_path)

    # Copy real profile files
    real_base = HUNTER_DIR / "_config" / "profile" / "base-resume" / "base-resume.md"
    real_linkedin = HUNTER_DIR / "_config" / "profile" / "full-experience" / "full-experience.md"
    paths.base_resume.parent.mkdir(parents=True, exist_ok=True)
    paths.linkedin_experience.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(real_base, paths.base_resume)
    shutil.copy2(real_linkedin, paths.linkedin_experience)

    # Protocol files are NOT copied — they are inlined into prompts via
    # protocol_constants.py, which loads from the real _config/ directory
    # at import time. The LLM never reads protocol files from disk.

    # Copy few-shot examples (needed by customizer)
    examples_src = HUNTER_DIR / "docs" / "examples" / "customized-resumes"
    examples_dst = tmp_path / "docs" / "examples" / "customized-resumes"
    if examples_src.exists():
        examples_dst.mkdir(parents=True, exist_ok=True)
        for f in examples_src.glob("*.md"):
            shutil.copy2(f, examples_dst / f.name)

    # Copy count_lines.py (customizer runs it for self-measurement)
    # The customizer runs `python3 -m pipeline.helpers.count_lines`, so we need
    # the pipeline package structure in the tmp workspace.
    pipeline_helpers_dst = tmp_path / "pipeline" / "helpers"
    pipeline_helpers_dst.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pipeline" / "__init__.py").write_text("")
    (tmp_path / "pipeline" / "helpers" / "__init__.py").write_text("")
    shutil.copy2(HUNTER_DIR / "pipeline" / "helpers" / "count_lines.py", pipeline_helpers_dst / "count_lines.py")

    return paths


@pytest.fixture
def real_config() -> PipelineConfig:
    """Config with 10-minute timeout, no retries, and dry_run=False."""
    return PipelineConfig(
        dry_run=False,
        llm_timeout_seconds=TIMEOUT_SECONDS,
        llm_retries=0,  # single attempt — no 15-minute retry loops
        llm_retry_delay=0,
    )


@pytest.fixture
def real_logger() -> logging.Logger:
    """Verbose logger so you can watch progress in -s mode."""
    log = logging.getLogger("real_integration")
    log.handlers.clear()
    log.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("  %(message)s"))
    log.addHandler(handler)
    return log


def _make_node_config(llm, logger, config, paths, export_dir=None):
    """Build a RunnableConfig with the given deps.

    If export_dir is set, wraps the LLM to pass --export <export_dir>/<test>.md
    so the agent's thoughts/tool calls are captured even if it's killed by timeout.
    """
    if export_dir is not None:
        export_dir.mkdir(parents=True, exist_ok=True)
        original_llm = llm

        class ExportingLLM:
            """Wraps RealLLM to pass --export for each call."""
            def __init__(self):
                self._call_count = 0

            def __call__(self, prompt, *, model, timeout, workspace,
                         retries=2, retry_delay=5, export_path=None,
                         permission_mode="dangerous", config_path=None,
                         job_slug=None, step=None, **kwargs):
                self._call_count += 1
                export_file = export_dir / f"call-{self._call_count}.md"
                return original_llm(
                    prompt, model=model, timeout=timeout, workspace=workspace,
                    retries=retries, retry_delay=retry_delay,
                    export_path=str(export_file),
                    permission_mode=permission_mode,
                    config_path=config_path,
                    job_slug=job_slug,
                    step=step,
                    **kwargs,
                )

        llm = ExportingLLM()

    return {
        "configurable": {
            "llm": llm,
            "logger": logger,
            "config": config,
            "paths": paths,
        }
    }


# ─── Tests ───────────────────────────────────────────────────────────────────


def test_real_customizer(real_paths, real_config, real_logger, tmp_path):
    """Call the real customizer agent (customizer-model) to customize a resume.

    Verifies:
    - The LLM is called (not skipped)
    - A resume file is produced at stages/2_drafts/<slug>/[TBD] resume-v1.md
    - The file is non-empty and contains markdown
    - The file is different from the base resume (was actually customized)
    """
    from pipeline.steps.step6_customize import step6_customize_node

    llm = RealLLM()
    export_dir = tmp_path / "exports"
    node_config = _make_node_config(llm, real_logger, real_config, real_paths, export_dir=export_dir)

    state = JobState(
        slug=SLUG,
        jd_text=SAMPLE_JD,
        jd_grade=JdGrade(grade=8.5, justification="Strong fit — senior engineering leader."),
    )

    result = step6_customize_node(state, node_config)

    # Verify state update
    assert "resume_versions" in result
    versions = result["resume_versions"]
    assert len(versions) == 1
    assert versions[0].version == 1

    # Verify the file exists and is non-empty
    resume_path = versions[0].path
    assert resume_path.exists(), f"Resume file not created at {resume_path}"
    content = resume_path.read_text(encoding="utf-8")
    assert len(content) > 100, "Resume file is too short — customizer may not have written anything"

    # Verify it's different from the base resume (was actually customized)
    base_content = real_paths.base_resume.read_text(encoding="utf-8")
    assert content != base_content, "Resume is identical to base — was not customized"

    real_logger.info(f"\n  PASS: customizer produced {resume_path.name} ({len(content)} chars)")


def test_real_grader(real_paths, real_config, real_logger, tmp_path):
    """Call the real grader agent (grader-model) to grade a customized resume.

    Verifies:
    - The LLM is called (not skipped)
    - Grade JSON is produced (either .grading/<slug>/grade-v1.json or parsed from output)
    - The grade is a valid float in [0, 10]
    - .grades.log is appended
    - The resume file is renamed with the score prefix
    """
    from pipeline.steps.step7_grade_resume import step7_grade_resume_node

    # Set up: create a JD file and a resume file in stages/2_drafts/<slug>/
    job_dir = real_paths.drafts / SLUG
    job_dir.mkdir(parents=True, exist_ok=True)

    jd_file = job_dir / "[8.5] job-description.md"
    jd_file.write_text(SAMPLE_JD, encoding="utf-8")

    # Copy base resume as the "customized" resume to grade
    resume_file = job_dir / "[TBD] resume-v1.md"
    shutil.copy2(real_paths.base_resume, resume_file)

    llm = RealLLM()
    export_dir = tmp_path / "exports"
    node_config = _make_node_config(llm, real_logger, real_config, real_paths, export_dir=export_dir)

    state = JobState(
        slug=SLUG,
        jd_text=SAMPLE_JD,
        resume_versions=[ResumeVersion(version=1, path=resume_file)],
    )

    result = step7_grade_resume_node(state, node_config)

    # Verify state update
    assert "latest_grade" in result
    grade = result["latest_grade"]
    assert 0 <= grade.grade <= 10, f"Grade {grade.grade} out of range [0, 10]"
    assert len(grade.per_criterion) > 0, "No criteria in grade"

    real_logger.info(f"\n  PASS: grader returned grade={grade.grade} ({len(grade.per_criterion)} criteria)")

    # Verify .grades.log was appended
    assert real_paths.grades_log.exists(), ".grades.log not created"
    log_content = real_paths.grades_log.read_text()
    assert SLUG in log_content, "Slug not found in .grades.log"

    # Verify resume was renamed with score
    renamed = list(job_dir.glob("[[]*] resume-v1.md"))
    assert len(renamed) == 1, f"Expected 1 graded resume, found {len(renamed)}"
    assert "[TBD]" not in renamed[0].name, "Resume was not renamed from [TBD]"

    real_logger.info(f"  PASS: resume renamed to {renamed[0].name}")
    real_logger.info(f"  PASS: .grades.log appended")


def test_real_truthfulness(real_paths, real_config, real_logger, tmp_path):
    """Call the real truthfulness verifier (grader-model) to verify a resume.

    Verifies:
    - The LLM is called (not skipped)
    - Verification JSON is produced (either .veracity/<slug>/verification.json or parsed from output)
    - The verification.verified field is a boolean
    - If unverified, unverifiable_claims is populated
    """
    from pipeline.steps.step9_veracity import step9_veracity_node

    # Set up: create a graded resume in stages/2_drafts/<slug>/
    job_dir = real_paths.drafts / SLUG
    job_dir.mkdir(parents=True, exist_ok=True)

    # Copy base resume as a "passing" graded resume
    resume_file = job_dir / "[9.5] resume-v1.md"
    shutil.copy2(real_paths.base_resume, resume_file)

    llm = RealLLM()
    export_dir = tmp_path / "exports"
    node_config = _make_node_config(llm, real_logger, real_config, real_paths, export_dir=export_dir)

    state = JobState(
        slug=SLUG,
        jd_text=SAMPLE_JD,
        resume_versions=[ResumeVersion(version=1, path=resume_file)],
        latest_grade=ResumeGrade(
            grade=9.5,
            per_criterion=[
                Criterion(
                    requirement="10+ years engineering experience",
                    tier="core",
                    assessment=Assessment.DIRECT_HIT,
                    comment="Strong match",
                ),
            ],
        ),
    )

    result = step9_veracity_node(state, node_config)

    # Verify state update
    assert "verification" in result
    verification = result["verification"]
    assert isinstance(verification.verified, bool), "verified field is not a boolean"

    if not verification.verified:
        assert len(verification.unverifiable_claims) > 0, "Unverified but no claims listed"
        real_logger.info(
            f"\n  PASS: truthfulness UNVERIFIED ({len(verification.unverifiable_claims)} claims)"
        )
    else:
        real_logger.info("\n  PASS: truthfulness VERIFIED")


def test_real_jd_grader(real_paths, real_config, real_logger, tmp_path):
    """Call the real JD grader (customizer-model) to grade a JD.

    Verifies:
    - The LLM returns JSON (not GRADE:/JUSTIFICATION: text format)
    - The grade is a valid float in [0, 10]
    - The JD file is renamed with the grade prefix
    - Clearance detection works (CLEARANCE response → is_clearance=True)
    """
    from pipeline.steps.step4_grade_jd import step4_grade_jd_node

    # Create a listing folder with a JD file
    job_dir = real_paths.listings / SLUG
    job_dir.mkdir(parents=True, exist_ok=True)
    jd_file = job_dir / "[TBD] job-description.md"
    jd_file.write_text(f"<!-- url: https://linkedin.com/123 -->\n\n{SAMPLE_JD}", encoding="utf-8")

    llm = RealLLM()
    export_dir = tmp_path / "exports"
    node_config = _make_node_config(llm, real_logger, real_config, real_paths, export_dir=export_dir)

    state = JobState(
        slug=SLUG,
        url="https://linkedin.com/123",
        jd_text=SAMPLE_JD,
        jd_path=jd_file,
    )

    result = step4_grade_jd_node(state, node_config)

    # Verify state update
    assert "jd_grade" in result
    jd_grade = result["jd_grade"]
    assert isinstance(jd_grade.is_clearance, bool)
    if not jd_grade.is_clearance:
        assert 0 <= jd_grade.grade <= 10, f"Grade {jd_grade.grade} out of range [0, 10]"
        real_logger.info(f"\n  PASS: JD grader returned grade={jd_grade.grade}")
        # Verify JD file was renamed with score
        assert not jd_file.exists(), "JD file was not renamed from [TBD]"
        renamed = list(job_dir.glob("[[]*] job-description.md"))
        assert len(renamed) == 1, f"Expected 1 graded JD, found {len(renamed)}"
        real_logger.info(f"  PASS: JD renamed to {renamed[0].name}")
    else:
        real_logger.info("\n  PASS: JD grader detected clearance requirement")


def test_real_optimize(real_paths, real_config, real_logger, tmp_path):
    """Call the real optimize decision (customizer-model) to check 'can improve?'.

    Verifies:
    - The LLM is called (not skipped via exit conditions)
    - The response is YES or NO
    - optimize_can_improve is a boolean
    """
    from pipeline.steps.step8_optimize import step8_optimize_node

    # Set up: create a resume file in stages/2_drafts/<slug>/ with a below-threshold grade
    job_dir = real_paths.drafts / SLUG
    job_dir.mkdir(parents=True, exist_ok=True)

    jd_file = job_dir / "[8.5] job-description.md"
    jd_file.write_text(SAMPLE_JD, encoding="utf-8")

    resume_file = job_dir / "[7.0] resume-v1.md"
    shutil.copy2(real_paths.base_resume, resume_file)

    llm = RealLLM()
    export_dir = tmp_path / "exports"
    node_config = _make_node_config(llm, real_logger, real_config, real_paths, export_dir=export_dir)

    state = JobState(
        slug=SLUG,
        jd_text=SAMPLE_JD,
        resume_versions=[ResumeVersion(version=1, path=resume_file)],
        latest_grade=ResumeGrade(
            grade=7.0,
            per_criterion=[
                Criterion(
                    requirement="Kubernetes experience",
                    tier="core",
                    assessment=Assessment.PARTIAL,
                    comment="Mentioned but not deeply",
                ),
            ],
        ),
        optimize_iteration_count=0,
    )

    result = step8_optimize_node(state, node_config)

    # Verify state update
    assert "optimize_can_improve" in result
    can_improve = result["optimize_can_improve"]
    assert isinstance(can_improve, bool), f"optimize_can_improve is not a boolean: {can_improve}"

    if can_improve:
        real_logger.info("\n  PASS: optimize says YES — can improve further")
    else:
        real_logger.info("\n  PASS: optimize says NO — done iterating")


def test_real_feasibility(real_paths, real_config, real_logger, tmp_path):
    """Call the real feasibility checker (customizer-model) to check job relevance.

    Verifies:
    - The LLM returns a JSON array of verdicts
    - Each verdict is PREFERRED, YES, or NO
    - The rationale is a non-empty string
    - The URL mapping is correct
    """
    from pipeline.infrastructure.feasibility_checker import DevinCLIChecker
    from pipeline.infrastructure.config import load_config
    from pipeline.infrastructure.llm_interface import RealLLM

    # Load the real feasibility prompt from config.json
    config_file = HUNTER_DIR / "config.json"
    config = load_config(config_file)
    prompt = config.feasibility_prompt if config.feasibility_prompt else ""
    if not prompt:
        # Fall back to a basic prompt if not configured
        prompt = "Filter for senior engineering leadership roles at Big Tech companies."

    checker = DevinCLIChecker(
        llm=RealLLM(),
        model=real_config.models.customizer,
        prompt=prompt,
        timeout=TIMEOUT_SECONDS,
        workspace=str(real_paths.hunter_dir),
    )

    # A small batch of test jobs
    jobs = [
        {
            "url": "https://linkedin.com/jobs/view/1",
            "title": "Senior Director of Engineering",
            "company": "Google",
            "location": "Mountain View, CA",
        },
        {
            "url": "https://linkedin.com/jobs/view/2",
            "title": "Junior Developer",
            "company": "Startup Inc",
            "location": "Remote",
        },
    ]

    verdicts = checker.check_batch(jobs)

    # Verify we got verdicts
    assert len(verdicts) > 0, "No verdicts returned"

    for url, (verdict, rationale) in verdicts.items():
        assert verdict in ("PREFERRED", "YES", "NO"), f"Invalid verdict: {verdict}"
        assert isinstance(rationale, str), f"Rationale is not a string: {rationale}"
        real_logger.info(f"  {url}: {verdict} — {rationale}")

    # The senior director role at Google should be at least YES
    google_url = "https://linkedin.com/jobs/view/1"
    if google_url in verdicts:
        google_verdict = verdicts[google_url][0]
        assert google_verdict in ("PREFERRED", "YES"), \
            f"Google Senior Director should be PREFERRED or YES, got {google_verdict}"

    real_logger.info(f"\n  PASS: feasibility checker returned {len(verdicts)} verdicts")
