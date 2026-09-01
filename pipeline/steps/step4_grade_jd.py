"""Step 4 (per-job): Grade JD — LLM grades the JD + detects clearance.

This is a LangGraph node. It uses a two-call grading decomposition (same
rubric as the resume grader, but evaluating the candidate's full background
against the JD):
  - Call 1 (reasoning): extracts requirements + assesses each criterion
    against the base resume + LinkedIn
  - Call 2 (scoring): takes Call 1's reasoning as fixed input, computes
    the score

Source documents (base resume, LinkedIn) are pre-fed into the prompt to
eliminate file-read tool calls.

Clearance detection is LLM-only (ADR-0007 — no regex). If the LLM returns
CLEARANCE, the jd_grade.is_clearance flag is set and the conditional edge
routes to trash.
"""
from __future__ import annotations

import json

from langchain_core.runnables import RunnableConfig

from pipeline.infrastructure.llm_interface import get_deps, parse_llm_json
from pipeline.infrastructure.state import JobState, JdGrade
from pipeline.infrastructure.file_ops import rename_with_grade, strip_url_metadata
from pipeline.infrastructure.injection_filter import scrub_jd_text, wrap_jd_content
from pipeline.infrastructure.protocol_constants import (
    JD_GRADING_REASONING_PROTOCOL,
    JD_GRADING_SCORING_PROTOCOL,
)


# ─── Two-call prompt builders ────────────────────────────────────────────────


def build_jd_reasoning_prompt(
    slug: str, jd_text: str,
    base_resume_content: str, linkedin_content: str,
) -> str:
    """Build the Call 1 (reasoning) prompt for the JD grader.

    Evaluates the candidate's full background (base resume + LinkedIn)
    against the JD requirements. Source documents are pre-fed.

    JD text is scrubbed of injection patterns and wrapped in structural
    isolation tags before being interpolated into the prompt (E4).
    """
    scrubbed = scrub_jd_text(jd_text)
    wrapped = wrap_jd_content(scrubbed)
    return f"""You are grading a job description to determine if it's worth customizing a resume for.

{JD_GRADING_REASONING_PROTOCOL}

## Source documents (pre-loaded — do NOT read any files)

### Base resume (profile/base-resume/base-resume.md)

```markdown
{base_resume_content}
```

### Full LinkedIn experience (profile/full-experience/full-experience.md)

```markdown
{linkedin_content}
```

Location note: The candidate is based in McLean, VA. Do not flag location as a GAP if the job is in the Washington, DC metro area (including College Park, MD, Arlington, VA, Tysons Corner, VA, Washington, DC, etc.) — these are commutable. Do not flag travel as a GAP if the amount required is below 25%. Do not flag relocation as a GAP; the candidate is willing to relocate for the right role.

## Job

Job: {slug}
{wrapped}

## Your task

Extract requirements from the JD's qualifications sections only. Assess each against the candidate's full background (base resume + LinkedIn above). A requirement is a DIRECT_HIT if the experience appears in either source.

Output ONLY valid JSON to stdout (format: {{"per_criterion": [{{"requirement": "...", "tier": "core", "assessment": "DIRECT_HIT", "comment": "..."}}]}}). No reasoning text, no explanations, no markdown fences, no preamble — start with `{{` and end with `}}`. Nothing else. Do NOT write any files. Do NOT compute a score — that is Call 2's job.

If the JD requires security clearance, output CLEARANCE instead of the reasoning JSON."""


def build_jd_scoring_prompt(slug: str, reasoning_json: str) -> str:
    """Build the Call 2 (scoring) prompt for the JD grader.

    Computes the score from the fixed reasoning JSON produced by Call 1.
    """
    return f"""{JD_GRADING_SCORING_PROTOCOL}

You are grading the JD for {slug}. The reasoning from Call 1 is provided below as fixed, committed input. You CANNOT change these verdicts — your job is to compute the score from them.

Reasoning JSON from Call 1:
{reasoning_json}

Compute the score using the protocol formula (ceiling, core score, preferred bonus, quantitative base, subjective adjustment, final score). Pass through the per_criterion list unchanged.

This is arithmetic — count the verdicts, apply the formula, output the number. You have all the input above. Do NOT run commands, write scripts, search files, or use any tools. Do NOT read files — everything you need is in this prompt. Such actions will be blocked by a hook and waste time. If you spend more than a few seconds on this, you are overthinking. Just compute and output.

Output ONLY valid JSON to stdout. No reasoning text, no explanations, no markdown fences, no preamble — start with `{{` and end with `}}`. Nothing else. Do NOT write any files. The orchestrator handles all file operations from your stdout output."""


