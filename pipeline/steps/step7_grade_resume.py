"""Step 7 (per-job): Grade resume — LLM grades the customized resume.

This is a LangGraph node. It uses a two-call grading decomposition (ADR-0011):
  - Call 1 (reasoning): extracts requirements + assesses each criterion
  - Call 2 (scoring): takes Call 1's reasoning as fixed input, computes the score

The orchestrator captures stdout from both calls, persists the reasoning JSON,
writes the grade JSON, parses it into a ResumeGrade, renames the resume file
with the grade prefix, appends to .grades.log, and cleans up the grading
workspace.

Both calls use `normal` permission mode (ADR-0010) — the grader cannot write
files or exec commands; it returns JSON via stdout only.

The grade is stored in state.latest_grade (not on the ResumeVersion) to
avoid list-reducer complexity — see the "Resume version grade update" note
in the refactor plan.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from pipeline.infrastructure.file_ops import rename_with_grade
from pipeline.infrastructure.llm_interface import get_deps, parse_llm_json
from pipeline.infrastructure.paths import Paths
from pipeline.infrastructure.protocol_constants import GRADING_REASONING_PROTOCOL, GRADING_SCORING_PROTOCOL
from pipeline.infrastructure.state import JobState, ResumeGrade


# ─── Two-call prompt builders (ADR-0011) ─────────────────────────────────────


def build_reasoning_prompt(
    slug: str, resume_path: str, jd_path: str, version: int
) -> str:
    """Build the Call 1 (reasoning) prompt for the grader model.

    This prompt covers Steps 1-2 of the grading protocol: extract requirements
    from the JD and assess each against the resume. Returns reasoning JSON
    via stdout. The protocol is inlined directly — no file-read tool call.
    """
    return f"""Be realistically harsh — as harsh as a recruiter taking only 30 seconds to skim the resume.

{GRADING_REASONING_PROTOCOL}

Location note: The candidate is based in McLean, VA. Do not flag location as a GAP if the job is in the Washington, DC metro area (including College Park, MD, Arlington, VA, Tysons Corner, VA, Washington, DC, etc.) — these are commutable. Do not flag travel as a GAP if the amount required is below 25%. Do not flag relocation as a GAP; the candidate is willing to relocate for the right role.

Read the resume at {resume_path} and the JD at {jd_path}.

Extract requirements from qualifications sections only. Assign verdicts (DIRECT_HIT / ADDRESSED / PARTIAL / GAP) with one-sentence comments citing specific resume evidence.

Output ONLY valid JSON to stdout (format: {{"per_criterion": [{{"requirement": "...", "tier": "core", "assessment": "DIRECT_HIT", "comment": "..."}}]}}). No reasoning text, no explanations, no markdown fences, no preamble — start with `{{` and end with `}}`. Nothing else. Do NOT write any files. Do NOT compute a score — that is Call 2's job.

If the JD requires security clearance, output CLEARANCE instead of the reasoning JSON."""


def build_scoring_prompt(
    slug: str, version: int, reasoning_json: str
) -> str:
    """Build the Call 2 (scoring) prompt for the grader model.

    This prompt covers Steps 3-4 of the grading protocol: compute the score
    from the fixed reasoning JSON produced by Call 1, and output the final
    grade JSON via stdout. The protocol is inlined directly.
    """
    return f"""{GRADING_SCORING_PROTOCOL}

You are grading resume v{version} for {slug}. The reasoning from Call 1 is provided below as fixed, committed input. You CANNOT change these verdicts — your job is to compute the score from them.

Reasoning JSON from Call 1:
{reasoning_json}

Compute the score using the protocol formula (ceiling, core score, preferred bonus, quantitative base, subjective adjustment, final score). Pass through the per_criterion list unchanged.

This is arithmetic — count the verdicts, apply the formula, output the number. You have all the input above. Do NOT run commands, write scripts, search files, or use any tools. Do NOT read files — everything you need is in this prompt. Such actions will be blocked by a hook and waste time. If you spend more than a few seconds on this, you are overthinking. Just compute and output.

Output ONLY valid JSON to stdout. No reasoning text, no explanations, no markdown fences, no preamble — start with `{{` and end with `}}`. Nothing else. Do NOT write any files. Do NOT rename anything. Do NOT append to any log file. The orchestrator handles all file operations from your stdout output."""


def parse_resume_grade_json(grade_file: Path) -> dict | None:
    """Read and parse a resume grade JSON file from .grading/."""
    if not grade_file.exists():
        return None
    try:
        with open(grade_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def append_grades_log(
    resume_path: str,
    grade: float,
    model: str,
    per_criterion: list,
    grades_log: Path,
    logger,
) -> None:
    """Append a grading event to .grades.log (audit trail).

    hits = count of DIRECT_HIT verdicts, gaps = count of GAP verdicts.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hits = sum(1 for c in per_criterion if c.assessment.value == "DIRECT_HIT")
    gaps = sum(1 for c in per_criterion if c.assessment.value == "GAP")
    line = f"{timestamp} | {resume_path} | grade={grade} | model={model} | hits={hits} gaps={gaps}\n"
    with open(grades_log, "a") as f:
        f.write(line)
    logger.debug(f"Grades log appended: {line.strip()}")


