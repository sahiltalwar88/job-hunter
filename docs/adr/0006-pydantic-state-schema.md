# Pydantic state schema

**Status:** accepted

The per-job pipeline state (`JobState`) and pipeline configuration
(`PipelineConfig`) are Pydantic `BaseModel` classes. All sub-models (grades,
criteria, verifications, resume versions) are also Pydantic models with
field constraints and enums for closed vocabularies.

## Considered Options

- **TypedDict:** Type hints only, zero runtime validation. At runtime it's a
  plain `dict` — nothing catches a bad value. Partial updates are just
  `return {"grades": {...}}` merged into a dict; if you return the wrong key
  type, it sails through and explodes somewhere downstream with an opaque
  `KeyError`. Rejected because the pipeline ingests untrusted structured
  output from LLMs (grade JSON, verification JSON), and TypedDict catches
  malformed LLM output never — the error surfaces 3 steps later as a
  `TypeError: string indices must be integers` with no indication which field
  was wrong.

- **Pydantic BaseModel (chosen):** Runtime validation at the LLM-JSON
  boundary. `grade: float = Field(ge=0, le=10)` catches a hallucinated
  `grade=11` at ingestion. `Assessment` enum catches `"gap"` (lowercase)
  instead of `"GAP"` — a real failure mode in the current codebase where
  `step_optimize_loop` does `c.get("assessment") == "GAP"` and silently
  misses lowercase values. JSON Schema is generated for free, useful for
  debugging and AI editors. Pydantic is already a transitive dependency via
  LangGraph (`pydantic>=2.7.4`), so no new dependency is introduced.

- **`@dataclass` (frozen):** Familiar, typed, no extra dep. But no validation,
  no JSON schema, no enum coercion. Would need hand-written validators for
  the LLM-JSON boundary — exactly what Pydantic gives for free. Rejected
  because the validation payoff is concentrated at the LLM ingestion
  boundary, which is the pipeline's most error-prone area.

## Consequences

- **Validation at the LLM-JSON boundary:** `ResumeGrade`, `JdGrade`, and
  `Verification` models validate LLM-produced JSON at ingestion time. A
  malformed grade JSON (missing `per_criterion`, wrong enum value, grade out
  of range) is caught immediately with a clear error message naming the
  field, not 3 steps later with an opaque exception.

- **Enums for closed vocabularies:** `FeasibilityTier` (preferred/yes/no),
  `Assessment` (DIRECT_HIT/ADDRESSED/PARTIAL/GAP), `TriageDestination`
  (trash/rejected_job_fit/drafts/ready/rejected_resume). These are
  de-facto enums currently scattered as string literals across the codebase.
  Pydantic makes them real and checked.

- **`PipelineConfig` is also Pydantic:** `config.json` is loaded into
  a typed `PipelineConfig` model with validation. A typo like
  `"max_optimization_iterations": "3"` (string instead of int) is caught at
  load time, not at iteration time.

- **Immutable updates via `model_copy`:** State transforms use
  `state.model_copy(update={...})` — Pydantic's immutable update. No in-place
  mutation of state objects. See ADR-0008 for the FP-style rich domain model
  pattern.

- **List field reducers:** `resume_versions: Annotated[list[ResumeVersion],
  operator.add]` — LangGraph concatenates when a node returns a partial
  update with a new version, instead of replacing the list. Nodes don't need
  to read existing state to append.

- **Node return type is partial dict:** Nodes return `{"jd_grade": grade}`,
  not a full `JobState`. LangGraph merges field-by-field and reconstructs the
  Pydantic model (which validates on merge). The type safety loss on the
  return value is acceptable because the state model validates on merge and
  tests catch mismatches.
