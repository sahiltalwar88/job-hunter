"""Step 8 (per-job): Optimize decision — should we continue iterating?

This is a LangGraph node. It checks the exit conditions and calls the
LLM "can you improve?" check (ADR-0009). Sets optimize_can_improve on state.

Exit conditions (any -> optimize_can_improve = False):
1. Grade >= threshold AND no core gaps
2. Iteration count >= max_optimization_iterations
3. LLM says NO to "can you truthfully improve this resume further?"

If none met, optimize_can_improve = True (continue to step6).

The routing function (in graph.py) reads optimize_can_improve:
    True  -> "continue" (back to step6_customize)
    False -> "done" (forward to step9_veracity)
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from pipeline.infrastructure.file_ops import strip_url_metadata
from pipeline.infrastructure.llm_interface import get_deps
from pipeline.infrastructure.paths import Paths
from pipeline.infrastructure.protocol_constants import RESUME_CUSTOMIZATION_PROTOCOL
from pipeline.infrastructure.state import Assessment, JobState


def build_can_improve_prompt(
    slug: str, jd_text: str, resume_path: str, per_criterion: list,
    resume_content: str = "", linkedin_content: str = "",
) -> str:
    """Build the 'can you improve?' prompt for the customizer model.

    The prompt enforces truthfulness: the LLM may only say YES if it can
    identify a specific, truthful improvement sourced from the LinkedIn
    experience (ADR-0009).

    Source documents (current resume, LinkedIn) are pre-fed into the prompt
    to eliminate file-read tool calls.
    """
    feedback_lines = []
    for c in per_criterion:
        if c.assessment in (Assessment.GAP, Assessment.PARTIAL):
            feedback_lines.append(
                f"- [{c.tier}] {c.assessment.value}: {c.requirement} -- {c.comment}"
            )
    feedback = (
        "\n".join(feedback_lines) if feedback_lines else "No specific gaps identified."
    )

    return f"""You are evaluating whether a customized resume can be truthfully improved further.

{RESUME_CUSTOMIZATION_PROTOCOL}

## Source documents (pre-loaded — do NOT read any files)

### Current customized resume ({resume_path})

```markdown
{resume_content}
```

### Full LinkedIn experience (profile/full-experience/full-experience.md)

```markdown
{linkedin_content}
```

## Job

Job: {slug}
Job Description:
---
{jd_text}
---

The grader found these areas:
{feedback}

Can you truthfully improve this resume further by sourcing ONLY from the LinkedIn experience? Do NOT suggest fabricated improvements -- only say YES if you can identify a specific, truthful change that would raise the grade.

Reply with exactly one word: YES or NO."""


def _find_jd_in_drafts(slug: str, paths: Paths) -> str:
    """Find and return JD text from drafts/<slug>/."""
    job_dir = paths.drafts / slug
    if not job_dir.exists():
        return ""
    jd_files = list(job_dir.glob("*job-description.md"))
    if not jd_files:
        return ""
    text = jd_files[0].read_text(encoding="utf-8")
    return strip_url_metadata(text)


def step8_optimize_node(state: JobState, config: RunnableConfig) -> dict:
    """Decide whether to continue the optimize loop or exit.

    Sets optimize_can_improve on state. The routing function reads it:
        True  -> continue (back to step6)
        False -> done (forward to step9)

    Exit conditions (any -> False):
    1. Grade >= threshold AND no core gaps
    2. Iteration count >= max_optimization_iterations
    3. LLM says NO to "can you improve?"

    In dry_run, always returns False (no LLM call).
    """
    deps = get_deps(config)
    slug = state.slug

    if deps.config.dry_run:
        deps.logger.info(f"  {slug}: dry-run optimize -> done")
        return {"optimize_can_improve": False}

    # Condition 1: grade >= threshold AND no core gaps
    if state.latest_grade:
        threshold = deps.config.resume_grade_threshold
        if state.latest_grade.grade >= threshold and not state.has_core_gaps:
            deps.logger.info(
                f"  {slug}: grade {state.latest_grade.grade} >= {threshold}, "
                f"no core gaps -> done"
            )
            return {"optimize_can_improve": False}

    # Condition 2: iteration count >= max
    max_iter = deps.config.max_optimization_iterations
    if state.optimize_iteration_count >= max_iter:
        deps.logger.info(
            f"  {slug}: {state.optimize_iteration_count} iterations >= {max_iter} -> done"
        )
        return {"optimize_can_improve": False}

    # Condition 3: LLM "can you improve?" check
    latest = state.latest_resume
    if not latest or not state.latest_grade:
        deps.logger.info(f"  {slug}: no resume or grade -> done")
        return {"optimize_can_improve": False}

    # Get JD text
    jd_text = state.jd_text
    if not jd_text:
        jd_text = _find_jd_in_drafts(slug, deps.paths)
    else:
        jd_text = strip_url_metadata(jd_text)

    # Get resume path (relative to workspace)
    resume_path = latest.path
    try:
        resume_rel = str(resume_path.relative_to(deps.paths.hunter_dir))
    except ValueError:
        resume_rel = str(resume_path)

    # Pre-read source files for prompt inlining
    resume_content = resume_path.read_text(encoding="utf-8") if resume_path.exists() else ""
    linkedin_content = deps.paths.linkedin_experience.read_text(encoding="utf-8")

    prompt = build_can_improve_prompt(
        slug, jd_text, resume_rel, state.latest_grade.per_criterion,
        resume_content, linkedin_content,
    )

    output, error = deps.llm(
        prompt,
        model=deps.config.models.customizer,
        timeout=deps.config.llm_timeout_seconds,
        retries=deps.config.llm_retries,
        retry_delay=deps.config.llm_retry_delay,
        workspace=str(deps.paths.hunter_dir),
    )

    if error:
        deps.logger.warning(
            f"  {slug}: 'can improve?' LLM call failed: {error} -> defaulting to NO"
        )
        return {"optimize_can_improve": False}

    # Parse YES/NO response
    can_improve = "YES" in (output or "").upper()
    deps.logger.info(
        f"  {slug}: 'can improve?' -> {'YES' if can_improve else 'NO'}"
    )
    return {"optimize_can_improve": can_improve}