def _find_jd_in_drafts(slug: str, paths: Paths) -> Path | None:
    """Find the JD file in drafts/<slug>/."""
    job_dir = paths.drafts / slug
    if not job_dir.exists():
        return None
    jd_files = list(job_dir.glob("*job-description.md"))
    return jd_files[0] if jd_files else None


def step7_grade_resume_node(state: JobState, config: RunnableConfig) -> dict:
    """Grade the latest resume using the grader LLM.

    Returns partial state with latest_grade set. Raises on LLM failure,
    missing files, or unparseable grade JSON.

    In dry_run, skips grading entirely (returns empty dict).
    """
    deps = get_deps(config)
    slug = state.slug
    latest = state.latest_resume

    if not latest:
        raise RuntimeError(f"{slug}: no resume to grade")

    version = latest.version
    resume_path = latest.path

    if deps.config.dry_run:
        deps.logger.info(f"  {slug}: dry-run grade v{version} (skipped)")
        return {}

    # Find the JD file
    jd_path = _find_jd_in_drafts(slug, deps.paths)
    if not jd_path:
        raise RuntimeError(f"{slug}: JD file not found for resume grading")

    # Create grading workspace
    grading_dir = deps.paths.grading / slug
    grading_dir.mkdir(parents=True, exist_ok=True)

    # Build relative paths for prompts
    resume_rel = str(resume_path.relative_to(deps.paths.hunter_dir))
    jd_rel = str(jd_path.relative_to(deps.paths.hunter_dir))

    # ─── Call 1: Reasoning (ADR-0011) ──────────────────────────────────
    reasoning_prompt = build_reasoning_prompt(slug, resume_rel, jd_rel, version)
    reasoning_output, reasoning_error = deps.llm(
        reasoning_prompt,
        model=deps.config.models.grader,
        timeout=deps.config.llm_timeout_seconds,
        retries=deps.config.llm_retries,
        retry_delay=deps.config.llm_retry_delay,
        workspace=str(deps.paths.hunter_dir),
        permission_mode="normal",
    )

    if reasoning_error:
        raise RuntimeError(
            f"{slug}: resume grading reasoning call failed: {reasoning_error}"
        )

    # Check for clearance detection
    if "CLEARANCE" in reasoning_output.upper():
        deps.logger.info(f"  {slug}: clearance detected during grading → trash")
        resume_grade = ResumeGrade(grade=0, per_criterion=[])
        # Still need to handle the resume file — leave ungraded
        return {"latest_grade": resume_grade}

    # Parse reasoning JSON from stdout
    reasoning_data = parse_llm_json(reasoning_output)
    if reasoning_data is None:
        raise RuntimeError(
            f"{slug}: could not parse reasoning JSON from Call 1 stdout"
        )

    # Persist reasoning JSON for auditability (ADR-0011)
    reasoning_file = deps.paths.grading / slug / f"reasoning-v{version}.json"
    reasoning_file.parent.mkdir(parents=True, exist_ok=True)
    with open(reasoning_file, "w") as f:
        json.dump(reasoning_data, f, indent=2)

    # ─── Call 2: Scoring (ADR-0011) ────────────────────────────────────
    reasoning_json_str = json.dumps(reasoning_data)
    scoring_prompt = build_scoring_prompt(slug, version, reasoning_json_str)
    scoring_output, scoring_error = deps.llm(
        scoring_prompt,
        model=deps.config.models.grader,
        timeout=deps.config.llm_timeout_seconds,
        retries=deps.config.llm_retries,
        retry_delay=deps.config.llm_retry_delay,
        workspace=str(deps.paths.hunter_dir),
        permission_mode="normal",
    )

    if scoring_error:
        raise RuntimeError(
            f"{slug}: resume grading scoring call failed: {scoring_error}"
        )

    # Parse grade JSON from stdout (primary path — ADR-0010)
    grade_data = parse_llm_json(scoring_output)

    # Fallback: read from file (transitional compat for any LLM that still writes)
    if grade_data is None:
        grade_file = deps.paths.grading / slug / f"grade-v{version}.json"
        grade_data = parse_resume_grade_json(grade_file)

    if grade_data is None:
        raise RuntimeError(
            f"{slug}: could not parse grade JSON from scoring call stdout"
            f" or read from .grading/{slug}/grade-v{version}.json"
        )

    # Orchestrator writes the grade JSON file (ADR-0010)
    grade_file = deps.paths.grading / slug / f"grade-v{version}.json"
    grade_file.parent.mkdir(parents=True, exist_ok=True)
    with open(grade_file, "w") as f:
        json.dump(grade_data, f, indent=2)

    # Parse into ResumeGrade (validates grade range, enum values)
    try:
        resume_grade = ResumeGrade.model_validate(grade_data)
    except Exception as e:
        raise RuntimeError(f"{slug}: could not parse grade JSON: {e}") from e

    grade = resume_grade.grade
    deps.logger.info(f"  {slug}: resume v{version} grade = {grade}")

    # Append to audit log
    append_grades_log(
        resume_rel,
        grade,
        deps.config.models.grader,
        resume_grade.per_criterion,
        deps.paths.grades_log,
        deps.logger,
    )

    # Rename the resume file with the grade
    if resume_path.exists():
        rename_with_grade(resume_path, grade)

    # Clean up grading workspace
    shutil.rmtree(grading_dir, ignore_errors=True)

    return {"latest_grade": resume_grade}