# ─── Node ────────────────────────────────────────────────────────────────────


def step4_grade_jd_node(state: JobState, config: RunnableConfig) -> dict:
    """Grade the JD using the LLM. Detects clearance (ADR-0007).

    Uses a two-call decomposition (same rubric as resume grading):
    - Call 1: Extract requirements + assess against full background
    - Call 2: Compute score from fixed reasoning

    Returns partial state with jd_grade set. If clearance is detected,
    jd_grade.is_clearance is True and the conditional edge routes to trash.
    If the LLM call fails or the grade can't be parsed, raises an exception
    (caught by the orchestrator's per-job error boundary).
    """
    deps = get_deps(config)
    slug = state.slug

    # Read JD text from state or from file
    jd_text = state.jd_text
    if not jd_text and state.jd_path and state.jd_path.exists():
        jd_text = state.jd_path.read_text(encoding="utf-8")

    # Strip URL metadata comment for grading
    jd_text_clean = strip_url_metadata(jd_text)

    # Pre-read source files for prompt inlining
    base_resume_content = deps.paths.base_resume.read_text(encoding="utf-8")
    linkedin_content = deps.paths.linkedin_experience.read_text(encoding="utf-8")

    # ─── Call 1: Reasoning ──────────────────────────────────────────────
    reasoning_prompt = build_jd_reasoning_prompt(
        slug, jd_text_clean, base_resume_content, linkedin_content
    )
    reasoning_output, reasoning_error = deps.llm(
        reasoning_prompt,
        model=deps.config.models.customizer,
        timeout=deps.config.llm_timeout_seconds,
        retries=deps.config.llm_retries,
        retry_delay=deps.config.llm_retry_delay,
        workspace=str(deps.paths.hunter_dir),
    )

    if reasoning_error:
        raise RuntimeError(f"{slug}: JD grading reasoning call failed: {reasoning_error}")

    # Check for clearance detection
    if "CLEARANCE" in (reasoning_output or "").upper():
        deps.logger.info(f"  {slug}: clearance detected by LLM → trash")
        jd_grade = JdGrade(grade=0, justification="CLEARANCE", is_clearance=True)
        return {"jd_grade": jd_grade}

    # Parse reasoning JSON from stdout
    reasoning_data = parse_llm_json(reasoning_output)
    if reasoning_data is None:
        raise RuntimeError(
            f"{slug}: could not parse reasoning JSON from Call 1 stdout: {reasoning_output[:200]}"
        )

    # ─── Call 2: Scoring ────────────────────────────────────────────────
    reasoning_json_str = json.dumps(reasoning_data)
    scoring_prompt = build_jd_scoring_prompt(slug, reasoning_json_str)
    scoring_output, scoring_error = deps.llm(
        scoring_prompt,
        model=deps.config.models.customizer,
        timeout=deps.config.llm_timeout_seconds,
        retries=deps.config.llm_retries,
        retry_delay=deps.config.llm_retry_delay,
        workspace=str(deps.paths.hunter_dir),
    )

    if scoring_error:
        raise RuntimeError(f"{slug}: JD grading scoring call failed: {scoring_error}")

    # Parse grade JSON from stdout
    grade_data = parse_llm_json(scoring_output)
    if grade_data is None:
        raise RuntimeError(
            f"{slug}: could not parse grade JSON from scoring call stdout: {scoring_output[:200]}"
        )

    grade = float(grade_data.get("grade", -1))
    if not (0 <= grade <= 10):
        raise RuntimeError(f"{slug}: grade {grade} out of range [0, 10]")

    justification = str(grade_data.get("justification", ""))

    deps.logger.info(f"  {slug}: JD grade = {grade}")
    jd_grade = JdGrade(grade=grade, justification=justification, is_clearance=False)

    # Rename the JD file with the grade
    if not deps.config.dry_run and state.jd_path and state.jd_path.exists():
        rename_with_grade(state.jd_path, grade)

    return {"jd_grade": jd_grade}
