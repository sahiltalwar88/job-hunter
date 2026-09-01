# Conventions

## File Naming

- **Grade prefix:** Every graded file is renamed `[score] filename.ext`. Ungraded files use `[TBD] filename.ext`.
  - Example: `[TBD] job-description.md` → `[8] job-description.md`
  - Example: `[TBD] resume-v1.md` → `[9.5] resume-v1.md`
- **Resume versions:** Number sequentially within an application: `resume-v1.md`, `resume-v2.md`, etc.
- **Job descriptions:** Always named `job-description.md` (with grade prefix).

## Folder Naming

- **Application folders:** `<company-role>/` — lowercase, hyphenated.
  - Example: `google-direct-of-engineering/`, `stripe-vp-engineering/`
- **One folder per application** across all pipeline stages. Same `<company-role>` name used in `listings/`, `drafts/`, `ready/`, `in-progress/`, `submitted/`.
- **Trash folders:** `trash/<company-role>/` — same naming convention, no prefix. Used for auto-rejects: JD grade < 6, or clearance-required positions.
- **Rejected folders:** Prefixed with reason:
  - `[JOB-FIT] <company-role>/` — JD grade 6–7.9, weak overall fit
  - `[RESUME] <company-role>/` — resume grade < 9, couldn't optimize enough

## File Formats

- Job descriptions: Markdown (`.md`)
- Resumes: Markdown (`.md`) — customized resumes are written and verified as markdown. PDF rendering is available via `pipeline/helpers/md_to_pdf.py` for final output, but grading and review happen on the markdown.
- Notes, links, summaries: Markdown (`.md`)

## Pipeline Flow

- Active pipeline: `listings/` → `drafts/` → `ready/`
- Deferred stages: `in-progress/` → `submitted/` (not yet implemented — form-filling is future work)
- Clearance-required JD → move to `trash/<company-role>/` (auto-reject, do not grade). Detected by the LLM during JD grading (ADR-0007 — no regex filter). The JD grader returns `CLEARANCE` instead of a grade.
- JD grade < 6 → move to `trash/<company-role>/` (auto-reject, periodically deleted)
- JD grade 6–7.9 → move to `rejected/[JOB-FIT] <company-role>/` (weak fit)
- JD grade 8+ → proceed to resume customization in `drafts/`
- Resume grade < 9 after optimization → move to `rejected/[RESUME] <company-role>/`
- Never skip stages. Never move backward (except within the `drafts/` loop).
- `submitted/` is user-only. The agent never writes there.
- `trash/` is periodically emptied by the user. The agent may move files there but should not delete them.

## Profile Files

Two reference docs under `profile/` define the candidate's background. They have distinct roles:

- **`_config/profile/base-resume/base-resume.md`** — the **base resume**. The only customization base: every tailored resume starts from this template (structure, formatting, existing bullets preserved). Also the primary reference when grading a JD.
- **`_config/profile/full-experience/full-experience.md`** — the **full experience** (LinkedIn). Reference pool material for optimal JD match:
  - **JD grading:** consulted to catch relevant experience not surfaced on the generic resume, so the grade reflects what's available to bring to the role.
  - **Customization:** supplies experience that can be pulled onto the base resume (new bullets, skills, technologies) — but only material that appears in LinkedIn.
  - **Never a customization base.** A customized resume is never built directly from the LinkedIn experience. It is always the generic resume + pulled-in LinkedIn material.
  - **Truthfulness review:** verifies every claim on a customized resume against this (the superset). Anything not verifiable here → fix or remove.
- **`_config/resume-customization-protocol.md`** — the customization protocol (emphasis, formatting, line budget, self-measurement, hard rules). Read by the customizer before every customization step.

## Deduplication

- Before processing a job from the scraper, check its URL against all workspace folders.
- If the URL already exists in `listings/`, `drafts/`, `ready/`, `in-progress/`, `rejected/`, `trash/`, or `submitted/`, skip it.
- Same role reposted with a new URL is treated as a new job (the JD grade will catch it).

## Automation Artifacts

The automated pipeline (`pipeline/__main__.py`) uses several transient and
persistent artifacts:

