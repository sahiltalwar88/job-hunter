# job-hunter — System Definition

_**This file is the living source of truth for the design.** The interactive atlas is built from the same data._

_Question status: **1 open · 4 resolved**._

## One paragraph

An AI agent workspace that automates job applications: pulls listings from a job scraper, grades them against a candidate profile, customizes resumes through an iterative grade-and-improve loop, and parks passing resumes for manual application. A LangGraph StateGraph orchestrates the per-job pipeline; LLMs handle 5 cognitive tasks. Hourly via systemd.

## Decisions locked

| Axis | Decision | ADR |
|---|---|---|
| Architecture | Code orchestrates, LLMs cognate — 5 LLM tasks only, all plumbing in Python | — |
| Self-measurement | Customizer runs count_lines during its own turn and self-trims | [0001](../adr/0001-self-measurement-in-customizer-turn.md) |
| Exec restriction | Universal whitelist hook (pipeline mode only) — two-tier: count_lines/pytest/mv/echo + git/gh/ls/cat/grep/etc. | [0002](../adr/0002-universal-exec-whitelist-hook.md) |
| Line budget | Target 70, hard ceiling 75 rendered lines | [0003](../adr/0003-line-budget-target-70-ceiling-75.md) |
| Widow elimination | Dropped entirely — cosmetic concern, not worth the time budget | [0004](../adr/0004-drop-widow-elimination.md) |
| Enrichment storage | SQLite sidecar instead of writing to all_jobs.json | — |
| Agent separation | Customizer, grader, and truthfulness verifier are distinct agents | — |
| Pipeline engine | LangGraph StateGraph with Pydantic state + SqliteSaver checkpointer | [0005](../adr/0005-langgraph-as-pipeline-engine.md) |
| Clearance detection | LLM-only (no regex) — JD grader returns CLEARANCE instead of a grade | [0007](../adr/0007-llm-only-clearance-check.md) |
| Optimize termination | LLM "can you improve?" check + hard limits (grade ≥ 9 + no core gaps, max iterations) | [0009](../adr/0009-optimize-cycle-termination.md) |

## Cost model

## Reading order (the atlas chapters)

1. **The feed** — Strip everything away and this is the heartbeat: a scraper feeds jobs, a timer kicks the orchestrator every hour. _(adds JS, OR, ST)_
2. **Is it feasible?** — Before doing anything, the orchestrator tags each job: is this a role the candidate could plausibly get? _(adds ES, FC)_
3. **Getting the JD** — The scraper saves titles only. The orchestrator fetches full JD text. Clearance is detected by the LLM during JD grading — no regex filter. _(adds JF, CF)_
4. **Is it worth applying?** — The JD grader reads the candidate's profile and returns a score. The base resume and full career history are the reference material. _(adds JG, BR, LE)_
5. **Sorting the pile** — The orchestrator routes by grade: trash, rejected, or drafts. Only ≥ 8 proceeds to customization. _(adds TL, TR, RJ)_
6. **The reference pool** — Three documents form the pool the customizer draws from. The drafts folder is where the work happens. _(adds CG, DR)_
7. **Customizing the resume** — The orchestrator pre-copies the base resume. The customizer edits it in place, measures with count_lines, and self-trims if over 75 lines. _(adds CU, CL)_
8. **Grading the resume** — A separate agent grades the resume with realistic recruiter harshness. It owns the score rename. _(adds RG, GL)_
9. **Is it true?** — A truthfulness verifier checks every claim against the LinkedIn superset. Hooks enforce integrity throughout. _(adds TV, HK)_
10. **Done** — Passing resumes land in stages/4_ready/. The user gets a push notification. _(adds RD, NO)_
11. **Later** — Designed for, not switched on. Form-filling and submission are deferred. _(adds FF, SU)_
12. **The whole system** — Everything at once, for free exploration.

## Structures

### External

#### JS · Job Scraper

**In one line.** The adjacent repo that feeds raw job listings.

**What it does.** A GitHub Actions pipeline that scrapes LinkedIn job postings on an hourly schedule and commits them to a rolling master file.

**How it's built.** `../job-scraper/scrape_jobs.py` → `output/all_jobs.json` (3.9MB+, 7-day rolling window). Read-only from job-hunter's perspective.

**Steps in execution.**

