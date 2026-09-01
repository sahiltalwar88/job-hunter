#!/usr/bin/env python3
"""PreToolUse hook: block score tampering on resume files.

Blocks any tool call (exec, edit, write) that would rename a [score] resume-vN.md
file to a different score. The grader subagent renames from [TBD] to [score] —
that's allowed. Changing an existing [score] to a different [score] is blocked.

Reads PreToolUse stdin payload, exits 2 to block, exits 0 to allow.
"""
import json
import os
import re
import sys


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # can't parse — allow

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    # For exec: check if it's a mv/rename command targeting a resume file
    if tool_name == "exec":
        command = tool_input.get("command", "")
        # Look for mv commands that rename [score] resume files
        if re.search(r'mv\s+.*\[\d+(?:\.\d+)?\]\s+resume-v\d+\.md', command):
            # Check if it's renaming TO a different score
            scores = re.findall(r'\[(\d+(?:\.\d+)?)\]\s+resume-v\d+\.md', command)
            if len(scores) >= 2 and scores[0] != scores[1]:
                print(json.dumps({
                    "decision": "block",
                    "reason": "Score tampering detected: renaming a graded resume from "
                              f"[{scores[0]}] to [{scores[1]}] is not allowed."
                }))
                sys.exit(2)
        sys.exit(0)

    # For edit/write: block ALL writes to [score] resume files.
    # Scored files are only created by the grader via mv (rename from [TBD]).
    # This prevents an agent from writing a fake [9.0] resume-v1.md directly.
    if tool_name in ("edit", "write"):
        file_path = tool_input.get("file_path", "")
        match = re.search(r'\[(\d+(?:\.\d+)?)\]\s+resume-v\d+\.md', file_path)
        if match:
            print(json.dumps({
                "decision": "block",
                "reason": f"Writing to a scored resume file ({match.group(0)}) is not allowed. "
                          "Scored files are only created by the grader via rename from [TBD]."
            }))
            sys.exit(2)
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
