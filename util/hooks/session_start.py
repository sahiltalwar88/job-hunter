#!/usr/bin/env python3
"""SessionStart hook — injects debug workflow context + writes session state.

When a Devin session starts in this workspace, this hook:
1. Writes a per-session state file (.devin/session-state/<session_id>.json)
   containing the initial snapshot of stages/2_drafts/ — used by block_failing_grade.py
   to only check slugs this session touched.
2. Injects debug workflow context (subagent profiles, integrity rules,
   file conventions) into the session.

In pipeline mode (DEVIN_PIPELINE_MODE=1), exits immediately without injecting
context — devin -p calls don't need debug workflow instructions.
"""
import json
import os
import re
import sys


CONTEXT = """\
## Job Hunter — Debug Workflow Context

This workspace has an automated pipeline (python3 -m pipeline, hourly via systemd) AND a debug workflow. You are in a debug session.

### When to use debug mode
- The user asks to work on a specific job, customize a resume, or iterate on grading.
- The user says "help me with this job" or similar.

### When to use automated mode
- The user asks to run the pipeline, process new jobs, or check the scraper.
- In that case, use: `python3 -m pipeline --dry-run` (or without --dry-run).

### Debug workflow steps
1. Find the job: look in stages/1_listings/<company-role>/ for a [score] job-description.md (already graded ≥8).
2. Customize: pre-copy _config/profile/base-resume/base-resume.md to stages/2_drafts/<company-role>/[TBD] resume-v1.md, then spawn the `customizer` subagent (customizer-model). It reads the pre-copied base, _config/resume-customization-protocol.md, _config/profile/full-experience/full-experience.md (reference pool), and few-shot examples. It edits the resume in place, self-measures with count_lines.py, and self-trims if over 75 lines. Pass the JD grade (from the [score] job-description.md filename) to the customizer.
3. Grade: spawn the `resume-grader` subagent (grader-model). It grades and renames the file with the score prefix. Do NOT rename scored files yourself — the hook blocks this.
4. Iterate: if grade < 9, read the grader's feedback, improve the resume (new version), re-grade. Up to 3 iterations. If you cannot reach 9 after 3 iterations, move the folder to stages/6_rejected/[RESUME] <company-role>/.
5. Verify truthfulness: spawn the `truthfulness-reviewer` subagent (grader-model). It checks every claim against the base resume and LinkedIn experience. This is MANDATORY before moving to stages/4_ready/ — a resume must be both grade ≥ 9 AND truthful. If truthfulness fails, do NOT move to stages/4_ready/ — fix the untruthful claims and re-grade, or move to stages/6_rejected/[RESUME] <company-role>/ if the claims cannot be made truthful.
6. Move to stages/4_ready/: ONLY if grade ≥ 9 AND truthfulness verified. Move the entire folder (JD + resume) to stages/4_ready/<company-role>/.

### Integrity rules (enforced by hooks)
- **Never rename a [score] file.** The PreToolUse hook blocks changing an existing score prefix. The grader subagent owns the rename from [TBD] → [score].
- **Never write a [score] file directly.** The PreToolUse hook blocks all writes to scored resume files. Scores are only assigned by the grader via rename from [TBD].
- **Exec is whitelisted (pipeline mode only).** The PreToolUse hook (restrict_exec.py) enforces a whitelist only in automated pipeline mode. In debug mode (your current session), all commands are allowed — you approve them yourself.
- **Never stop with a failing grade in stages/2_drafts/.** The Stop hook blocks finishing if any resume you created in stages/2_drafts/ has a score < 9 or is ungraded. Move it to stages/6_rejected/[RESUME] first, or iterate.
- **Base resume is the only customization base.** _config/profile/full-experience/ is reference pool material, never a base.
- **Truthfulness is non-negotiable.** Every claim must appear in the LinkedIn experience.

### Subagent profiles available
- `customizer` (customizer-model): Customizes resumes for specific JDs. Draws from the reference pool (base resume, LinkedIn, few-shot examples). Self-measures with count_lines.py, self-trims if over 75 lines. The optimizer is the same agent re-invoked with grader feedback.
- `resume-grader` (grader-model): Grades resumes against JDs with realistic recruiter framing. Writes JSON to .grading/, renames the resume file with the score.
- `truthfulness-reviewer` (grader-model): Verifies every resume claim against the LinkedIn superset. Writes JSON to .veracity/.

### Key files
- _config/grading-protocol.md — the grading rubric (read by the grader subagent)
- _config/veracity-protocol.md — the truthfulness protocol (read by the truthfulness reviewer)
- _config/resume-customization-protocol.md — the customization protocol (read by the customizer subagent)
- pipeline/helpers/count_lines.py — rendered line measurement (run by the customizer to self-measure)
- .grades.log — append-only audit trail of every grading event
"""


def _snapshot_drafts(project_dir: str) -> dict:
    """Snapshot the current state of stages/2_drafts/ — slug → {filename: status}.

    Status is "ungraded" for [TBD] files, "scored" for [N] files.
    Includes ALL slug directories (even those with only JDs and no resumes)
    so the Stop hook can distinguish "existed at session start" from "new".
    """
    drafts_dir = os.path.join(project_dir, "stages", "2_drafts")
    if not os.path.isdir(drafts_dir):
        return {}

    snapshot = {}
    for slug in os.listdir(drafts_dir):
        slug_dir = os.path.join(drafts_dir, slug)
        if not os.path.isdir(slug_dir):
            continue
        files = {}
        for f in os.listdir(slug_dir):
            if not re.search(r'resume-v\d+\.md$', f):
                continue
            if f.startswith("[TBD]"):
                files[f] = "ungraded"
            elif re.match(r'\[\d+(?:\.\d+)?\]', f):
                files[f] = "scored"
        # Include all slug dirs, even those with no resume files yet.
        # This ensures the Stop hook doesn't treat them as "new" when
        # the pipeline later adds resumes to them.
        snapshot[slug] = files
    return snapshot


def _write_session_state(session_id: str, project_dir: str) -> None:
    """Write per-session state file with initial stages/2_drafts/ snapshot."""
    state_dir = os.path.join(project_dir, ".devin", "session-state")
    os.makedirs(state_dir, exist_ok=True)
    state_file = os.path.join(state_dir, f"{session_id}.json")
    state = {
        "session_id": session_id,
        "initial_slugs": _snapshot_drafts(project_dir),
        "touched_slugs": [],
    }
    try:
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass  # non-fatal — Stop hook will check all stages/2_drafts/ as fallback


def main():
    # Pipeline mode (devin -p): skip context injection, don't write session state.
    # The orchestrator handles workflow integrity; no debug context needed.
    if os.environ.get("DEVIN_PIPELINE_MODE") == "1":
        sys.exit(0)

    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}

    session_id = data.get("session_id", "unknown")
    project_dir = os.environ.get("DEVIN_PROJECT_DIR", os.getcwd())

    # Write session state for the Stop hook to use
    _write_session_state(session_id, project_dir)

    # Inject the debug workflow context into the session
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": CONTEXT,
        }
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
