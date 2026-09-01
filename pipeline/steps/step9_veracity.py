"""Step 9 (per-job): Truthfulness review — verify resume claims against LinkedIn.

This is a LangGraph node. It uses a two-call truthfulness decomposition
(ADR-0011):
  - Call 1 (classification): loads sources + classifies each claim
  - Call 2 (synthesis): takes Call 1's verdicts as fixed input, produces
    the final verification JSON

The orchestrator captures stdout from both calls, persists the classification
JSON, writes the verification JSON, parses it into a Verification model, and
cleans up the veracity workspace.

Both calls use `normal` permission mode (ADR-0010) — the truthfulness reviewer
cannot write files or exec commands; it returns JSON via stdout only.

Only runs if grade >= threshold (resume_grade_threshold, default 9). If
grade < threshold, the routing function (route_after_truthfulness in
graph.py) sends to rejected_resume regardless — but this node still sets
verification.verified = False as a safety net.

The verification is stored in state.verification. The routing function
reads it: verified -> step10_ready, unverified -> rejected_resume.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from pipeline.infrastructure.file_ops import parse_grade_from_filename
from pipeline.infrastructure.llm_interface import get_deps, parse_llm_json
from pipeline.infrastructure.paths import Paths
from pipeline.infrastructure.protocol_constants import (
    VERACITY_CLASSIFICATION_PROTOCOL,
    VERACITY_SYNTHESIS_PROTOCOL,
)
from pipeline.infrastructure.state import JobState, Verification


# ─── Two-call prompt builders (ADR-0011) ─────────────────────────────────────


def build_classification_prompt(
    slug: str,
    resume_path: str,
    resume_content: str,
    base_resume_content: str,
    linkedin_content: str,
) -> str:
    """Build the Call 1 (classification) prompt for the truthfulness model.

    All three source documents are pre-fed into the prompt — the LLM does
    not need to read any files. This eliminates tool-call round-trips and
    prevents the LLM from writing scripts to parse the files (which caused
    it to never produce output in practice).
    """
    return f"""You are a truthfulness verification subagent.

{VERACITY_CLASSIFICATION_PROTOCOL}

## Source documents (pre-loaded — do NOT read any files)

### Customized resume ({resume_path})

```markdown
{resume_content}
```

### Base resume (profile/base-resume/base-resume.md)

```markdown
{base_resume_content}
```

### Full LinkedIn experience (profile/full-experience/full-experience.md)

```markdown
{linkedin_content}
```

## Your task

All three sources are above. Do NOT run commands, write scripts, or use any tools other than reading files — they will be blocked by a hook. Classify each claim on the resume into one of 4 buckets (TRACEABLE / MINOR_VARIATION / MATERIAL_OVERSTATEMENT / FABRICATED).

This is a mechanical classification task, not an investigation. You have all three documents above — read them, classify each claim, output JSON. Do not write code, run commands, or use any tools. If you find yourself wanting to write a script, stop — you are overcomplicating this.

Output ONLY valid JSON to stdout (format: {{"per_claim": [{{"claim": "...", "location": "...", "bucket": "...", "source_checked": "...", "reason": "..."}}]}}). No reasoning text, no explanations, no markdown fences, no preamble — start with `{{` and end with `}}`. Nothing else. Do NOT write any files. Do NOT synthesize the final verdict — that is Call 2's job."""


def build_synthesis_prompt(slug: str, classification_json: str) -> str:
    """Build the Call 2 (synthesis) prompt for the truthfulness model.

    This prompt covers Steps 3-4 of the veracity protocol: synthesize the
    final verification result from the fixed classification JSON produced
    by Call 1, and output the verification JSON via stdout. The protocol
    is inlined directly.
    """
    return f"""{VERACITY_SYNTHESIS_PROTOCOL}

You are verifying resume for {slug}. The per-claim classifications from Call 1 are provided below as fixed, committed input. You CANNOT change these classifications — your job is to synthesize the final verification from them.

Classification JSON from Call 1:
{classification_json}

Count the MATERIAL_OVERSTATEMENT and FABRICATED verdicts (these are unverifiable). If zero → verified: true. If one or more → verified: false. Pass through the unverifiable claims unchanged.

This is a counting task — count the MATERIAL_OVERSTATEMENT and FABRICATED verdicts, collect them, output JSON. You have all the input above. Do NOT run commands, write scripts, search files, or use any tools. Do NOT read files — everything you need is in this prompt. Such actions will be blocked by a hook and waste time. If you spend more than a few seconds on this, you are overthinking. Just count and output.

Output ONLY valid JSON to stdout. No reasoning text, no explanations, no markdown fences, no preamble — start with `{{` and end with `}}`. Nothing else. Do NOT write any files. The orchestrator handles all file operations from your stdout output."""


