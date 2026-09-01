#!/usr/bin/env python3
"""Stop hook: block completion when a failing grade remains.

Checks if any resume in stages/2_drafts/ has a [TBD] prefix (meaning it was never
graded) or a score below the threshold (< 9). If so, blocks the Stop tool.

In pipeline mode (DEVIN_PIPELINE_MODE=1), exits immediately — the orchestrator
(pipeline_cli.py) handles workflow integrity at the pipeline level.

In debug mode, only checks slugs that this session touched (created or
modified), not all of stages/2_drafts/. This prevents stale resumes from previous
broken runs from blocking an unrelated debug session.

Reads Stop stdin payload, exits 2 to block, exits 0 to allow.
"""
import json
import os
import re
import sys


def _get_session_state(session_id: str) -> dict:
    """Load per-session state file created by session_start.py + audit_log.py.

    Returns dict with 'initial_slugs' (dict of slug → file states at session
    start) and 'touched_slugs' (list of slugs written to during this session).
    Returns empty dict if no state file exists.

    If the exact session_id doesn't match a state file, falls back to the most
    recently modified state file (handles cases where the Stop hook receives a
    different session_id than SessionStart, or no session_id at all).
    """
    state_dir = os.path.join(
        os.environ.get("DEVIN_PROJECT_DIR", os.getcwd()),
        ".devin", "session-state",
    )

    # Try exact match first
    state_file = os.path.join(state_dir, f"{session_id}.json")
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Fall back to most recently modified state file
    if not os.path.isdir(state_dir):
        return {}
    state_files = [
        os.path.join(state_dir, f)
        for f in os.listdir(state_dir)
        if f.endswith(".json")
    ]
    if not state_files:
        return {}
    # Sort by modification time, most recent first
    state_files.sort(key=os.path.getmtime, reverse=True)
    try:
        with open(state_files[0]) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _all_draft_slugs() -> set[str]:
    """Return the set of slug directories in stages/2_drafts/."""
    drafts_dir = os.path.join(
        os.environ.get("DEVIN_PROJECT_DIR", os.getcwd()), "stages", "2_drafts"
    )
    if not os.path.isdir(drafts_dir):
        return set()
    return {
        d for d in os.listdir(drafts_dir)
        if os.path.isdir(os.path.join(drafts_dir, d))
    }


def _check_slug_for_issues(slug: str) -> list[str]:
    """Check a single slug's stages/2_drafts/ folder for ungraded or below-threshold resumes.

    Returns a list of issue description strings.
    """
    drafts_dir = os.path.join(
        os.environ.get("DEVIN_PROJECT_DIR", os.getcwd()), "stages", "2_drafts"
    )
    slug_dir = os.path.join(drafts_dir, slug)
    if not os.path.isdir(slug_dir):
        return []

    issues = []
    for f in os.listdir(slug_dir):
        if not f.endswith(".md"):
            continue
        if not re.search(r'resume-v\d+\.md$', f):
            continue

        # Check for [TBD] prefix — ungraded
        if f.startswith("[TBD]"):
            issues.append(f"stages/2_drafts/{slug}/{f}: ungraded (TBD)")
            continue

        # Check for score prefix
        score_match = re.match(r'\[(\d+(?:\.\d+)?)\]', f)
        if score_match:
            score = float(score_match.group(1))
            if score < 9.0:
                issues.append(f"stages/2_drafts/{slug}/{f}: score {score} below threshold")

    return issues


def main():
    # Pipeline mode (devin -p): the orchestrator handles workflow integrity.
    # Individual LLM calls shouldn't be blocked by global stages/2_drafts/ state.
    if os.environ.get("DEVIN_PIPELINE_MODE") == "1":
        sys.exit(0)

    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # can't parse — allow

    session_id = data.get("session_id", "")
    session_state = _get_session_state(session_id)

    # If we have session state, only check slugs this session touched
    # or that are new since session start.
    if session_state:
        initial_slugs = set(session_state.get("initial_slugs", {}).keys())
        touched_slugs = set(session_state.get("touched_slugs", []))
        all_slugs = _all_draft_slugs()
        # Check: slugs touched by this session + slugs not present at session start
        slugs_to_check = touched_slugs | (all_slugs - initial_slugs)
    else:
        # No session state — check all of stages/2_drafts/ (backward compat)
        slugs_to_check = _all_draft_slugs()

    issues = []
    for slug in slugs_to_check:
        issues.extend(_check_slug_for_issues(slug))

    if issues:
        print(json.dumps({
            "decision": "block",
            "reason": "Cannot complete: ungraded or below-threshold resumes remain in stages/2_drafts/:\n"
                      + "\n".join(f"  - {i}" for i in issues)
                      + "\nAll resumes must be graded and either moved to ready/ or rejected/."
        }))
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