1. **Scrape** — Hourly GitHub Actions workflow pulls new LinkedIn postings.
2. **Dedup** — Jobs deduped by URL across search terms and geos.
3. **Commit** — Results committed to all_jobs.json.

#### ST · Systemd Timer

**In one line.** The hourly heartbeat that kicks off the pipeline.

**What it does.** A systemd user timer that triggers the pipeline service every hour with boot catch-up.

**How it's built.** `job-hunter-pipeline.timer` → `job-hunter-pipeline.service`. **Hourly** with `Persistent=true` for boot catch-up.

**Steps in execution.**

1. **Tick** — Timer fires every hour.
2. **Launch** — Starts <code>pipeline/__main__.py</code> as a service.

### Prep

#### OR · Orchestrator

**In one line.** The brain — a LangGraph StateGraph that runs the per-job pipeline, with a plain-function prep phase.

**What it does.** The control plane: prep phase (feasibility, fetch, discover) runs as plain functions, then a per-job LangGraph StateGraph handles ingest, grading, customization, optimization loops, truthfulness, and routing. Each job runs as a separate graph invocation with SqliteSaver checkpointing for resumability. It invokes LLMs for cognitive tasks and handles all plumbing itself.

**How it's built.** `pipeline/__main__.py` + `pipeline/` package. Acquires a file lock, loads config from `config.json`, runs prep functions, invokes the per-job graph for each new job. Graph nodes call `devin -p` for LLM work via `pipeline/infrastructure/devin_cli.py` (wrapped by `pipeline/infrastructure/llm_interface.py`). Commits and pushes at the end.

**Steps in execution.**

1. **Lock** — Acquire <code>.devin/pipeline.lock</code> to prevent concurrent runs.
2. **Config** — Load thresholds, timeouts, model names from config.json.
3. **Prep 1-2** — Feasibility check + fetch JDs (plain functions).
4. **Prep 3** — Discover + dedup new jobs (plain function).
5. **Per-job graph** — For each new job: ingest → grade JD → triage → customize → grade resume → optimize loop → truthfulness → stages/4_ready/stages/6_rejected.
6. **Checkpoint** — SqliteSaver at <code>data/jobs.db</code> — interrupted runs resume per job.
7. **Finish** — Commit, push, notify, release lock.

**Questions.**

- ~~**Q-OR1** Should optimization iterations run in parallel for multiple jobs?~~ ✓ No — sequential is safer and cost is bounded by max_optimization_iterations (2026-08-24).

#### ES · Enrichment Sidecar

**In one line.** A SQLite database that stores pipeline enrichments separate from the scraper's output.

**What it does.** Holds feasibility tags and JD descriptions so the scraper's all_jobs.json stays read-only. Replaces the old architecture where enrichments were written directly to all_jobs.json (and lost on every scraper update).

**How it's built.** `data/enrichments.db`. Managed by `pipeline/infrastructure/enrichment_store.py`. Gitignored — regenerable from scraper data + LLM/HTTP fetches.

**Steps in execution.**

1. **Write feasibility** — <code>store.set_feasibility(url, feasible, tier, ...)</code>
2. **Write JD** — <code>store.set_description(url, text)</code>
3. **Read** — <code>store.merge_into_jobs(jobs)</code> merges sidecar data into job list.

#### FC · Feasibility Checker

**In one line.** LLM task 1 — tags each job as preferred, yes, or no.

