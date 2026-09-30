## Routing Table

| Task | Where to go | Load first |
|------|-------------|------------|
| Discover new jobs | `python3 -m pipeline.helpers.list_feasible_jobs --summary` → diff against existing folders | This workspace's folder listing |
| Query feasible jobs (paginated) | `python3 -m pipeline.helpers.list_feasible_jobs --limit N --offset N` | Schema in this file |
| Tag jobs with feasibility | Pipeline runs `pipeline/infrastructure/feasibility_checker.py` automatically (Step 1). Prompt in `config.json` → `feasibility_prompt`. Verdicts stored in `data/jobs.db`. | `pipeline/infrastructure/feasibility_checker.py`, `pipeline/infrastructure/enrichment_store.py` |
| Fetch JD text for feasible jobs | `python3 -m pipeline.helpers.fetch_jds` | Scraper repo (imports `fetch()` with exponential backoff). Writes to `data/jobs.db`, not `all_jobs.json`. |
| Start a new job application | The workflow below (13 active steps, 4 deferred) → `_config/profile/base-resume/` + `_config/profile/full-experience/` + `_config/resume-customization-protocol.md` | The workflow + both profile docs |
| Grade a job description | `stages/1_listings/<company-role>/[TBD] job-description.md` → rename to `[score] job-description.md` | `_config/profile/base-resume/` (primary) + `_config/profile/full-experience/` (informs — catches experience not on the generic resume) |
| Auto-reject a poor fit (< 6) | Move to `stages/7_trash/<company-role>/` | JD grade < 6 |
| Auto-reject clearance-required | Move to `stages/7_trash/<company-role>/` (do not grade) | JD requires security clearance — detected by LLM during JD grading (ADR-0007, no regex) |
| Abandon a listing (weak fit) | Move to `stages/6_rejected/[JOB-FIT] <company-role>/` | JD grade 6–7.9 |
| Customize a resume | Pre-copy `_config/profile/base-resume/base-resume.md` to `stages/2_drafts/<company-role>/[TBD] resume-v1.md` → spawn the `customizer` subagent (edits in place, reads `_config/resume-customization-protocol.md` + `_config/profile/full-experience/` + few-shot examples, self-measures with `python3 -m pipeline.helpers.count_lines`, self-trims if over 75) → pass the JD grade from the `[score] job-description.md` filename | Resume protocol + generic resume (base, pre-copied) + full experience (sourcing reference) + few-shot examples |
| Grade a resume | `stages/2_drafts/<company-role>/[TBD] resume-vN.md` → rename to `[score] resume-vN.md` | The JD from `stages/1_listings/<company-role>/` |
| Optimization review (can we improve?) | Stay in `stages/2_drafts/<company-role>/` — loop back to customize if yes. Continue until no further truthful improvement is possible, regardless of grade. | Current resume + JD + `_config/profile/full-experience/` (for additional sourcing material) |
| Truthfulness review | Check `stages/2_drafts/<company-role>/[score] resume-vN.md` against `_config/profile/full-experience/` (the superset) | Resume + full experience |
| Abandon a listing (couldn't optimize) | Move to `stages/6_rejected/[RESUME] <company-role>/` | No resume version passed grade ≥ 9 + veracity |
| Move to ready | `stages/2_drafts/` → `stages/4_ready/<company-role>/` | Passing resume + JD |
| Convert resume to PDF | `python3 -m pipeline.helpers.md_to_pdf` | Resume markdown file |
| Measure resume rendered lines | `python3 -m pipeline.helpers.count_lines <path> [--json] [--wrap-chars N]` | Resume markdown file. Used by the customizer to self-measure against the line budget (target 70, ceiling 75). |
| Run the automated pipeline | `python3 -m pipeline` | `config.json` for thresholds/timeouts |
| Check pipeline timer status | `systemctl --user status job-hunter-pipeline.timer` | — |
| View pipeline logs | `logs/pipeline-*.log` (local) or `journalctl --user -u job-hunter-pipeline.service` | — |
| Review grading audit trail | `.grades.log` (append-only, tracked) | — |
| Understand the workspace | `IDENTITY.md` (folder map) → this file (routing + workflow) | Both layers |
| Understand the architecture visually | `docs/atlas.html` (interactive isometric atlas) or `docs/SYSTEM.md` (text twin) | `docs/atlas/data.mjs` is the source — rebuild with `node docs/atlas/build.mjs` |
| Review conventions/voice | `_config/conventions.md`, `_config/voice.md`, `_config/glossary.md` | All three |
| Review grading/veracity protocols | `_config/grading-protocol.md`, `_config/veracity-protocol.md` | Both protocol files |

## Session Start Protocol

1. Read `IDENTITY.md` for the workspace map.
2. Read the workflow definition below (13 active steps, 4 deferred).
3. For architecture questions, read `docs/SYSTEM.md` (text twin of the atlas) or open `docs/atlas.html` (interactive). The source is `docs/atlas/data.mjs` — rebuild with `node docs/atlas/build.mjs` after edits.
4. Check `stages/1_listings/` and `stages/2_drafts/` for in-progress applications — resume where left off.
5. Check for new jobs: run steps 1-2 if needed (feasibility check + fetch_jds), then query `python3 -m pipeline.helpers.list_feasible_jobs` for feasible jobs not yet processed in this workspace.
6. Load `_config/conventions.md` and `_config/voice.md` before producing any output.

## Automation

The pipeline is fully automated by `python3 -m pipeline` (`pipeline/__main__.py`), a LangGraph-based orchestrator that:

- **Runs hourly** via systemd user timer (`job-hunter-pipeline.timer`).
- **Prep phase** (plain functions): feasibility check, fetch JDs, discover new jobs.
- **Per-job phase** (LangGraph `StateGraph`): ingest → grade JD → triage → [customize → grade resume → veracity → should_continue] loop → final veracity gate → finalize → ready/rejected. Each job runs as a separate graph invocation with `thread_id=slug` for per-job checkpointing.
- **Checkpointer** (`SqliteSaver` at `data/jobs.db`) provides per-job resumability — an interrupted hourly run resumes from the last checkpoint.
- **Clearance detection is LLM-only** (ADR-0007) — no regex filter. The JD grading prompt returns `CLEARANCE` instead of a grade if the job requires clearance.
- **Optimize loop terminates** via grade + gaps + iteration count + LLM "can you improve?" check (ADR-0009).
- **Invokes a configured LLM CLI** (`devin -p`, `codex exec`, or `claude -p`) via provider adapters wrapped by `pipeline/infrastructure/llm_interface.py:RealLLM`. Select it with `config.json` → `llm_provider`; `--llm-provider` overrides it for one run.
- **Writes transient output** to `.grading/<slug>/` and `.veracity/<slug>/` (gitignored, cleaned after processing).
- **Appends every grade** to `.grades.log` (tracked, append-only audit trail).
- **Saves state** to `.devin/pipeline-state.json` (gitignored) — tracks last scraper SHA and run metadata.
- **Logs each run** to `logs/pipeline-YYYY-MM-DD-HHMM.log` (gitignored).
- **Sends Pushover notifications** via `pipeline/infrastructure/notify.py` — run summaries + high-priority for ready/error.
- **Commits and pushes** all changes at the end of every successful run.

### Manual run

```bash
python3 -m pipeline
python3 -m pipeline --dry-run       # process but don't move files or commit
python3 -m pipeline --job <slug>    # reprocess a single job
python3 -m pipeline --step <name> --job <slug>  # run one node for debugging
```

### Timer management

```bash
systemctl --user start job-hunter-pipeline.timer    # start
systemctl --user enable job-hunter-pipeline.timer   # enable on boot
systemctl --user status job-hunter-pipeline.timer   # check status
systemctl --user stop job-hunter-pipeline.timer     # stop
journalctl --user -u job-hunter-pipeline.service    # view logs
```

### Configuration

Pipeline config lives in `config.json` (gitignored, copy from `config.example.json`). Pushover credentials go in `.devin/pipeline.env` (gitignored, copy from `.devin.example/pipeline.env.example`).

### Interactive mode

For working on a specific job interactively (instead of the automated pipeline), custom subagent profiles and hooks provide the same integrity guarantees:

**Subagent profiles** (in `util/agent-profiles/`):
- `customizer` — customizes resumes for specific JDs by drawing from the reference pool (base resume, full experience, few-shot examples). Self-measures with `count_lines` and self-trims if over the line ceiling (75). The optimizer is the same agent re-invoked with grader feedback.
- `resume-grader` — grades resumes against JDs with realistic recruiter framing. Writes JSON to `.grading/`, renames the resume file with the score prefix, appends to `.grades.log`.
- `truthfulness-reviewer` — verifies every resume claim against the full experience superset. Writes JSON to `.veracity/`.

**Hooks** (in `.devin/hooks.v1.json`, scripts in `util/hooks/`):
- **PreToolUse** (`block_score_tamper.py`) — blocks renaming a `[score]` file to a different score. The grader subagent owns the rename from `[TBD]` → `[score]`.
- **PreToolUse** (`restrict_exec.py`) — universal exec whitelist, **pipeline mode only** (`DEVIN_PIPELINE_MODE=1`). In debug mode, allows everything.
- **PostToolUse** (`audit_log.py`) — logs file writes to pipeline files (resumes, JDs, grade JSON) to `.grades.log`.
- **SessionStart** (`session_start.py`) — injects debug workflow context at session start.
- **Stop** (`block_failing_grade.py`) — blocks finishing if any resume in `stages/2_drafts/` has a score < 9.

**Debug workflow:**
1. Find a graded JD (≥8) in `stages/1_listings/<company-role>/[score] job-description.md`.
2. Customize: pre-copy `_config/profile/base-resume/base-resume.md` to `stages/2_drafts/<company-role>/[TBD] resume-v1.md`, then spawn the `customizer` subagent. It reads the pre-copied base + `_config/resume-customization-protocol.md` + `_config/profile/full-experience/` (sourcing) + few-shot examples, edits the resume in place, self-measures with `count_lines`, self-trims if over 75. Pass the JD grade (from the `[score] job-description.md` filename) to the customizer.
3. Grade: spawn the `resume-grader` subagent. It grades, renames with score, appends to `.grades.log`.
4. Iterate: if grade < 9, read feedback, improve (new version), re-grade. Up to 3 iterations. If you cannot reach 9 after 3 iterations, move to `stages/6_rejected/[RESUME] <company-role>/`.
5. Verify truthfulness: spawn the `truthfulness-reviewer` subagent. MANDATORY before moving to `stages/4_ready/`. If truthfulness fails, fix the untruthful claims and re-grade, or move to `stages/6_rejected/[RESUME] <company-role>/` if claims cannot be made truthful.
6. Move to `stages/4_ready/`: ONLY if grade ≥ 9 AND truthfulness verified. Move the entire folder (JD + resume) to `stages/4_ready/<company-role>/`.

## Scraper Integration

job-hunter reads job data from an external scraper repo via a configurable path or URL. The scraper produces `all_jobs.json` (a rolling master of scraped jobs) and delta files for incremental updates.

**Do NOT read `all_jobs.json` directly into context.** Use `python3 -m pipeline.helpers.list_feasible_jobs` to query it with pagination.

### Data contract

The scraper's job record schema is documented in the scraper's `docs/JOB_SCHEMA.md`. Key fields:

| Field | Type | Description |
|-------|------|-------------|
| `url` | string | Job URL. Unique key. Used for dedup. |
| `title` | string | Job title. |
| `company` | string | Employer name. |
| `location` | string | Job location. |
| `date_posted` | string | Posting date. |
| `salary` | string | Salary range if available. |
| `ats` | string | Source platform. |
| `description` | string | Full JD text. Fetched separately by `fetch_jds`. |
| `first_seen` | string | ISO timestamp when the scraper first saw this URL. |
| `feasible` | bool | Set by the pipeline's feasibility checker. Stored in the enrichment sidecar. |
| `feasibility` | string | Tier: `"preferred"`, `"yes"`, or `"no"`. Stored in the enrichment sidecar. |

### Transport

Configured via `config.json`:
- `scraper_transport: "filesystem"` (default) — read from a local path (`scraper_repo_path`).
- `scraper_transport: "http"` — fetch from a URL (`scraper_url`), e.g., GitHub Pages or raw.githubusercontent.com.

### Delta consumption

The scraper produces delta files (`output/deltas/index.jsonl` + per-run JSON files) for incremental updates. The pipeline:
1. **One-time setup:** Full sync from `all_jobs.json` into the SQLite mirror (`data/jobs.db`).
2. **Ongoing:** Delta-only — process new delta files, upsert by URL.
3. **On delta failure:** Log a loud error. Do NOT fall back to full sync.

### Querying feasible jobs (`python3 -m pipeline.helpers.list_feasible_jobs`)

```bash
# Summary: list feasible jobs (title/company/url only, no descriptions)
python3 -m pipeline.helpers.list_feasible_jobs --summary

# Full: first 10 feasible jobs with descriptions (for grading)
python3 -m pipeline.helpers.list_feasible_jobs --limit 10 --has-description

# Next 10 (pagination)
python3 -m pipeline.helpers.list_feasible_jobs --limit 10 --offset 10 --has-description

# Feasible jobs MISSING descriptions (need fetch_jds first)
python3 -m pipeline.helpers.list_feasible_jobs --missing-description --limit 50
```

## Workflow (13 active steps, 4 deferred)

### Stage 1 — Prep (scraper output + enrichment sidecar)

1. The pipeline runs its own feasibility checker (`pipeline/infrastructure/feasibility_checker.py`) to tag jobs with `feasible: true/false` + `feasibility` tier. The prompt lives in `config.json` → `feasibility_prompt`. Verdicts are stored in the enrichment sidecar (`data/jobs.db`), not in `all_jobs.json`.
2. Run `python3 -m pipeline.helpers.fetch_jds` to fetch JD descriptions for feasible jobs without them. Built-in exponential backoff for rate-limiting. Descriptions are stored in the enrichment sidecar.

### Stage 2-3 — Discovery + JD Ingestion (`stages/1_listings/`)

3. Check the scraper output for jobs not yet processed in this workspace. A "new" job is one whose URL does not already appear in `stages/1_listings/`, `stages/2_drafts/`, `stages/4_ready/`, `stages/3_in-progress/`, `stages/6_rejected/`, or `stages/7_trash/`. Only process jobs where `feasible: true`. **Never read `all_jobs.json` directly** — use `python3 -m pipeline.helpers.list_feasible_jobs`.
4. For each new feasible job, create `stages/1_listings/<company-role>/[TBD] job-description.md` with the JD text. No clearance check at ingestion (ADR-0007 — clearance is detected by the LLM during JD grading at step 5).

### Stage 4-6 — JD Grading + Triage (`stages/1_listings/` → `stages/7_trash/` / `stages/6_rejected/` / `stages/2_drafts/`)

5. Grade the JD against your base resume (`_config/profile/base-resume/`), informed by your full career history (`_config/profile/full-experience/`) — consult the full experience to catch relevant experience not surfaced on the generic resume. Rename to `[score] job-description.md`. **Clearance detection (ADR-0007):** the LLM returns `CLEARANCE` instead of a grade if the JD requires security clearance. The conditional edge routes clearance jobs to `stages/7_trash/`.
6. If grade < 6, move to `stages/7_trash/<company-role>/` — auto-reject.
7. If grade is 6–7.9, move to `stages/6_rejected/[JOB-FIT] <company-role>/` and move on.
8. If the grade is >= 8, **pre-copy the base resume** (`_config/profile/base-resume/base-resume.md`) to `stages/2_drafts/<company-role>/[TBD] resume-v1.md`, then customize it in place — pulling relevant experience from the full career history (`_config/profile/full-experience/`) onto the base as the JD demands, following the customization protocol (`_config/resume-customization-protocol.md`). The JD grade is passed to the customizer. The customizer self-measures with `python3 -m pipeline.helpers.count_lines` and self-trims if over 75 rendered lines.

### Stage 7-9 — Resume Customization Loop (`stages/2_drafts/`)

9. Grade resume against job description. Rename to `[score] resume-vN.md`.
10. Can we improve the grade while staying truthful (sourcing only from `_config/profile/full-experience/`)? This loop continues regardless of whether the grade is already ≥ 9 — keep iterating until no further truthful improvement is possible. If yes, repeat steps 8 and 9. If no, exit loop.

### Stage 10 — Threshold Gate

11. Is final grade ≥ 9? If no, move to `stages/6_rejected/[RESUME] <company-role>/` — not worth applying.

### Stage 11 — Truthfulness Review (`stages/2_drafts/`)

12. Truthfulness review: verify every claim, skill, and experience on the resume against the full career history (`_config/profile/full-experience/` — the superset). Anything not verifiable there → fix or remove and repeat from step 8. If all claims verified, proceed.

### Stage 12 — Ready (`stages/4_ready/`)

13. Move passing resume markdown to `stages/4_ready/<company-role>/`.

### Stages 13-16 — Application + Submission (DEFERRED)

14. Go to job posting, start application.
15. Fill in fields, get to last page, and STOP — DO NOT SUBMIT.
16. Text user "ready for review: <link>".
17. (User reviews, submits manually, moves to `stages/5_submitted/`.)

## Shared Config References

- `_config/conventions.md` — file naming patterns, folder naming, format rules
- `_config/glossary.md` — domain terms used throughout the workflow
- `_config/voice.md` — tone, audience, and output standards
- `_config/grading-protocol.md` — resume grading rubric (read by grader subagent)
- `_config/veracity-protocol.md` — truthfulness verification protocol (read by truthfulness subagent)
- `_config/resume-customization-protocol.md` — customization protocol (read by customizer subagent)
