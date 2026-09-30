> **job-hunter** — AI agent workspace that automates job applications: pulls listings from a job scraper, grades them against your base resume (informed by your full career history), customizes your resume through an iterative grade-and-improve loop, and parks passing resumes for manual application. Fully automated by a LangGraph-based pipeline (`python3 -m pipeline`) that runs hourly via systemd. (Form-filling + submission steps are deferred — will be implemented as semi-automated co-work.)

## Workspace Map

```
job-hunter/
├── IDENTITY.md                  # Layer 0 — this file. Workspace map.
├── CONTEXT.md                   # Layer 1 — routing table + session protocol + workflow definition.
├── config.example.json          # Config template. Copy to config.json (gitignored) and edit.
├── config.json                  # Your config (gitignored). Centralized pipeline settings.
├── LICENSE                      # GPL v3.
├── requirements.txt             # Python dependencies.
├── _config/                     # Layer 3 — rules that apply to all work.
│   ├── profile/                 # Candidate reference material — always loaded, never consumed.
│   │   ├── base-resume/         # Generic resume — the customization BASE. The agent
│   │   │   └── base-resume.md   #   starts every customized resume from this template.
│   │   │                        #   Grades JDs against this (primary), informed by
│   │   │                        #   full-experience/ to catch experience not surfaced here.
│   │   └── full-experience/     # Full career history — the SOURCING reference.
│   │       └── full-experience.md  # Consulted during JD grading (to know what's
│   │                            #   available) and customization (to pull in experience
│   │                            #   not on the generic resume). Never used as a base.
│   │                            #   Truthfulness review verifies against both.
│   ├── conventions.md           # File naming, folder naming, format rules.
│   ├── glossary.md              # Domain terms (grade, JD, passing, etc.).
│   ├── grading-protocol.md      # Resume grading rubric (read by grader subagent).
│   ├── grading-reasoning-protocol.md
│   ├── grading-scoring-protocol.md
│   ├── jd-grading-reasoning-protocol.md
│   ├── jd-grading-scoring-protocol.md
│   ├── resume-customization-protocol.md  # Customization protocol (read by customizer subagent).
│   ├── veracity-protocol.md     # Truthfulness verification protocol.
│   ├── veracity-classification-protocol.md
│   ├── veracity-synthesis-protocol.md
│   └── voice.md                 # Tone, audience, output standards.
├── pipeline/                    # The pipeline package (LangGraph-based).
│   ├── __init__.py              #   Package docstring.
│   ├── __main__.py              #   Entry point (was scripts/pipeline_cli.py). Run via `python3 -m pipeline`.
│   │                            #   Supports --dry-run, --job, --step. Runs hourly via systemd timer.
│   ├── infrastructure/          #   Core modules the steps depend on.
│   │   ├── paths.py             #   Frozen Paths dataclass with all workspace paths.
│   │   ├── config.py            #   Pydantic PipelineConfig + load_config().
│   │   ├── state.py             #   Pydantic JobState + sub-models + enums + free functions.
│   │   ├── llm_interface.py     #   LLM Protocol + provider-dispatching RealLLM + FakeLLM + NodeDeps.
│   │   ├── file_ops.py          #   rename_with_grade, move_dir, company_role_slug, URL dedup.
│   │   ├── lifecycle.py         #   lock, git commit/push, scraper sync, logging, cleanup.
│   │   ├── graph.py             #   LangGraph builder: nodes, edges, conditional routing.
│   │   ├── audit_store.py       #   Append-only audit trail helpers.
│   │   ├── state_store.py       #   Per-job state persistence.
│   │   ├── job_schema.py        #   Scraper job record schema + validation.
│   │   ├── injection_filter.py  #   Prompt injection filtering.
│   │   ├── protocol_constants.py #  Shared protocol constants.
│   │   ├── enrichment_store.py  #   SQLite job store (scraper mirror + enrichments).
│   │   ├── delta_sync.py        #   Incremental scraper sync via delta files.
│   │   ├── devin_cli.py         #   LLM wrapper for `devin -p`. Reference implementation.
│   │   ├── codex_cli.py         #   LLM wrapper for non-interactive `codex exec`.
│   │   ├── claude_cli.py        #   LLM wrapper for non-interactive `claude -p`.
│   │   ├── llm_error.py         #   Shared provider subprocess error type.
│   │   ├── feasibility_checker.py # LLM-based feasibility checker.
│   │   ├── notify.py            #   Pushover notification adapter. No-ops without creds.
│   │   └── tests/               #   Co-located tests for infrastructure modules.
│   ├── steps/                   #   Pipeline step nodes (one per workflow stage).
│   │   ├── step1_feasibility.py #   Feasibility check (batch, plain function).
│   │   ├── step2_fetch_jds.py   #   Fetch missing JDs (batch, plain function).
│   │   ├── step3_discover.py    #   Discover + dedup (batch, plain function).
│   │   ├── step3_ingest.py      #   Create listing folder + JD file (per-job graph node).
│   │   ├── step4_grade_jd.py    #   Grade JD + LLM clearance detection (per-job node).
│   │   ├── step5_triage.py      #   Triage: trash/rejected/drafts (per-job node).
│   │   ├── step6_customize.py   #   Customize/revise resume + YES/NO signal (per-job node).
│   │   ├── step7_grade_resume.py#   Grade resume + rename + audit log (per-job node).
│   │   ├── step8_veracity.py    #   In-loop truthfulness review, per version (per-job node).
│   │   ├── step9_should_continue.py # Pure routing: continue or stop (per-job node).
│   │   ├── step10_final_veracity.py # Final truthfulness gate on the selected version (per-job node).
│   │   ├── step11_finalize.py   #   Prune non-selected, move to ready + terminal nodes.
│   │   └── tests/               #   Co-located tests for step nodes.
│   ├── helpers/                 #   Standalone CLI tools and utilities.
│   │   ├── count_lines.py       #   Measures rendered line count of a resume markdown file.
│   │   ├── fetch_jds.py         #   Fetches JDs for feasible jobs without descriptions.
│   │   ├── list_feasible_jobs.py#   Queries jobs with pagination (SQLite mirror).
│   │   ├── md_to_pdf.py         #   Converts resume markdown to PDF.
│   │   ├── pii_scrub.py         #   Scrubs PII before sending to external services.
│   │   ├── serve.py             #   Named serve shortcuts (npm-run style). Default: atlas.
│   │   ├── smoke_test_pipeline.py # End-to-end smoke test for the pipeline.
│   │   └── tests/               #   Co-located tests for helper tools.
│   ├── tests/                   #   Integration tests (multi-component).
│   └── conftest.py              #   Pipeline-level test fixtures.
├── stages/                      # Workflow state directories (filesystem as state machine).
│   ├── 1_listings/              #   Stage 2-6: Discovery + JD grading + triage.
│   │   └── <company-role>/      #   One folder per job listing.
│   │       └── [N] job-description.md   # [TBD] before grading, [score] after.
│   ├── 2_drafts/                #   Stage 7-11: Resume customization + review loops.
│   │   └── <company-role>/      #   One folder per active application.
│   │       └── [N] resume-vN.md #   [TBD] before grading, [score] after. Markdown only.
│   ├── 3_in-progress/           #   DEFERRED — Stage 13-15: Application form-filling.
│   ├── 4_ready/                 #   Stage 12: Passing resumes awaiting application.
│   │   └── <company-role>/      #   Moved here once grade ≥ 9 + truthful.
│   ├── 5_submitted/             #   DEFERRED — Stage 16: User-submitted applications.
│   ├── 6_rejected/              #   Abandoned listings. Two types, prefixed on folder:
│   │   ├── [JOB-FIT] <company-role>/  # JD grade 6–7.9 — weak overall fit.
│   │   └── [RESUME] <company-role>/   # Resume grade < 9 — couldn't optimize enough.
│   └── 7_trash/                 #   Auto-rejected jobs (JD grade < 6, or clearance-required).
│       └── <company-role>/      #   Skim periodically, then delete the contents.
├── util/                        # Tooling and configuration.
│   ├── hooks/                   #   Devin lifecycle hooks (debug mode enforcement).
│   │   ├── block_score_tamper.py    # PreToolUse: blocks renaming/overwriting graded resumes.
│   │   ├── restrict_exec.py         # PreToolUse: universal exec whitelist (pipeline mode only).
│   │   ├── audit_log.py             # PostToolUse: logs file writes to .grades.log.
│   │   ├── session_start.py         # SessionStart: injects debug workflow context.
│   │   ├── block_failing_grade.py   # Stop: blocks completion with ungraded/below-threshold resumes.
│   │   └── tests/                   # Hook tests.
│   └── agent-profiles/          #   Custom subagent profiles for debug mode (Devin CLI).
│       ├── customizer.md        #   Pinned to customizer model for resume customization.
│       ├── resume-grader.md     #   Pinned to grader model for resume grading.
│       ├── truthfulness-reviewer.md # Pinned to grader model for truthfulness verification.
│       └── agent-permissions.json   # Permissions config for subagents.
├── .devin.example/              # Shipped Devin CLI configuration template (tracked).
│   ├── hooks.v1.json            #   Lifecycle hooks config (references util/hooks/ paths).
│   ├── agents -> ../util/agent-profiles  # Symlink to agent profiles.
│   └── pipeline.env.example     #   Pushover credentials template (copy to pipeline.env).
├── docs/                        # ADRs + interactive system atlas + examples.
│   ├── adr/                     #   Architecture Decision Records.
│   ├── atlas/                   #   Atlas source (data.mjs, build.mjs, template.html).
│   ├── atlas.html               #   Interactive isometric atlas (generated).
│   ├── SYSTEM.md                #   Text twin of the atlas (generated).
│   └── examples/                #   Few-shot example resumes for the customizer.
│       └── customized-resumes/  #   Sample customized resumes. Filenames listed in config.json
│                                #   → few_shot_examples. Users replace with their own.
├── .grades.log                  # Append-only audit trail of every grade. (tracked)
├── data/                        # SQLite job store (gitignored, regenerable).
│   └── jobs.db                  # Two tables: jobs (scraper mirror) + enrichments.
├── tests/                       # Root tests (multi-package: integration + scraper contract).
│   ├── fixtures/                #   Test data (sample_all_jobs_with_feasibility_tags.json).
│   ├── test_integration_real.py #   Real-LLM integration tests (skipped without API keys).
│   └── test_scraper_to_hunter_pipeline_contract.py  # Scraper contract tests.
├── .github/workflows/
│   └── tests.yml                # CI — runs pytest on push/PR.
├── .gitignore
└── .gitleaks.toml
```

