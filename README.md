# job-hunter

![Tests](https://github.com/sahiltalwar88/job-hunter/actions/workflows/tests.yml/badge.svg)
![Secrets Scan](https://github.com/sahiltalwar88/job-hunter/actions/workflows/secrets-scan.yml/badge.svg)
![Dependabot](https://img.shields.io/badge/Dependabot-enabled-blue)
![License](https://img.shields.io/badge/license-GPLv3-blue)

AI agent workspace that automates job applications: pulls listings from [a job scraper](https://github.com/sahiltalwar88/job-scraper), grades them against your base resume (informed by your full career history), customizes your resume through an iterative grade-and-improve loop, and parks passing resumes for manual application.

Fully automated by a LangGraph-based pipeline that runs hourly via systemd. Form-filling + submission steps are deferred — the agent never submits applications for you.

## About this project

This repo is also a reference for two agent-oriented documentation methodologies:

- **[Interactive system atlas](https://github.com/inkboard/system-atlas)** — the architecture is visualized as an explorable isometric map. See `docs/atlas.html` (open in a browser) or `docs/SYSTEM.md` (text twin). The atlas is generated from `docs/atlas/data.mjs`.
- **[ICM (Interpretable Context Methodology)](https://github.com/RinDig/icm-architect)** — the workspace is organized as a folder-based context architecture that AI agents can walk. `IDENTITY.md` (Layer 0) maps the workspace; `CONTEXT.md` (Layer 1) routes tasks; `_config/` (Layer 3) holds protocols and conventions.

A companion **job-hunter skill** will also be released, providing a guided startup workflow for new users: configuring the scraper, setting up your profile, and running your first pipeline cycle.

## Quick Start

### Prerequisites

- Python 3.11+
- A supported LLM CLI: [Devin](https://devin.ai), [Codex](https://developers.openai.com/codex/cli), or [Claude Code](https://code.claude.com/docs/en/cli-usage) (see [LLM Provider](#llm-provider) below)
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
   - `llm_provider` — `"devin"`, `"codex"`, or `"claude"`
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

The pipeline uses an `LLM` protocol (defined in `pipeline/infrastructure/llm_interface.py`) as its effect boundary. `RealLLM` dispatches each call to the CLI selected by `config.json` → `llm_provider`:

| Provider | Config value | Non-interactive command | Permissions |
|----------|--------------|-------------------------|-------------|
| [Devin CLI](https://devin.ai) | `"devin"` (default) | `devin -p` | Existing Devin permission modes and customizer config |
| [Codex CLI](https://developers.openai.com/codex/cli) | `"codex"` | `codex exec` | Read-only sandbox for analysis; workspace-write sandbox for customization |
| [Claude Code](https://code.claude.com/docs/en/cli-usage) | `"claude"` | `claude -p` | Exact read/edit/count-line allowlists in `dontAsk` mode |

Install and authenticate the chosen CLI, set `llm_provider`, and set each entry under `models` to a model name that provider accepts. For any provider, use `"default"` to defer model selection to the CLI's own configuration:

```json
{
  "llm_provider": "codex",
  "models": {
    "customizer": "default",
    "grader": "default",
    "truthfulness": "default"
  }
}
```

You can override the configured provider for one run without editing the file:

```bash
python3 -m pipeline --llm-provider claude --dry-run
```

To add another provider, implement the `LLM` protocol—a callable that takes a prompt string and returns an `(output, error)` tuple—and add its adapter to `RealLLM._PROVIDER_MODULES`. See `FakeLLM` for a minimal implementation.

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

## Standalone Resume Grading

Grade a single resume against a single JD — without running the full pipeline or using your candidate profile files. Useful for evaluating someone else's resume against a job, or quickly checking how a specific resume matches a specific JD.

### Usage

1. Put a resume file and a JD file (PDF or Markdown) in a folder:
   ```
   my-folder/
   ├── resume.pdf
   └── job-description.pdf
   ```

2. Run the command:
   ```bash
   python3 -m pipeline.helpers.grade_resume my-folder
   ```

The tool identifies which file is the resume and which is the JD by filename keywords ("resume"/"cv" → resume, "jd"/"job"/"description" → JD). It extracts text from both files, grades the resume against the JD using the same two-call grading protocol as the pipeline's step 7, and prints a score with per-criterion feedback.

**Options:**
- `--model <name>` — override the grader model (default: from `config.json`)
- `--timeout <seconds>` — override the LLM timeout
- `--json` — output raw grade JSON instead of a formatted summary

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
│   │   ├── *_cli.py           #   Devin, Codex, and Claude CLI adapters
│   ├── steps/                #   Step nodes (step1 through step10)
│   └── helpers/              #   Standalone CLI tools (count_lines, fetch_jds, grade_resume, etc.)
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
| `llm_provider` | `"devin"` | CLI adapter: `"devin"`, `"codex"`, or `"claude"` |
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

## Observability

The pipeline supports optional LLM observability via [Arize Phoenix](https://github.com/Arize-ai/phoenix)
(ADR-0014). Phoenix provides a local web UI for tracing prompts, responses,
token costs, and latencies across the LangGraph pipeline — no cloud account
or Docker required.

### Setup

```bash
# 1. Install the optional observability dependencies
pip install -r requirements-observability.txt

# 2. Start the Phoenix server (runs at localhost:6006)
python -m phoenix serve

# 3. Enable tracing in the pipeline
export PHOENIX_HOST=http://localhost:6006
```

### How it works

- **Auto-instrumentation**: `openinference-instrumentation-langchain` traces
  LangGraph nodes and LLM calls automatically — no code changes needed.
- **Manual spans**: Each `RealLLM.__call__` creates a span with `job_slug`
  and `step` attributes, so you can filter traces by job in the Phoenix UI.
- **Structured logs**: All log output is JSON (via structlog) with `slug`
  and `node` correlation keys, matching the Phoenix trace identity.

### Without Phoenix

The pipeline runs identically with or without Phoenix. If
`arize-phoenix` is not installed or `PHOENIX_HOST` is unset, tracing is
silently skipped. No configuration changes are required to run without it.

## License

GPL v3. See [LICENSE](LICENSE).
