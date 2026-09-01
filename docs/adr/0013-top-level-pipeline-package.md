# Top-level pipeline package restructure

**Status:** accepted

Restructured the repository from a `scripts/`-based layout to a top-level
`pipeline/` Python package with clear separation between infrastructure, steps,
and helpers. Workflow data directories moved under `stages/` with numeric
prefixes. Tooling consolidated into `util/`.

## Context

The original layout grew organically: pipeline code lived in `scripts/pipeline/`,
standalone scripts in `scripts/`, hooks in `scripts/hooks/`, agent profiles in
`.devin/agents/`, and workflow data in top-level directories (`listings/`,
`drafts/`, `ready/`, etc.). This created several problems:

1. **sys.path hacks** — tests and scripts needed `sys.path.insert(0, "scripts/")`
   to import modules. Fragile and non-idiomatic.
2. **No package structure** — `python3 scripts/pipeline_cli.py` was the entry
   point, not `python3 -m pipeline`. Imports were flat (`from pipeline.config
   import ...`) with no hierarchy.
3. **Scattered tooling** — hooks, agent profiles, and utility scripts were
   spread across `scripts/`, `.devin/`, and root.
4. **Unordered stages** — `listings/`, `drafts/`, `ready/`, etc. sorted
   alphabetically, not in pipeline order.
5. **Profile mixed with config** — `profile/` sat at root alongside `_config/`,
   making it unclear which was configuration vs. candidate data.

## Considered Options

- **Keep scripts/ layout, just fix imports** — Add a proper `pyproject.toml`
  and `pip install -e .`. Rejected: adds packaging complexity for a single-user
  tool, and doesn't solve the scattered tooling or unordered stages problems.

- **Move to src/ layout** — Standard Python `src/pipeline/` layout. Rejected:
  overkill for this project, adds an unnecessary indirection layer, and makes
  `python3 -m pipeline` require a `pip install` step.

- **Top-level pipeline/ package (chosen)** — `pipeline/` at repo root with
  `infrastructure/`, `steps/`, and `helpers/` subpackages. `python3 -m pipeline`
  works out of the box. No `pyproject.toml` or `pip install` needed — the `-m`
  flag adds the current directory to `sys.path`.

## Decision

### Package structure

```
pipeline/
├── __init__.py
├── __main__.py              # entry point (was scripts/pipeline_cli.py)
├── infrastructure/          # core modules (config, state, paths, graph, etc.)
├── steps/                   # step nodes (step1 through step10)
├── helpers/                 # standalone CLI tools (count_lines, fetch_jds, etc.)
├── tests/                   # integration tests
└── conftest.py              # pipeline-level fixtures
```

All imports use full package paths: `from pipeline.infrastructure.config
import PipelineConfig`, `from pipeline.steps.step6_customize import ...`.

### Stage directories with numeric prefixes

```
stages/
├── 1_listings/              # Discovery + JD grading + triage
├── 2_drafts/                # Resume customization + review loops
├── 3_in-progress/           # (Deferred) Application form-filling
├── 4_ready/                 # Passing resumes awaiting application
├── 5_submitted/             # (Deferred) User-submitted applications
├── 6_rejected/              # Abandoned listings
└── 7_trash/                 # Auto-rejected jobs
```

Numeric prefixes (1-7) provide logical sort order. They are deliberately
distinct from `stepN_` code naming to avoid false step-to-stage mappings —
stages are states, not transformations, and multiple steps operate on the
same stage.

### Other moves

- `profile/` → `_config/profile/` — candidate data grouped with configuration.
- `examples/` → `docs/examples/` — sample resumes grouped with documentation.
- `scripts/hooks/` → `util/hooks/` — tooling separated from pipeline code.
- `.devin/agents/` → `util/agent-profiles/` — agent configs consolidated.
  `.devin/agents` is now a symlink to `../util/agent-profiles`.
- `customizer-permissions.json` → `agent-permissions.json` — not specific to
  the customizer.
- `step9_truthfulness.py` → `step9_veracity.py` — aligns with
  `veracity-protocol.md` naming.

### Config and entry point

- `config.json` at repo root (was `.devin/pipeline-config.json` in closed repo).
- `python3 -m pipeline` replaces `python3 scripts/pipeline_cli.py`.
- Root `conftest.py` provides shared fixtures and sys.path setup.
- No `pyproject.toml`, no `pip install -e .`.

## Consequences

- **Positive:** Idiomatic Python package structure. `python3 -m pipeline` works
  everywhere. No sys.path hacks. Tests co-located with code. Stages sort in
  pipeline order. Tooling is clearly separated from pipeline code.
- **Positive:** Both OSS and closed repos share identical structure (modulo
  private data and gjalla tooling), making synchronization straightforward.
- **Negative:** All documentation (IDENTITY.md, CONTEXT.md, glossary, atlas,
  README) required path updates.
- **Negative:** External references (systemd service, CI workflows) required
  updates.
