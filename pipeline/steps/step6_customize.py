"""Step 6 (per-job): Customize resume — first customization or optimize iteration.

This is a LangGraph node. It handles both the first customization (version 1)
and re-entry from the optimize cycle (version 2+).

- First iteration (version 1): pre-copy base resume to
  drafts/<slug>/[TBD] resume-v1.md, build customize prompt, call LLM.
- Re-entry (version > 1): build optimize prompt with grader feedback,
  call LLM to write drafts/<slug>/[TBD] resume-v{N}.md.

The LLM (customizer model) edits/writes the file in place. The node appends
a ResumeVersion to state (LangGraph concatenates via Annotated[list, add]).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from pipeline.infrastructure.file_ops import strip_url_metadata
from pipeline.infrastructure.injection_filter import scrub_jd_text, wrap_jd_content
from pipeline.infrastructure.llm_interface import get_deps
from pipeline.infrastructure.paths import Paths
from pipeline.infrastructure.protocol_constants import RESUME_CUSTOMIZATION_PROTOCOL
from pipeline.infrastructure.state import Assessment, JobState, ResumeVersion


def build_customize_prompt(
    slug: str,
    jd_text: str,
    version: int,
    jd_grade: float = 0,
    jd_justification: str = "",
    base_resume_content: str = "",
    linkedin_content: str = "",
    few_shot_content: str = "",
) -> str:
    """Build the resume customization prompt for the customizer model.

    The base resume is pre-copied to drafts/<slug>/[TBD] resume-v{version}.md
    before this prompt is sent. The agent edits that file in place.

    Source documents (base resume, LinkedIn, few-shot examples) are pre-fed
    into the prompt to eliminate file-read tool calls and prevent the LLM
    from missing details in long files.

    JD text is scrubbed of injection patterns and wrapped in structural
    isolation tags before being interpolated into the prompt (E4).
    """
    scrubbed = scrub_jd_text(jd_text)
    wrapped = wrap_jd_content(scrubbed)
    jd_grade_section = ""
    if jd_grade:
        jd_grade_section = (
            f"\nJD grade: {jd_grade}/10 — how well the base resume matches this JD."
        )
        if jd_justification:
            jd_grade_section += f" {jd_justification}"

    return f"""You are customizing a resume for a specific job.

The base resume has been pre-copied to stages/2_drafts/{slug}/[TBD] resume-v{version}.md. Edit it in place — do NOT create a new file.

{RESUME_CUSTOMIZATION_PROTOCOL}

## Source documents (pre-loaded — do NOT read any files)

### Base resume (_config/profile/base-resume/base-resume.md)

```markdown
{base_resume_content}
```

### Full LinkedIn experience (_config/profile/full-experience/full-experience.md)

```markdown
{linkedin_content}
```

### Few-shot examples (docs/examples/customized-resumes/)

{few_shot_content}

## Job

Job: {slug}
{wrapped}
{jd_grade_section}

## Your task

Customize the resume for this job. All source documents are above — do NOT read any files. The base resume has been pre-copied to stages/2_drafts/{slug}/[TBD] resume-v{version}.md. Edit it in place.

Follow the customization protocol strictly — it contains all formatting, ordering, truthfulness, and strategy rules. Pull in experience from the LinkedIn (truthfully — never fabricate). Study the few-shot examples above to learn the desired transformation patterns.

After writing the resume, verify it fits the line budget per the customization protocol (self-measure with `python3 -m pipeline.helpers.count_lines stages/2_drafts/{slug}/[TBD] resume-v{version}.md --json`, self-trim if over 75).

Edit the resume at stages/2_drafts/{slug}/[TBD] resume-v{version}.md in place.

