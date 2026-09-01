# job-hunter

AI agent workspace that automates job applications: pulls listings from a job scraper, grades them against your base resume (informed by your full career history), customizes your resume through an iterative grade-and-improve loop, and parks passing resumes for manual application.

Fully automated by a LangGraph-based pipeline that runs hourly via systemd. Form-filling + submission steps are deferred — the agent never submits applications for you.

## Quick Start

### Prerequisites

- Python 3.11+
- An LLM provider (the reference implementation uses the [Devin CLI](https://devin.ai); see [LLM Provider](#llm-provider) below)
- A job scraper producing `all_jobs.json` (see [Scraper Integration](#scraper-integration) below)
- Optional: [Pushover](https://pushover.net) account for notifications

### Setup

1. **Clone and configure:**
   ```bash
   git clone <repo-url> job-hunter
   cd job-hunter
   cp config.example.json config.json
   cp -r .devin.example .devin
   ```

2. **Edit `config.json`:**
   - `candidate_name` — your name
   - `models` — LLM model names for each task (customizer, grader, truthfulness)
   - `scraper_transport` — `"filesystem"` (default) or `"http"`
   - `scraper_repo_path` — path to your scraper repo (if filesystem)
   - `scraper_url` — URL to scraper output (if http)
   - `feasibility_prompt` — customize to match your target roles and companies
   - `few_shot_examples` — filenames of your example customized resumes

3. **Add your profile:**
   ```bash
   # Replace the synthetic sample data with your own
   echo "# Your Name\n\nYour resume..." > _config/profile/base-resume/base-resume.md
   echo "# Your full career history..." > _config/profile/full-experience/full-experience.md
   ```

4. **Add example resumes:**
   Place 3-4 customized resumes in `docs/examples/customized-resumes/`. List their filenames in `config.json` → `few_shot_examples`. See `docs/examples/customized-resumes/README.md` for guidance.

5. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   pip install -r tests/requirements-dev.txt  # for running tests
   ```

6. **One-time job sync:**
   ```bash
   python3 -m pipeline.helpers.list_feasible_jobs --summary  # syncs from all_jobs.json into data/jobs.db
   ```

7. **Run the pipeline:**
   ```bash
   python3 -m pipeline --dry-run  # process but don't move files or commit
   python3 -m pipeline            # full run
   ```

### Optional: Hourly automation via systemd

```bash
# Create a systemd user timer (example)
systemctl --user start job-hunter-pipeline.timer
systemctl --user enable job-hunter-pipeline.timer
```

## LLM Provider

The pipeline uses an `LLM` protocol (defined in `pipeline/infrastructure/llm_interface.py`) as its effect boundary. The reference implementation wraps the [Devin CLI](https://devin.ai) (`pipeline/infrastructure/devin_cli.py` → `RealLLM`).

**Using Devin CLI:** Install it, and the pipeline works out of the box. Configure model names in `config.json` → `models`.

**Using another LLM provider:** Implement the `LLM` protocol — a callable that takes a prompt string and returns a response string. See `pipeline/infrastructure/llm_interface.py` for the protocol definition and `FakeLLM` as a reference implementation. Point `RealLLM` at your adapter or replace it in the dependency injection.

## Scraper Integration

job-hunter reads job data from an external scraper. The data contract is documented in the scraper's `docs/JOB_SCHEMA.md`. Key requirements:

- The scraper produces `all_jobs.json` — a JSON array of job records with `url`, `title`, `company`, `location`, `ats`, `first_seen`, and optionally `description`.
- Delta files (`output/deltas/index.jsonl`) provide incremental updates.
- Transport: filesystem (co-located scraper) or HTTP (GitHub Pages / raw.githubusercontent.com).

Configure via `config.json`:
- `scraper_transport: "filesystem"` — set `scraper_repo_path` to the scraper's root directory.
- `scraper_transport: "http"` — set `scraper_url` to the base URL serving the scraper's `output/` directory.

**One-time setup:** Full sync from `all_jobs.json`. **Ongoing:** Delta-only — on delta failure, the pipeline logs a loud error and does NOT fall back to full sync.

## How It Works

The pipeline runs 13 active steps (4 deferred):

1. **Feasibility check** — LLM tags each job as preferred/yes/no based on your configured prompt.
2. **Fetch JDs** — Pulls job descriptions for feasible jobs.
3. **Discover** — Finds new jobs not yet processed (dedup by URL).
4. **Ingest** — Creates `stages/1_listings/<company-role>/[TBD] job-description.md`.
5. **Grade JD** — LLM grades the JD against your base resume (informed by full experience). Detects clearance requirements.
6-7. **Triage** — Grade < 6 → trash. Grade 6-7.9 → rejected. Grade ≥ 8 → customize.
8. **Customize** — LLM tailors your base resume for the JD, pulling from your full experience. Self-measures line count, self-trims.
9-10. **Grade + optimize loop** — Grades the resume, iterates until no further truthful improvement is possible.
11. **Threshold gate** — Grade ≥ 9 → proceed. Grade < 9 → rejected.
12. **Truthfulness review** — LLM verifies every claim against your full career history.
13. **Ready** — Passing resume + JD moved to `stages/4_ready/<company-role>/`.

14-17. *(Deferred)* Application form-filling + submission — the agent fills forms but stops before submitting. You review and submit manually.

## Architecture

- **LangGraph StateGraph** for per-job processing (ADR-0005) with SqliteSaver checkpointer for resumability.
- **Pydantic state schema** (ADR-0006) for validation at the LLM-JSON boundary.
- **LLM-only clearance detection** (ADR-0007) — no regex filter.
- **Filesystem as state machine** — each pipeline stage maps to a folder (`stages/1_listings/` → `stages/2_drafts/` → `stages/4_ready/`).
- **Top-level pipeline package** (ADR-0013) — `pipeline/` with `infrastructure/`, `steps/`, `helpers/` subpackages.

See `docs/adr/` for all architecture decisions, and `docs/atlas.html` for an interactive architecture visualization.

## Repository Structure

```
job-hunter/
├── pipeline/                 # Pipeline package (LangGraph-based)
│   ├── __main__.py           #   Entry point: `python3 -m pipeline`
│   ├── infrastructure/       #   Core modules (config, state, paths, graph, LLM, etc.)
│   ├── steps/                #   Step nodes (step1 through step10)
│   └── helpers/              #   Standalone CLI tools (count_lines, fetch_jds, etc.)
├── stages/                   # Workflow state directories (filesystem as state machine)
│   ├── 1_listings/           #   JDs awaiting grading
│   ├── 2_drafts/             #   Resumes in optimization loop
│   ├── 3_in-progress/        #   (Deferred) Application form-filling
│   ├── 4_ready/              #   Passing resumes awaiting application
│   ├── 5_submitted/          #   (Deferred) User-submitted applications
│   ├── 6_rejected/           #   Abandoned listings
│   └── 7_trash/              #   Auto-rejected jobs
├── util/                     # Tooling
│   ├── hooks/                #   Devin lifecycle hooks
│   └── agent-profiles/       #   Subagent profiles for debug mode
├── _config/                  # Configuration + candidate data
│   ├── profile/              #   Base resume + full career history
│   └── *.md                  #   Protocols, conventions, glossary, voice
├── docs/                     # ADRs + atlas + examples
├── tests/                    # Root tests (integration + scraper contract)
└── .devin.example/           # Devin CLI config template (copy to .devin/)
```

## Interactive Mode

For working on a specific job interactively (instead of the automated pipeline), custom subagent profiles and lifecycle hooks provide the same integrity guarantees:

- `util/agent-profiles/customizer.md` — customizes resumes (self-measures, self-trims)
- `util/agent-profiles/resume-grader.md` — grades resumes
- `util/agent-profiles/truthfulness-reviewer.md` — verifies claims

Hooks in `util/hooks/` enforce integrity: block score tampering, exec whitelist (pipeline mode), audit logging, and block premature completion with failing grades.

See `CONTEXT.md` → "Interactive mode" for the full debug workflow.

## Configuration

All configuration lives in `config.json` (gitignored, copy from `config.example.json`). Key fields:

| Field | Default | Description |
|-------|---------|-------------|
| `candidate_name` | `"Squall Leonhart"` | Your name — used for PII scrubbing and prompts |
| `models.customizer` | `"customizer-model"` | LLM model for resume customization |
| `models.grader` | `"grader-model"` | LLM model for resume grading |
| `models.truthfulness` | `"grader-model"` | LLM model for truthfulness verification |
| `scraper_transport` | `"filesystem"` | `"filesystem"` or `"http"` |
| `scraper_repo_path` | `"/path/to/job-scraper"` | Scraper repo path (filesystem transport) |
| `scraper_url` | `""` | Scraper output URL (http transport) |
| `feasibility_prompt` | *(generic)* | LLM prompt for job feasibility tagging |
| `few_shot_examples` | `[]` | Filenames of example customized resumes |
| `jd_grade_threshold` | `8` | Minimum JD grade to proceed with customization |
| `resume_grade_threshold` | `9` | Minimum resume grade to move to ready |
| `max_optimization_iterations` | `3` | Max customize→grade iterations |

## Testing

```bash
python3 -m pytest                    # run all tests
python3 -m pytest pipeline/          # pipeline package tests only
```

## License

GPL v3. See [LICENSE](LICENSE).
