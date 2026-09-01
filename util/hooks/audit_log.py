#!/usr/bin/env python3
"""PostToolUse audit logging hook.

Logs file-writing tool calls (exec, edit, write) to .grades.log when
they touch pipeline files (resume, JD, grade JSON, verification JSON).
This gives debug sessions the same audit trail that the automated
pipeline produces.

Log format (same as the automated pipeline):
  timestamp | tool | file_path | action

The hook never blocks — it only logs. Exit 0 always (errors are swallowed
so they never interfere with the agent's work).
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


PIPELINE_PATTERNS = [
    re.compile(r"resume-v\d+\.md$"),
    re.compile(r"job-description\.md$"),
    re.compile(r"grade-v\d+\.json$"),
    re.compile(r"verification\.json$"),
]


def extract_paths(tool_name: str, tool_input: dict) -> list[str]:
    """Extract file paths from the tool input."""
    paths = []
    if tool_name in ("edit", "write"):
        p = tool_input.get("file_path", "")
        if p:
            paths.append(p)
    elif tool_name == "exec":
        # Look for file paths in the command string
        cmd = tool_input.get("command", "")
        # Match paths that look like pipeline files
        for m in re.finditer(r'[\w/.\-]+\[.*?\]\s+\w+\.md', cmd):
            paths.append(m.group(0))
        # Match mv/rename targets
        for m in re.finditer(r'(?:mv|rename)\s+(\S+)', cmd):
            paths.append(m.group(1))
    return paths


def is_pipeline_file(path: str) -> bool:
    """Check if a path looks like a pipeline file."""
    return any(p.search(path) for p in PIPELINE_PATTERNS)


def _update_touched_slugs(session_id: str, project_dir: str, paths: list[str]) -> None:
    """Update the touched_slugs list in the session state file.

    Extracts slug names from paths that contain stages/2_drafts/<slug>/ and adds them
    to the session state's touched_slugs list (used by block_failing_grade.py).
    """
    if not session_id or session_id == "unknown":
        return

    state_file = os.path.join(project_dir, ".devin", "session-state", f"{session_id}.json")
    if not os.path.exists(state_file):
        return

    try:
        with open(state_file) as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    touched = set(state.get("touched_slugs", []))
    for p in paths:
        # Extract slug from paths like stages/2_drafts/<slug>/[TBD] resume-v1.md
        m = re.search(r'drafts/([^/]+)/', p)
        if m:
            touched.add(m.group(1))

    if touched != set(state.get("touched_slugs", [])):
        state["touched_slugs"] = sorted(touched)
        try:
            with open(state_file, "w") as f:
                json.dump(state, f, indent=2)
        except OSError:
            pass


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # can't parse — don't interfere

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", {})
    success = tool_response.get("success", True)

    # Only log file-writing tools
    if tool_name not in ("exec", "edit", "write"):
        sys.exit(0)

    paths = extract_paths(tool_name, tool_input)
    pipeline_paths = [p for p in paths if is_pipeline_file(p)]
    if not pipeline_paths:
        sys.exit(0)

    # Determine the project directory
    project_dir = os.environ.get("DEVIN_PROJECT_DIR", os.getcwd())
    log_path = Path(project_dir) / ".grades.log"

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    action = "wrote" if success else "failed-write"

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            for p in pipeline_paths:
                f.write(f"{timestamp} | {tool_name} | {p} | {action}\n")
    except Exception:
        pass  # never let logging interfere with the agent

    # Update session state with touched slugs (for Stop hook scoping)
    session_id = data.get("session_id", "")
    _update_touched_slugs(session_id, project_dir, pipeline_paths)

    sys.exit(0)


if __name__ == "__main__":
    main()