## Related repos

- **Job scraper** — a separate repo that scrapes job boards and produces `all_jobs.json`. job-hunter reads this data via a configurable path (`config.json` → `scraper_repo_path` or `scraper_url`). See the scraper's `docs/JOB_SCHEMA.md` for the data contract. The scraper is not included in this repo — users provide their own job source.

## Rules

1. **Never submit an application.** (Deferred — but still a hard rule when implemented.) The user always submits manually.
2. **Never edit files in `stages/5_submitted/`.** That's the user's manual record.
3. **Grade prefix is mandatory.** Every graded file is renamed `[score] filename`. Ungraded files use `[TBD] filename`.
4. **Auto-reject if JD grade < 6.** Move to `stages/7_trash/<company-role>/`. Periodically emptied.
5. **Auto-reject clearance-required positions.** If a JD requires security clearance, move to `stages/7_trash/<company-role>/` immediately. Clearance is detected by the LLM during JD grading (ADR-0007 — no regex filter).
6. **Reject if JD grade is 6–7.9.** Move to `stages/6_rejected/[JOB-FIT] <company-role>/`. Not worth customizing a resume.
7. **Proceed only if JD grade ≥ 8.** Resume customization starts at this threshold.
8. **Abandon if resume grade < 9.** If the resume can't reach 9 after the optimization loop, drop the listing. Not worth applying.
9. **Truthfulness is non-negotiable.** The resume must pass a truthfulness review (verified against `_config/profile/base-resume/` and `_config/profile/full-experience/`) before moving to `stages/4_ready/`. No exaggerations, no fabricated skills.
10. **Save outputs to the right pipeline folder.** Don't clutter the root. Each application gets its own `<company-role>/` subfolder under `stages/`.
11. **Dedupe by URL.** Before processing a job from the scraper, check if its URL already exists in any stage folder. Skip duplicates.
12. **Base vs. reference.** `_config/profile/base-resume/` is the only customization base — every tailored resume starts from the generic resume. `_config/profile/full-experience/` is a reference for grading and sourcing: it informs the JD grade and supplies experience that can be pulled onto a customized resume, but a customized resume is never built directly from it. Truthfulness review verifies against both.
13. **Run feasibility-check before processing.** The pipeline runs its own feasibility checker (`pipeline/infrastructure/feasibility_checker.py`) and stores verdicts in the SQLite store (`data/jobs.db`, enrichments table). Only `feasible: true` jobs enter the pipeline.
14. **Fetch JDs before grading.** Run `python3 -m pipeline.helpers.fetch_jds` to populate descriptions for feasible jobs. Descriptions are stored in the enrichment sidecar, not `all_jobs.json`.
15. **Never read `all_jobs.json` directly into context.** Use `python3 -m pipeline.helpers.list_feasible_jobs` to query the SQLite mirror with pagination.
16. **`_config/profile/` is read-only.** The base resume and full experience are immutable sources of truth. Never write to `_config/profile/`. The customization protocol lives in `_config/resume-customization-protocol.md`.