**What it does.** An LLM that receives job metadata (title, company, location) and returns a tripartite verdict: "preferred" (Big Tech / top-tier fit), "yes" (fits but not Big Tech), or "no" (doesn't fit).

**How it's built.** `pipeline/infrastructure/feasibility_checker.py` (ported from scraper). Uses `DevinCLIChecker` with customizer-model via `llm.py`. Prompt in `config.json` → `feasibility_prompt`. Verdicts stored in the sidecar.

**Steps in execution.**

1. **Batch** — Load unchecked jobs from sidecar.
2. **Call LLM** — Send job metadata to customizer-model in batches.
3. **Parse** — Extract tier verdict (preferred/yes/no).
4. **Store** — Write feasible + feasibility fields to sidecar.

#### JF · JD Fetcher

**In one line.** Fetches JD descriptions from LinkedIn for feasible jobs.

**What it does.** A code step that fetches full JD text for feasible jobs that don't already have descriptions. The backfill scraper saves title/company/location/URL only — no JD text.

**How it's built.** `pipeline/helpers/fetch_jds.py`. Reuses scraper's `fetch()` with built-in exponential backoff for LinkedIn 429s (30s × 2^attempt, up to 4 retries). Writes to the sidecar, not all_jobs.json.

**Steps in execution.**

1. **Find gaps** — Query feasible jobs missing descriptions.
2. **Fetch** — HTTP GET each JD URL with backoff.
3. **Store** — Write description text to sidecar incrementally (crash-safe).

### Discovery

#### CF · Clearance Detection

**In one line.** LLM-only detection — auto-rejects clearance-required jobs during JD grading.

**What it does.** If a JD requires security clearance (active, eligible, TS/SCI, secret, ability to obtain, etc.), the JD grader LLM returns `CLEARANCE` instead of a grade. The conditional edge routes it to trash. No regex filter (ADR-0007).

**How it's built.** LLM-only — the JD grading prompt instructs the LLM to return `CLEARANCE` if the job requires clearance. The graph's `route_after_jd_grade` checks `state.jd_grade.is_clearance` and routes to the trash node.

**Steps in execution.**

1. **Grade JD** — LLM receives JD text.
2. **Detect** — If clearance required → returns <code>CLEARANCE</code> instead of a grade.
3. **Reject** — Graph routes to <code>stages/7_stages/7_trash/trash/&lt;lt;company-role&gt;/</code>.

#### LI · Listings

**In one line.** The folder where JD files await grading.

**What it does.** Each new feasible job gets a folder `stages/1_stages/1_listings/listings/<lt;company-role>/` with a `[TBD] job-description.md` file. After grading, the file is renamed to `[score] job-description.md`.

**How it's built.** Filesystem. One folder per job listing. `[TBD]` prefix = ungraded, `[score]` prefix = graded.

**Steps in execution.**

1. **Ingest** — Create folder + [TBD] JD file for each new feasible job.
2. **Grade** — JD grader renames [TBD] → [score].
3. **Route** — Triage moves the folder to stages/7_trash/, rejected/, or drafts/.

### Reference pool

#### BR · Base Resume

**In one line.** The generic resume — the only customization base.

**What it does.** Every tailored resume starts from this template. The orchestrator pre-copies it to `stages/2_stages/2_drafts/drafts/<lt;slug>/[TBD] resume-v1.md` before the customizer runs. The customizer edits the copy in place. Also the primary JD grading reference.

**How it's built.** `_config/_config/profile/base-resume/base-resume.md`. Root-owned, immutable. 70 rendered lines.

**Steps in execution.**


#### LE · LinkedIn Experience

**In one line.** The full LinkedIn career history — the sourcing reference.

**What it does.** Consulted during JD grading (to know what's available) and customization (to pull in experience not on the base resume). Never used as a customization base. Truthfulness review verifies against this.

**How it's built.** `_config/_config/profile/full-experience/full-experience.md`. Root-owned, immutable.

**Steps in execution.**


#### CG · Customization Protocol

**In one line.** The rules for customizing a resume — the single source of truth for the customizer.

**What it does.** Defines formatting, ordering, truthfulness, strategy rules, line budget (target 70, ceiling 75), and the self-measurement instruction. The customizer follows it strictly.

**How it's built.** `_config/resume-customization-protocol.md`. The single source of truth for customization rules — line budget, self-measurement, reference pool semantics, formatting, truthfulness constraints.

**Steps in execution.**


**Questions.**

- ~~**Q-CG1** Should the guide move to _config/ with the grading and veracity protocols?~~ ✓ Yes — agreed during grilling session (2026-08-24). Deferred to a separate task.

### JD grading

#### JG · JD Grader

**In one line.** LLM task 2 — grades a JD against the candidate profile.

**What it does.** Receives JD text, reads the base resume and full career history, and returns a grade (0-10) with justification. The orchestrator parses the text output and does the rename/triage.

**How it's built.** customizer-model via `devin -p`. Prompt: `build_jd_grading_prompt()` in `pipeline/step4_grade_jd.py`. Outputs `GRADE: N\nJUSTIFICATION: ...` or `CLEARANCE` to stdout. Code parses and renames.

**Steps in execution.**

1. **Read profile** — Read base resume + full career history.
2. **Grade** — Assess fit: experience match, realistic obtainability, gap analysis.
3. **Output** — Return GRADE: N + JUSTIFICATION to stdout.

### Triage

#### TL · Triage Logic

**In one line.** Routes JDs by grade: trash, rejected, or drafts.

**What it does.** Code that moves the listing folder based on the JD grade. Grade < 6 → trash. Grade 6-7.9 → rejected. Grade ≥ 8 → drafts (proceed to customization).

**How it's built.** `step5_triage_node()` in `pipeline/step5_triage.py`. Threshold from `config.jd_grade_threshold` (default 8). Uses `move_dir()` to relocate folders.

**Steps in execution.**

1. **Check grade** — Compare JD grade against thresholds.
2. **Route** — &lt;6 → stages/7_trash/, 6-7.9 → stages/6_rejected/[JOB-FIT], ≥8 → stages/2_drafts/

#### TR · Trash

**In one line.** Auto-rejected jobs — clearance-required or JD grade < 6.

**What it does.** Skimmable but periodically emptied. Two triggers: LLM detected a clearance requirement during JD grading, or JD grade below 6.

**How it's built.** Filesystem: `stages/7_stages/7_trash/trash/<lt;company-role>/`. Periodically deleted by the user.

**Steps in execution.**


#### RJ · Rejected

**In one line.** Abandoned listings — weak fit or couldn't optimize the resume.

**What it does.** Two subfolder types: `[JOB-FIT]` (JD grade 6-7.9, weak overall fit) and `[RESUME]` (resume grade < 9 after optimization loop). Kept for reference.

**How it's built.** Filesystem: `stages/6_rejected/[JOB-FIT] <company-role>/` or `stages/6_rejected/[RESUME] <company-role>/`.

**Steps in execution.**


### Customization

#### CU · Customizer

**In one line.** LLM task 3 — edits a pre-copied resume from the reference pool to optimally match the JD.

**What it does.** The orchestrator pre-copies the base resume to `stages/2_stages/2_drafts/drafts/<lt;slug>/[TBD] resume-v1.md`. The customizer edits it in place — pulling relevant experience from LinkedIn, rephrasing, condensing, reordering. Self-measures with count_lines and self-trims if over 75 rendered lines. Also serves as the optimizer (re-invoked with grader feedback). The JD grade is passed to the first customization prompt.

**How it's built.** customizer-model via `devin -p` (automated) or custom subagent profile `util/agent-profiles/customizer.md` (debug). Prompt: `build_customize_prompt()`. Follows the customization protocol strictly. Runs `count_lines` via exec for self-measurement.

**Steps in execution.**

1. **Read inputs** — Pre-copied base resume + protocol + LinkedIn + JD + JD grade + few-shot examples.
2. **Customize** — Edit the pre-copied resume in place. Draw from the reference pool to optimally match the JD. Rephrase, condense, reorder, pull — all valid.
3. **Self-measure** — Run <code>python3 -m pipeline.helpers.count_lines --json</code> on the output.
4. **Self-trim** — If &gt; 75 rendered lines, trim least JD-relevant bullets. Up to 3 passes.

**Questions.**

- ~~**Q-CU1** What happens if count_lines reports >75 after 3 trim passes?~~ ✓ Accept and proceed — the grader is the quality gate, not the line count (2026-08-24).

#### CL · count_lines

**In one line.** A script that measures rendered line count — the customizer's only exec tool.

**What it does.** Takes a resume markdown file and outputs per-bullet character count, rendered line count, widow flags (diagnostic only), total rendered lines, and total bullet count. The customizer runs it during its own turn to self-measure and self-trim.

**How it's built.** `pipeline/helpers/count_lines`. Human-readable default, `--json` for machine-readable. `--wrap-chars` CLI arg (default 102). Detects `* ` bullet syntax with trailing two-space line breaks stripped.

**Steps in execution.**

1. **Parse** — Extract bullets from markdown (lines starting with <code>* </code>).
2. **Count** — Per-bullet char count → ceil(chars/102) rendered lines.
3. **Flag** — Widow flags for 103-115 and 205-217 ranges (diagnostic only).
4. **Output** — Total rendered lines + per-bullet breakdown.

#### DR · Drafts

**In one line.** The folder where resumes go through the customization and grading loop.

**What it does.** Each job gets a folder `stages/2_stages/2_drafts/drafts/<lt;company-role>/` with `[TBD] resume-vN.md` files. After grading, renamed to `[score] resume-vN.md`. The optimization loop iterates here until the grade is ≥ 9 or no further improvement is possible.

**How it's built.** Filesystem. `[TBD]` = ungraded, `[score]` = graded. Version numbers increment in the grade-improve loop (v1, v2, v3...).

**Steps in execution.**


### Quality gates

#### RG · Resume Grader

**In one line.** LLM task 4 — grades a customized resume against the JD with realistic recruiter framing.

**What it does.** Reads the grading protocol, resume, and JD. Grades in a single pass, writes JSON to .grading/, renames the resume file from [TBD] to [score], and appends to .grades.log. Be realistically harsh — as harsh as a recruiter taking 30 seconds to skim.

**How it's built.** grader-model via `devin -p` (automated) or `util/agent-profiles/resume-grader.md` (debug). Prompt: `build_resume_grading_prompt()`. Protocol: `_config/grading-protocol.md`. Uses `mv` to rename and `echo >> .grades.log` to append.

**Steps in execution.**

1. **Read protocol** — Load grading rubric from <code>_config/grading-protocol.md</code>.
2. **Read resume + JD** — Load the customized resume and the job description.
3. **Grade** — Single pass: extract requirements → assign verdicts → compute score.
4. **Write JSON** — Write grade to <code>.grading/&lt;slug&gt;/grade-vN.json</code>.
5. **Rename** — <code>mv [TBD] resume-vN.md [score] resume-vN.md</code>
6. **Log** — <code>echo "..." >> .grades.log</code>

#### GL · Grades Log

**In one line.** An append-only audit trail of every grade ever assigned.

**What it does.** Every JD grade and resume grade is appended here with timestamp, file path, score, model, and hit/gap counts. Tracked in git — a permanent record.

**How it's built.** `.grades.log`. Appended by the resume grader via `echo >> .grades.log`. Format: `timestamp | path | grade=N | model=X | hits=N gaps=N`.

**Steps in execution.**


#### TV · Truthfulness Reviewer

**In one line.** LLM task 5 — verifies every resume claim against the base resume and LinkedIn.

**What it does.** Reads the veracity protocol, customized resume, base resume, and full career history. Classifies every claim into one of 4 buckets. If all claims verified → replies VERIFIED. If not → replies UNVERIFIED with specific claims to fix. Mandatory before moving to stages/4_ready/.

**How it's built.** grader-model via `devin -p` (automated) or `util/agent-profiles/truthfulness-reviewer.md` (debug). Protocol: `_config/veracity-protocol.md`. Writes JSON to `.veracity/<slug>/verification.json`.

**Steps in execution.**

1. **Read protocol** — Load veracity protocol from <code>_config/veracity-protocol.md</code>.
2. **Read resume** — Load the customized resume.
3. **Read sources** — Load base resume + full career history.
4. **Classify** — Per-claim: verified in base, verified in LinkedIn, partial, or unverified.
5. **Synthesize** — All verified → VERIFIED. Any unverified → UNVERIFIED + list.
6. **Write JSON** — Write result to <code>.veracity/&lt;slug&gt;/verification.json</code>

#### HK · Hooks

**In one line.** Integrity enforcement — hard guarantees that agents can't tamper with scores or run unapproved commands.

**What it does.** Three PreToolUse hooks and one Stop hook that enforce the pipeline's integrity rules: no score tampering, no unapproved exec commands, no completion with failing grades.

**How it's built.** `.devin/hooks.v1.json` → `util/hooks/`. `block_score_tamper.py` (PreToolUse: blocks renaming/overwriting graded resumes). `restrict_exec.py` (PreToolUse: exec whitelist, pipeline mode only — tier 1: count_lines/pytest/mv/echo >> .grades.log; tier 2: git/gh/ls/cd/cat/head/tail/wc/pwd/diff/grep/find/which/file/echo/cp; chaining allowed if all whitelisted; debug mode allows all). `block_failing_grade.py` (Stop: blocks completion with ungraded/below-threshold resumes). `audit_log.py` (PostToolUse: logs file writes).

**Steps in execution.**

1. **PreToolUse: exec** — Check command against two-tier whitelist (pipeline mode only). Block if not whitelisted.
2. **PreToolUse: edit/write** — Block writes to [score] resume files.
3. **PostToolUse** — Log file writes to pipeline files.
4. **Stop** — Block if ungraded or <9 resumes remain in stages/2_drafts/.

**Questions.**

- ~~**Q-HK1** How does the user know to remove the exec hook before debugging?~~ ✓ Documented in the customizer fix plan and ADR-0002. Debug mode is manual — the user removes the hook from hooks.v1.json (2026-08-24).

### Output

#### RD · Ready

**In one line.** Passing resumes awaiting manual application.

**What it does.** A resume moves here only if grade ≥ 9 AND truthfulness verified. The entire folder (JD + resume) is moved from stages/2_drafts/ to stages/4_ready/. The user reviews and submits manually.

**How it's built.** Filesystem: `stages/4_stages/4_ready/ready/<lt;company-role>/`. Moved by `step10_ready_node()` in `pipeline/steps/step10_finalize.py`.

**Steps in execution.**


#### NO · Notifications

**In one line.** Pushover alerts for run summaries and high-priority events.

**What it does.** Sends push notifications at the end of each pipeline run: summary of what was processed, and high-priority alerts for ready resumes or errors.

**How it's built.** `pipeline/infrastructure/notify.py`. Pushover adapter. No-ops without credentials. Config in `.devin/pipeline.env` (gitignored).

**Steps in execution.**

1. **Summary** — Send run summary (jobs processed, grades, ready count).
2. **Alert** — High-priority push for ready resumes or pipeline errors.

### Not yet switched on

#### FF · Form Filler _(not switched on)_

**In one line.** Deferred — will fill out job application forms up to the last page.

**What it does.** A future agent that navigates to the job posting, fills in application fields, and stops at the last page. The user reviews and submits manually. Not yet implemented.

**How it's built.** Planned: `stages/3_in-progress/in-progress/<lt;company-role>/`. Semi-automated co-work with the user.

**Steps in execution.**

1. **Navigate** — Go to job posting URL.
2. **Fill** — Fill in application fields.
3. **Stop** — Get to last page and STOP — do not submit.

**Questions.**

- **Q-FF1** When will form-filling be implemented?

#### SU · Submission _(not switched on)_

**In one line.** Deferred — the user's manual record of submitted applications.

**What it does.** After the user reviews and submits an application, they record it here. The agent never writes to this folder — it is the user's manual record only.

**How it's built.** Planned: `stages/5_submitted/submitted/<lt;company-role>/`. Manual step — agent never writes here.

**Steps in execution.**


## Flows (representative packets)

Payload shapes are what the design implies, not measured traffic.

### Job ingestion

| # | From → To | Packet | Representative payload |
|---|---|---|---|
| 1 | JS → OR | raw jobs | `{"source":"all_jobs.json","count":2453}` |
| 2 | OR → FC | unchecked jobs | `{"batch_size":10}` |
| 3 | FC → ES | feasibility verdicts | `{"tier":"preferred\|yes\|no"}` |
| 4 | OR → JF | feasible jobs | `{"filter":"feasible=true"}` |
| 5 | JF → ES | JD descriptions | `{"source":"LinkedIn","backoff":"30s×2^n"}` |

### Discovery + clearance

| # | From → To | Packet | Representative payload |
|---|---|---|---|
| 1 | OR → LI | new feasible job | `{"url":"linkedin.com/...","jd_text":"..."}` |
| 2 | JG → CF | CLEARANCE? | `{"check":"LLM detects clearance"}` |
| 3 | CF → TR | clearance required | `{"reason":"TS/SCI detected by LLM"}` |
| 4 | LI → JG | cleared | `{"folder":"stages/1_listings/<slug>/","file":"[TBD] job-description.md"}` |

### JD grading + triage

| # | From → To | Packet | Representative payload |
|---|---|---|---|
| 1 | OR → JG | JD text | `{"slug":"google-director-of-engineering"}` |
| 2 | JG → BR | read profile | `{"file":"base-resume.md"}` |
| 3 | JG → LE | read LinkedIn | `{"file":"full-experience.md"}` |
| 4 | JG → OR | GRADE: 8.5 | `{"grade":8.5,"justification":"..."}` |
| 5 | OR → TL | grade | `{"score":8.5}` |
| 6 | TL → DR | ≥8 → drafts | `{"action":"move_dir"}` |

### Resume customization

| # | From → To | Packet | Representative payload |
|---|---|---|---|
| 1 | OR → CU | customize prompt | `{"slug":"...","jd_text":"..."}` |
| 2 | CU → BR | read base | `{"file":"base-resume.md"}` |
| 3 | CU → LE | read LinkedIn | `{"file":"full-experience.md"}` |
| 4 | CU → CG | read guide | `{"file":"resume-customization-protocol.md"}` |
| 5 | CU → DR | write resume | `{"file":"[TBD] resume-v1.md"}` |
| 6 | CU → CL | measure | `{"cmd":"python3 -m pipeline.helpers.count_lines --json"}` |
| 7 | CL → CU | line count | `{"total":72,"ceiling":75}` |
| 8 | CU → DR | rewrite (trimmed) | `{"file":"[TBD] resume-v1.md","lines":70}` |

### Resume grading

| # | From → To | Packet | Representative payload |
|---|---|---|---|
| 1 | OR → RG | grade prompt | `{"slug":"...","version":"v1"}` |
| 2 | RG → DR | rename | `{"from":"[TBD] resume-v1.md","to":"[9.0] resume-v1.md"}` |
| 3 | RG → GL | append log | `{"entry":"timestamp \| ... \| grade=9.0 \| model=grader-model"}` |

### Truthfulness + ready

| # | From → To | Packet | Representative payload |
|---|---|---|---|
| 1 | OR → TV | verify prompt | `{"slug":"..."}` |
| 2 | TV → BR | read base | `{"file":"base-resume.md"}` |
| 3 | TV → LE | read LinkedIn | `{"file":"full-experience.md"}` |
| 4 | TV → OR | VERIFIED | `{"result":"all claims verified"}` |
| 5 | OR → RD | move to ready | `{"action":"move_dir stages/2_drafts/ → stages/4_ready/"}` |
| 6 | OR → NO | notification | `{"type":"high-priority","message":"resume ready"}` |

## Questions — index

Reference by ID. ✓ resolved (with date) · otherwise open.

- ~~**Q-OR1**~~ (OR) ✓ No — sequential is safer and cost is bounded by max_optimization_iterations (2026-08-24).
- ~~**Q-CG1**~~ (CG) ✓ Yes — agreed during grilling session (2026-08-24). Deferred to a separate task.
- ~~**Q-CU1**~~ (CU) ✓ Accept and proceed — the grader is the quality gate, not the line count (2026-08-24).
- ~~**Q-HK1**~~ (HK) ✓ Documented in the customizer fix plan and ADR-0002. Debug mode is manual — the user removes the hook from hooks.v1.json (2026-08-24).
- **Q-FF1** (FF) When will form-filling be implemented?

## What the platform gives vs what we own

**Platform gives:** Devin CLI (<code>devin -p</code>) for LLM invocation in non-interactive mode. Custom subagent profiles for debug mode. PreToolUse/Stop hooks for integrity enforcement. Systemd user timers for scheduling.

**We own:** The orchestrator (<code>pipeline/__main__.py</code> + <code>pipeline/</code> package), all pipeline scripts, the enrichment sidecar, the customizer/grader/truthfulness prompts and profiles, the hooks, and all documentation.

## Planned filesystem

```
job-hunter/
  pipeline/           # pipeline package (infrastructure, steps, helpers)
  _config/          # grading/veracity protocols, glossary, conventions
  _config/_config/profile/   # base resume, full career history
  .devin/           # pipeline config, hooks, subagent profiles
  stages/1_listings/ # JDs awaiting grading
  stages/2_drafts/   # resumes in optimization loop
  stages/4_ready/    # passing resumes awaiting application
  stages/6_rejected/  # abandoned listings
  stages/7_trash/     # auto-rejected jobs
  data/             # enrichment sidecar (SQLite)
  docs/             # ADRs + this atlas
```

## How this file is maintained

Generated from `docs/atlas/data.mjs` by `node docs/atlas/build.mjs`, which also builds the interactive atlas (`atlas.html`). Edit the data file, rebuild, republish — never edit this file by hand.
