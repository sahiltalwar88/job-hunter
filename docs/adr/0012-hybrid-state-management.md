# Hybrid state management (filesystem + SQLite audit log)

**Status:** accepted

The pipeline continues to use filesystem directory moves as the primary state
mechanism (`listings/` → `drafts/` → `ready/` / `rejected/` / `trash/`), but
adds a SQLite `state_transitions` table to `data/jobs.db` that records every
state change as an auditable event. The filesystem remains the source of truth
for human readability; the SQLite table provides queryability and audit
history.

## Considered Options

- **Full SQLite centralization:** Add a `jobs` state table to `data/jobs.db`
  with status, history, and metadata per job. Filesystem moves become a
  consequence of state changes, not the state itself. The SQLite table is the
  source of truth; directory moves happen for human readability but are driven
  by the database. Rejected because it's a large refactor that touches every
  step node, and the filesystem-as-state approach has a real advantage: humans
  can see the state by `ls`-ing directories. Making SQLite the source of truth
  creates a synchronization problem — if the DB and filesystem disagree, which
  wins? The hybrid avoids this by keeping filesystem as the single source of
  truth and SQLite as a read-only audit log.

- **Hybrid: filesystem primary + SQLite audit log (chosen):** The filesystem
  remains the primary state mechanism. Every `move_dir()` call in the pipeline
  also writes a transition record to a `state_transitions` table in
  `data/jobs.db`. The table records: job slug, from-status, to-status,
  timestamp, reason, grade (if applicable), and metadata (JSON). A query
  helper can list transition history for any job. This adds queryability and
  auditability without changing the state mechanism or creating a sync problem.

- **Filesystem only + query helper:** Keep filesystem as the sole state
  mechanism. Add a `find`-based query helper that scans directories and
  reports status. No schema changes. Rejected because it doesn't fix the
  auditability problem — you can see the current state by scanning
  directories, but you can't see the history of how a job got there. The
  finding specifically calls out the inability to "query or audit at scale
  without scanning the entire workspace."

## Consequences

- **New `state_transitions` table in `data/jobs.db`.** Schema:
  `id INTEGER PRIMARY KEY, job_slug TEXT, from_status TEXT, to_status TEXT,
  timestamp TEXT, reason TEXT, grade REAL, metadata TEXT (JSON)`. The table is
  append-only — transitions are never updated or deleted.

- **`move_dir()` records transitions.** `scripts/pipeline/file_ops.py` gains
  an optional transition-recording hook, or the step nodes that call `move_dir()`
  (`step5_triage.py`, `step10_finalize.py`) record transitions directly. The
  transition is recorded after the move succeeds, so a failed move doesn't
  create a phantom transition.

- **Status vocabulary is explicit.** The `from_status` and `to_status` values
  are an enum: `discovered`, `listings`, `drafts`, `ready`, `rejected_job_fit`,
  `rejected_resume`, `trash`. This makes the state machine queryable and
  prevents typos.

- **Filesystem remains source of truth.** If the SQLite table is corrupted or
  deleted, the pipeline still works — it just loses audit history. The table
  is regenerable by scanning the filesystem (current state) though transition
  history would be lost. This is acceptable — the audit log is a convenience,
  not a critical path.

- **No change to step node logic.** Step nodes still call `move_dir()` to
  transition state. The only addition is a transition record write alongside
  the move. This keeps the refactor small and low-risk.

- **Query helper for audit history.** A function (in `enrichment_store.py` or
  a new `state_store.py`) can query transition history for a job slug, showing
  the full lifecycle: discovered → listings → drafts → ready. Useful for
  debugging and for the LLM audit logging (W7) to correlate LLM calls with
  state transitions.