| Path | Purpose | Tracked? |
|------|---------|----------|
| `.grading/<slug>/` | Transient grader output (grade-vN.json). Cleaned after processing. | No (gitignored) |
| `.veracity/<slug>/` | Transient veracity output (verification.json). Cleaned after processing. | No (gitignored) |
| `.grades.log` | Append-only audit trail of every grade. | Yes (committed) |
| `.devin/pipeline-state.json` | Pipeline state (last scraper SHA, processed URLs). | No (gitignored) |
| `.devin/pipeline.lock` | File lock to prevent concurrent runs. | No (gitignored) |
| `config.json` | Pipeline configuration (thresholds, timeouts, model names). | Yes (committed) |
| `.devin/pipeline.env` | Pushover credentials (user fills in from `.env.example`). | No (gitignored) |
| `logs/pipeline-*.log` | Timestamped per-run logs. | No (gitignored) |

## Grading and Veracity Protocols

- `_config/grading-protocol.md` — read by the the grader model resume grader. Defines the grading scale, per-criterion assessment levels (DIRECT_HIT, ADDRESSED, PARTIAL, GAP), critical requirement rules, and JSON output format.
- `_config/veracity-protocol.md` — read by the the grader model truthfulness checker. Defines how claims are verified against the LinkedIn experience.

Both protocols use a fresh, isolated model context — the grader/verifier never sees the customizer's reasoning or prompt history.

## Subagent Profiles and Hooks

Subagent profiles (in `.devin/agents/`):
- `customizer.md` — custom subagent pinned to `customizer-model` for resume customization. Reads the customization protocol, draws from the reference pool (base resume, LinkedIn, few-shot examples), writes the customized resume, self-measures with `count_lines.py`, self-trims if over the line ceiling (75). The optimizer is the same agent re-invoked with grader feedback.
- `resume-grader.md` — custom subagent pinned to `grader-model` for resume grading. Reads the grading protocol, grades each JD requirement, writes JSON to `.grading/`, renames the resume with the score prefix, appends to `.grades.log`.
- `truthfulness-reviewer.md` — custom subagent pinned to `grader-model` for truthfulness verification. Reads the veracity protocol, checks every claim against LinkedIn, writes JSON to `.veracity/`.

Hooks (in `.devin/hooks.v1.json`):
- `PreToolUse` on `exec|edit|write` (`block_score_tamper.py`): blocks score tampering (renaming a graded resume to a different score, or overwriting a graded resume).
- `PreToolUse` on `exec` (`restrict_exec.py`): universal exec whitelist, **pipeline mode only** (`DEVIN_PIPELINE_MODE=1`). In debug mode, allows everything. Tier 1 (pipeline): `python3 -m pipeline.helpers.count_lines`, `python3 -m pytest`, `mv`, `echo "..." >> .grades.log`. Tier 2 (general): `git`, `gh`, `ls`, `cd`, `cat`, `head`, `tail`, `wc`, `pwd`, `diff`, `grep`, `find`, `which`, `file`, `echo`, `cp`. Chaining (&&, ;, |) allowed if every sub-command is whitelisted. Blocks `$()`, backticks, single `>`, and any non-whitelisted command. See ADR-0002.
- `PostToolUse` on `exec|edit|write` (`audit_log.py`): logs file writes to pipeline files (resumes, JDs, grade JSON, verification JSON) to `.grades.log`. Non-blocking.
- `SessionStart` (`session_start.py`): injects debug workflow context at session start — tells the agent about the subagent profiles, integrity rules, and key files.
- `Stop` (`block_failing_grade.py`): blocks completion when ungraded or below-threshold resumes remain in `drafts/`.

All hook scripts live in `util/hooks/` and use `$DEVIN_PROJECT_DIR` for portability. Tests are in `util/hooks/tests/test_hooks.py`.

## Scheduling

The pipeline runs hourly via systemd user units:
- `~/.config/systemd/user/job-hunter-pipeline.service` — oneshot service that runs `pipeline_cli.py`.
- `~/.config/systemd/user/job-hunter-pipeline.timer` — hourly timer with boot catch-up (`Persistent=true`).

Manage with:
```bash
systemctl --user start job-hunter-pipeline.timer    # start the timer
systemctl --user enable job-hunter-pipeline.timer   # enable on boot
systemctl --user status job-hunter-pipeline.timer   # check status
systemctl --user stop job-hunter-pipeline.timer     # stop the timer
journalctl --user -u job-hunter-pipeline.service    # view service logs
```