Do NOT output the resume to stdout. Write it to the file."""


def build_optimize_prompt(
    slug: str,
    jd_text: str,
    current_resume_path: str,
    next_version: int,
    per_criterion: list,
    base_resume_content: str = "",
    linkedin_content: str = "",
    current_resume_content: str = "",
) -> str:
    """Build the optimization prompt for the customizer model.

    per_criterion is a list of Criterion Pydantic models from the latest grade.

    Source documents (base resume, LinkedIn, current resume) are pre-fed
    into the prompt to eliminate file-read tool calls.

    JD text is scrubbed of injection patterns and wrapped in structural
    isolation tags before being interpolated into the prompt (E4).
    """
    scrubbed = scrub_jd_text(jd_text)
    wrapped = wrap_jd_content(scrubbed)
    feedback_lines = []
    for c in per_criterion:
        if c.assessment in (Assessment.GAP, Assessment.PARTIAL):
            feedback_lines.append(
                f"- [{c.tier}] {c.assessment.value}: {c.requirement} — {c.comment}"
            )

    feedback = (
        "\n".join(feedback_lines) if feedback_lines else "No specific gaps identified."
    )

    return f"""You are improving a customized resume for a specific job.

{RESUME_CUSTOMIZATION_PROTOCOL}

## Source documents (pre-loaded — do NOT read any files)

### Base resume (_config/profile/base-resume/base-resume.md)

```markdown
{base_resume_content}
```

### Full LinkedIn experience (_config/profile/full-experience/full-experience.md)

```markdown
{linkedin_content}
```

### Current customized resume ({current_resume_path})

```markdown
{current_resume_content}
```

## Job

Job: {slug}
{wrapped}

The grader found these areas to improve:
{feedback}

## Your task

Improve the resume to address these gaps while staying truthful. All source documents are above — do NOT read any files. Follow the customization protocol strictly — it contains all formatting, ordering, truthfulness, and strategy rules.

After writing the resume, verify it fits the line budget per the customization protocol (self-measure with `python3 -m pipeline.helpers.count_lines <path> --json`, self-trim if over 75).

Write the improved resume to stages/2_drafts/{slug}/[TBD] resume-v{next_version}.md

