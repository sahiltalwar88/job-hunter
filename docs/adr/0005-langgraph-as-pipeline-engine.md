# LangGraph as pipeline engine

**Status:** accepted

The pipeline orchestrator (`scripts/run_pipeline.py`) is refactored from an
imperative 1500-line script into a LangGraph stateful graph. The per-job
pipeline (ingest → ready) is modeled as a LangGraph `StateGraph` with
conditional edges and a cycle for the optimize loop. The prep phase
(feasibility, fetch, discover) remains plain functions — it's linear,
idempotent, and doesn't benefit from graph machinery.

## Considered Options

- **Hand-rolled `pipe()` combinator:** A custom `pipe(step1, step2, ...)`
  over a `PipelineRun` context, where each step is `Ctx -> StepResult` folded
  into the context. No new dependencies, full control. The optimize cycle and
  conditional triage routing would be hand-built with `if/else` and loop
  constructs — workable but reimplementing what LangGraph provides natively.
  Resumability stays a manual state-file affair. Rejected because the optimize
  cycle is a real graph cycle, triage is a real conditional edge, and the
  deferred Stage 13-16 (form-fill → stop → user submits) is a real
  human-in-the-loop interrupt — all three are LangGraph's core features.

- **LangGraph (chosen):** State is a Pydantic `JobState` model. Nodes are
  functions `(state, config) -> partial_dict` that LangGraph merges into state.
  Conditional edges handle triage routing (trash / rejected / drafts) and
  optimize-cycle routing (continue / done). `SqliteSaver` checkpointer at
  `data/per-job-pipeline-checkpoints.db` provides per-job resumability — an
  interrupted hourly run resumes the job from its last checkpoint instead of
  restarting. The deferred Stage 13-16 HITL submit maps directly to
  LangGraph's `interrupt()` primitive when implemented.

- **Pipe now, LangGraph-ready (deferred):** Build hand-rolled `pipe()` now
  with step functions designed to be LangGraph-node-compatible
  (`State -> Partial[State]`), swap to LangGraph when building Stage 13-16.
  Rejected because it defers the dependency decision without saving
  meaningful work — the step functions are the same either way, and the graph
  topology (edges, conditionals, cycles) is the part that's hard to retrofit.

## Consequences

- **New dependency:** `langgraph>=1.2.11,<2` (published 2026-08-11, MIT
  license). Pulls `langchain-core`, `pydantic>=2.7.4`, `langgraph-checkpoint`,
  `langgraph-prebuilt`, `langgraph-sdk`, `xxhash` as transitive deps. Added to
  `requirements.txt`. LangGraph can be used without LangChain — nodes call
  our existing `call_llm_safe` directly, no LangChain model abstraction.

- **Two-graph model:** Prep phase (feasibility → fetch → discover) is plain
  functions returning `list[dict]`. Per-job phase is a LangGraph `StateGraph`
  invoked once per new job. The orchestrator calls prep functions, gets the
  new jobs list, then invokes the per-job graph for each. This keeps the graph
  focused on the cyclic/conditional per-job pipeline where checkpointing
  matters (LLM calls are expensive, optimize cycles are long).

- **Checkpointer replaces manual state for per-job resumability:**
  `data/per-job-pipeline-checkpoints.db` (gitignored, regenerable) stores
  per-job checkpoints. `.devin/pipeline-state.json` continues to track
  batch-level metadata (last scraper SHA, last run timestamp, run stats).

- **Entry point changes:** New `scripts/pipeline_cli.py` replaces
  `scripts/run_pipeline.py` as the systemd entry point. `run_pipeline.py`
  becomes a deprecated alias that prints a warning and delegates. The systemd
  unit (`job-hunter-pipeline.service`) is updated to point at the new file.

- **`--step` debugging calls node functions directly:** For debugging, node
  functions are called directly (bypassing the graph) with a `JobState`
  constructed from existing files. This gives fast iteration without graph
  overhead or checkpoint DB writes. The node function is the exact same
  function the graph calls — no behavioral difference.

- **Future HITL for Stage 13-16:** When application form-filling is
  implemented, LangGraph's `interrupt()` primitive provides the "fill form →
  stop → user submits" pattern natively. The per-job graph extends with new
  nodes after `step10_finalize`, and the interrupt sits between form-fill and
  submit.
