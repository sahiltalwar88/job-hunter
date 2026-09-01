# Orchestrator-owned LLM output writes

**Status:** accepted

LLM subagents (grader, truthfulness reviewer) no longer write files directly
to the filesystem. Instead, they return their JSON output via stdout, and the
orchestrator (LangGraph step nodes) captures the stdout, parses the JSON, and
performs all file writes — grade files, resume renames, `.grades.log` appends,
verification files.

The customizer subagent is exempt: it must edit resume files in place and run
`count_lines.py`, so it retains `dangerous` permission mode with scoped
permissions.

## Considered Options

- **LLM writes directly (current architecture):** The grader and truthfulness
  reviewer run with `--permission-mode dangerous` and are instructed to write
  JSON to `.grading/{slug}/grade-vN.json` and `.veracity/{slug}/verification.json`,
  rename resume files via `mv`, and append to `.grades.log` via `echo`. This
  creates a significant security risk: a hallucinated path or prompt injection
  could allow the LLM to overwrite critical source code, configuration files,
  or the enrichment database. The LLM has full filesystem access with no
  sandboxing. Rejected because the attack surface is unnecessary — the
  orchestrator already controls the pipeline flow and can perform these writes
  itself.

- **Orchestrator captures stdout, writes files (chosen):** The grader and
  truthfulness reviewer run in `normal` permission mode (read-only auto-approve,
  no writes or exec). They return JSON via stdout. The step nodes capture
  stdout, parse JSON, write grade/verification files, rename resumes, and
  append to `.grades.log`. This eliminates the LLM's filesystem write access
  entirely for these two subagents. The `normal` mode is viable in
  non-interactive `devin -p` calls because these subagents only need reads,
  which `normal` auto-approves without prompting.

- **Sandboxed temp directory:** LLM writes to a sandboxed temp directory, the
  orchestrator moves files into place. Rejected because it still gives the LLM
  write access (just to a temp dir), adds complexity, and doesn't address the
  core issue — the LLM shouldn't write files at all when the orchestrator can
  do it from stdout.

## Consequences

- **Grader and truthfulness reviewer switch to `normal` permission mode.**
  Their agent profiles (`agent-profiles/resume-grader.md`,
  `agent-profiles/truthfulness-reviewer.md`) remove write/exec from allowed
  tools. The `devin_cli.py` wrapper gains a `permission_mode` parameter
  (default `dangerous` for backward compat) so callers can specify the mode.

- **Protocol files lose their "File Operations" sections.**
  `_config/grading-protocol.md` and `_config/veracity-protocol.md` no longer
  instruct the LLM to write files, rename resumes, or append to logs. Those
  operations become orchestrator responsibilities, documented in the step node
  code.

- **Step nodes do more work.** `step7_grade_resume.py` and
  `step9_truthfulness.py` now parse stdout JSON as the primary path (the
  existing `_try_parse_json` fallback becomes the primary), write the output
  files, and perform renames/logs. The file-writing code already exists in
  these nodes (they already do `rename_with_grade()` and
  `append_grades_log()`) — the change is that the LLM no longer also does it.

- **Customizer keeps `dangerous` mode.** The customizer must edit resume files
  in `drafts/<slug>/` and run `python3 scripts/count_lines.py`. No
  non-prompting permission mode allows writes + exec. The customizer gets
  scoped permissions (allow `Write(drafts/**)`, deny `Write(.env*)`,
  `Read(~/.ssh/**)`, etc.) and JD injection scrubbing to limit blast radius.

- **Prompt injection resistance improves.** The grader and truthfulness
  reviewer process untrusted JD text. In `normal` mode, even if a malicious JD
  contains prompt injection instructions, the LLM cannot write files or execute
  commands — it can only read and return text. The attack surface narrows from
  "full filesystem access" to "can read files and return text in stdout."

- **FakeLLM test path becomes the production path.** Tests already use
  FakeLLM which returns JSON via stdout (the `_try_parse_json` fallback). With
  this change, that fallback becomes the primary parsing path, so tests
  require minimal changes.