Do NOT output the resume to stdout. Write it to the file."""


def _find_jd_in_drafts(slug: str, paths: Paths) -> tuple[str, Path | None]:
    """Find the JD file in drafts/<slug>/ and return (clean_text, path)."""
    job_dir = paths.drafts / slug
    if not job_dir.exists():
        return "", None
    jd_files = list(job_dir.glob("*job-description.md"))
    if not jd_files:
        return "", None
    jd_path = jd_files[0]
    text = jd_path.read_text(encoding="utf-8")
    text = strip_url_metadata(text)
    return text, jd_path


# Default few-shot examples — overridden by config.few_shot_examples if set.
_DEFAULT_FEW_SHOT_FILES: list[str] = []


def _load_few_shot_examples(paths: Paths, config) -> str:
    """Read few-shot examples and format them for prompt inlining.

    Uses config.few_shot_examples if set, otherwise falls back to
    _DEFAULT_FEW_SHOT_FILES (empty by default — users configure their own).
    """
    examples_dir = paths.hunter_dir / "docs" / "examples" / "customized-resumes"
    if not examples_dir.exists():
        return "(examples directory not found)"
    filenames = config.few_shot_examples if config.few_shot_examples else _DEFAULT_FEW_SHOT_FILES
    sections = []
    for fname in filenames:
        fpath = examples_dir / fname
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8")
            name = fname.replace(".docx.md", "").replace(".md", "")
            sections.append(f"#### {name}\n\n```markdown\n{content}\n```")
    if not sections:
        return "(no few-shot examples found)"
    return "\n\n".join(sections)


def step6_customize_node(state: JobState, config: RunnableConfig) -> dict:
    """Customize the resume (first iteration) or optimize it (re-entry).

    First iteration (no resume versions yet):
        - Pre-copy base resume to drafts/<slug>/[TBD] resume-v1.md
        - Build customize prompt, call LLM (customizer model)

    Re-entry (optimize cycle, version > 1):
        - Build optimize prompt with grader feedback
        - Call LLM to write drafts/<slug>/[TBD] resume-v{N}.md

    Returns partial state with a new ResumeVersion appended (LangGraph
    concatenates via the Annotated[list, add] reducer).

    Raises on missing base resume, missing JD text, or LLM failure.
    """
    deps = get_deps(config)
    slug = state.slug
    version = state.next_resume_version

    # Get JD text from state or from drafts folder
    jd_text = state.jd_text
    if not jd_text:
        jd_text, _ = _find_jd_in_drafts(slug, deps.paths)
    else:
        jd_text = strip_url_metadata(jd_text)

    if not jd_text:
        raise RuntimeError(f"{slug}: no JD text available for customization")

    job_dir = deps.paths.drafts / slug
    resume_path = job_dir / f"[TBD] resume-v{version}.md"

    if deps.config.dry_run:
        # Dry run: skip pre-copy and LLM call, just return the version
        deps.logger.info(f"  {slug}: dry-run customize v{version} -> {resume_path.name}")
        return {"resume_versions": [ResumeVersion(version=version, path=resume_path)]}

    if version == 1:
        # First iteration: pre-copy base resume
        if not deps.paths.base_resume.exists():
            raise RuntimeError(
                f"{slug}: base resume not found at {deps.paths.base_resume}"
            )
        job_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(deps.paths.base_resume, resume_path)
        deps.logger.debug(f"  {slug}: pre-copied base resume -> {resume_path.name}")

        # Pre-read source files for prompt inlining
        base_resume_content = deps.paths.base_resume.read_text(encoding="utf-8")
        linkedin_content = deps.paths.linkedin_experience.read_text(encoding="utf-8")
        few_shot_content = _load_few_shot_examples(deps.paths, deps.config)

        # Build customize prompt
        jd_grade = state.jd_grade.grade if state.jd_grade else 0
        jd_justification = state.jd_grade.justification if state.jd_grade else ""
        prompt = build_customize_prompt(
            slug, jd_text, version, jd_grade, jd_justification,
            base_resume_content, linkedin_content, few_shot_content,
        )
    else:
        # Re-entry: optimize with grader feedback
        latest = state.latest_resume
        if not latest:
            raise RuntimeError(f"{slug}: no latest resume to optimize from")

        # Find the actual current resume file (might be graded with [score] prefix)
        current_resumes = sorted(
            job_dir.glob(f"*resume-v{version - 1}.md"),
            key=lambda p: p.name,
        )
        if not current_resumes:
            raise RuntimeError(
                f"{slug}: could not find resume-v{version - 1} to optimize from"
            )
        current_resume = current_resumes[0]
        current_resume_rel = str(current_resume.relative_to(deps.paths.hunter_dir))

        # Pre-read source files for prompt inlining
        base_resume_content = deps.paths.base_resume.read_text(encoding="utf-8")
        linkedin_content = deps.paths.linkedin_experience.read_text(encoding="utf-8")
        current_resume_content = current_resume.read_text(encoding="utf-8")

        # Build optimize prompt with grader feedback
        per_criterion = state.latest_grade.per_criterion if state.latest_grade else []
        prompt = build_optimize_prompt(
            slug, jd_text, current_resume_rel, version, per_criterion,
            base_resume_content, linkedin_content, current_resume_content,
        )

    # Call LLM (customizer model edits/writes the file in place)
    # The customizer uses scoped permissions (ADR-0010, E4) — a config file
    # restricts writes to drafts/** and exec to count_lines.py only.
    customizer_config = None
    if deps.paths.agent_permissions.exists():
        customizer_config = str(deps.paths.agent_permissions)

    output, error = deps.llm(
        prompt,
        model=deps.config.models.customizer,
        timeout=deps.config.llm_timeout_seconds,
        retries=deps.config.llm_retries,
        retry_delay=deps.config.llm_retry_delay,
        workspace=str(deps.paths.hunter_dir),
        config_path=customizer_config,
    )

    if error:
        raise RuntimeError(
            f"{slug}: customization v{version} LLM call failed: {error}"
        )

    # Verify the resume file exists
    if not resume_path.exists():
        # Check if it was created with a different name
        resume_files = list(job_dir.glob(f"*resume-v{version}.md"))
        if resume_files:
            resume_path = resume_files[0]
        else:
            raise RuntimeError(
                f"{slug}: customization v{version} did not produce a resume file"
            )

    deps.logger.info(f"  {slug}: resume v{version} customized -> {resume_path.name}")
    return {"resume_versions": [ResumeVersion(version=version, path=resume_path)]}