def parse_verification_json(veracity_file: Path) -> dict | None:
    """Read and parse a verification JSON file from .veracity/."""
    if not veracity_file.exists():
        return None
    try:
        with open(veracity_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _find_best_resume_in_drafts(slug: str, paths: Paths) -> Path | None:
    """Find the resume with the highest grade in drafts/<slug>/."""
    job_dir = paths.drafts / slug
    if not job_dir.exists():
        return None
    resume_files = list(job_dir.glob("*resume-v*.md"))
    if not resume_files:
        return None
    # Pick the resume with the highest grade in the filename
    return max(resume_files, key=lambda p: parse_grade_from_filename(p.name) or -1)


def step9_veracity_node(state: JobState, config: RunnableConfig) -> dict:
    """Run truthfulness review on the latest resume.

    Returns partial state with verification set. If grade < threshold,
    returns verification.verified = False without calling the LLM (the
    routing function sends to rejected_resume anyway).

    Raises on LLM failure or unparseable verification JSON (when grade
    >= threshold). In dry_run, skips the LLM call.
    """
    deps = get_deps(config)
    slug = state.slug

    # If grade < threshold, skip truthfulness — route to rejected_resume
    threshold = deps.config.resume_grade_threshold
    if not state.latest_grade or state.latest_grade.grade < threshold:
        deps.logger.info(
            f"  {slug}: grade below {threshold}, skipping truthfulness"
        )
        return {"verification": Verification(verified=False)}

    if deps.config.dry_run:
        deps.logger.info(f"  {slug}: dry-run truthfulness (skipped)")
        return {"verification": Verification(verified=False)}

    # Find the best resume (highest grade in filename)
    resume_path = _find_best_resume_in_drafts(slug, deps.paths)
    if not resume_path:
        raise RuntimeError(f"{slug}: no resume found for truthfulness review")

    # Create veracity workspace
    veracity_dir = deps.paths.veracity / slug
    veracity_dir.mkdir(parents=True, exist_ok=True)

    # Build relative path for prompts
    resume_rel = str(resume_path.relative_to(deps.paths.hunter_dir))

    # Pre-read source files to inline into the classification prompt
    resume_content = resume_path.read_text(encoding="utf-8")
    base_resume_content = deps.paths.base_resume.read_text(encoding="utf-8")
    linkedin_content = deps.paths.linkedin_experience.read_text(encoding="utf-8")

    # ─── Call 1: Classification (ADR-0011) ─────────────────────────────
    classification_prompt = build_classification_prompt(
        slug, resume_rel, resume_content, base_resume_content, linkedin_content
    )
    classification_output, classification_error = deps.llm(
        classification_prompt,
        model=deps.config.models.truthfulness,
        timeout=deps.config.llm_timeout_seconds,
        retries=deps.config.llm_retries,
        retry_delay=deps.config.llm_retry_delay,
        workspace=str(deps.paths.hunter_dir),
        permission_mode="normal",
    )

    if classification_error:
        raise RuntimeError(
            f"{slug}: truthfulness classification call failed: {classification_error}"
        )

    # Parse classification JSON from stdout
    classification_data = parse_llm_json(classification_output)
    if classification_data is None:
        raise RuntimeError(
            f"{slug}: could not parse classification JSON from Call 1 stdout"
        )

    # Persist classification JSON for auditability (ADR-0011)
    classification_file = deps.paths.veracity / slug / "classification.json"
    classification_file.parent.mkdir(parents=True, exist_ok=True)
    with open(classification_file, "w") as f:
        json.dump(classification_data, f, indent=2)

    # ─── Call 2: Synthesis (ADR-0011) ──────────────────────────────────
    classification_json_str = json.dumps(classification_data)
    synthesis_prompt = build_synthesis_prompt(slug, classification_json_str)
    synthesis_output, synthesis_error = deps.llm(
        synthesis_prompt,
        model=deps.config.models.truthfulness,
        timeout=deps.config.llm_timeout_seconds,
        retries=deps.config.llm_retries,
        retry_delay=deps.config.llm_retry_delay,
        workspace=str(deps.paths.hunter_dir),
        permission_mode="normal",
    )

    if synthesis_error:
        raise RuntimeError(
            f"{slug}: truthfulness synthesis call failed: {synthesis_error}"
        )

    # Parse verification JSON from stdout (primary path — ADR-0010)
    verification_data = parse_llm_json(synthesis_output)

    # Fallback: read from file (transitional compat)
    if verification_data is None:
        veracity_file = deps.paths.veracity / slug / "verification.json"
        verification_data = parse_verification_json(veracity_file)

    if verification_data is None:
        raise RuntimeError(
            f"{slug}: could not parse verification JSON from synthesis call stdout"
            f" or read from .veracity/{slug}/verification.json"
        )

    # Orchestrator writes the verification JSON file (ADR-0010)
    veracity_file = deps.paths.veracity / slug / "verification.json"
    veracity_file.parent.mkdir(parents=True, exist_ok=True)
    with open(veracity_file, "w") as f:
        json.dump(verification_data, f, indent=2)

    # Parse into Verification model (validates fields)
    try:
        verification = Verification.model_validate(verification_data)
    except Exception as e:
        raise RuntimeError(f"{slug}: could not parse verification JSON: {e}") from e

    verified = verification.verified
    deps.logger.info(
        f"  {slug}: truthfulness {'VERIFIED' if verified else 'UNVERIFIED'}"
        + (f" ({len(verification.unverifiable_claims)} claims)" if not verified else "")
    )

    # Clean up veracity workspace
    shutil.rmtree(veracity_dir, ignore_errors=True)

    return {"verification": verification}
