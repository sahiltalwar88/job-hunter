# Rich domain models, FP-style

**Status:** accepted

The pipeline's state models (`JobState`, `ResumeVersion`, etc.) are rich
domain models in the FP sense: pure data (Pydantic models) with co-located
free functions for construction and transformation, and pure query methods
for read-only computation. No mutation methods. This satisfies both the DDD
principle that models should handle operations concerning themselves and the
FP preference for immutability and free functions over methods.

## Considered Options

- **Anemic models + separate builder module:** `JobState` is pure data with
  no methods. A separate `state_builder.py` module contains
  `build_job_state_from_filesystem(slug, paths) -> JobState`. Rejected because
  this is the anemic domain model anti-pattern from DDD — the model doesn't
  know how to construct itself or answer questions about itself, forcing
  every consumer to know about external builder functions.

- **OOP-style rich models (methods for everything):** `JobState` has methods
  like `set_jd_grade(grade)`, `add_resume_version(version)`,
  `from_filesystem(slug)`. Rejected because mutation methods conflict with
  the immutable state model required by LangGraph (nodes return partial
  updates, not mutations) and with the FP preference for free functions and
  immutability.

- **FP-style rich models (chosen):** Three categories of operations, each
  placed according to FP principles:

  1. **Pure queries** (read state, return a value, no mutation) →
     methods/properties on the model. `latest_resume`, `is_passing`,
     `has_core_gaps`. These are fine in FP because they're pure — they don't
     mutate, just read.

  2. **Constructors** (build state from external sources) → free functions
     co-located in the same module. `build_job_state_from_filesystem(slug,
     paths) -> JobState`. The function lives in `state.py` alongside the
     model, not in a separate builder module.

  3. **Transforms** (produce new state from old) → free functions co-located
     in the same module. `with_jd_grade(state, grade) -> JobState` returns
     `state.model_copy(update={"jd_grade": grade})`. No mutation; each
     transform returns a new copy.

## Consequences

- **Co-location, not methods:** Functions that concern only `JobState` live
  in `state.py`, not in a separate `state_builder.py` or `state_ops.py`. The
  model and its operations are in one file. An agent or human editing
  `JobState`-related logic goes to one place.

- **No mutation methods:** No `state.set_jd_grade(grade)` or
  `state.add_resume_version(v)`. Transforms return new copies via
  `model_copy(update={...})`. This is compatible with LangGraph's merge
  semantics (nodes return partial dicts) and with the immutable-update
  pattern from FP.

- **Pure queries are methods:** `@property def latest_resume` and
  `@property def is_passing` are on the model because they're pure reads.
  This is the FP-acceptable form of methods — they don't mutate, they just
  compute from the model's data. They provide ergonomic access
  (`state.latest_resume` vs `get_latest_resume(state)`) without violating
  immutability.

- **`--step` debugging uses transforms:** When running a single step in
  isolation, the CLI constructs a `JobState` via
  `build_job_state_from_filesystem`, passes it to the node function, and
  uses `with_*` transforms to chain steps manually if needed. The same
  transforms are available for tests.

- **LangGraph compatibility:** LangGraph nodes return partial dicts
  (`{"jd_grade": grade}`), not transformed `JobState` objects. The `with_*`
  transforms are for `--step` mode and tests, not for node return values.
  Inside nodes, you return a dict; outside nodes (debugging, testing), you
  use transforms. Both paths operate on the same `JobState` model.
