# LLM-only clearance check (drop regex)

**Status:** accepted

The deterministic regex-based clearance filter (`scripts/clearance_filter.py`)
is removed. Clearance detection is handled exclusively by the LLM during JD
grading (step4). The JD grading prompt instructs the LLM to return
`CLEARANCE` instead of a grade if the job requires security clearance.

## Considered Options

- **Keep regex as first-pass, LLM as safety net (current architecture):**
  `clearance_filter.py` does a regex check at ingestion (step3). If it
  passes, the LLM checks again during grading (step4) as a safety net for
  clearance requirements buried in the JD body that the regex missed. This is
  the current two-layer design. Rejected because the regex adds a module, a
  test file, and a step3 concern for marginal value — the LLM sees the full
  JD and does a simpler task than grading (binary: clearance vs. not). The
  regex is a second source of truth that can drift from the LLM's
  understanding of what constitutes a clearance requirement.

- **Drop regex, LLM-only (chosen):** The LLM is the sole clearance detector.
  The JD grading prompt already includes the clearance safety-net instruction.
  If the LLM returns `CLEARANCE`, the conditional edge after step4 routes to
  trash. No regex, no `clearance_filter.py`, no `test_clearance_filter.py`.
  The LLM is already being called for grading — clearance detection is a
  byproduct of that call, not an additional call.

- **Enhanced regex only (no LLM check):** Expand the regex pattern list to
  catch more clearance variants. Rejected because regex can't understand
  context — "clearance" in "the candidate will have clearance to make
  decisions" is not a security clearance. The LLM understands context.

## Consequences

- **`clearance_filter.py` and `test_clearance_filter.py` are deleted.** The
  regex module is dead code in the new architecture.

- **Clearance jobs briefly land in `listings/`:** Without the regex check at
  ingestion, a clearance-required job gets a `listings/<slug>/` folder + JD
  file before being trashed at step4 when the LLM detects clearance. If the
  pipeline crashes between step3 and step4, a clearance job sits in
  `listings/` with `[TBD]` until the next run catches it. Not a real problem
  — the next run re-grades it and trashes it.

- **Feasibility check still runs on clearance jobs:** Step1 (feasibility)
  runs before step4 (JD grading), so a clearance job gets a feasibility
  verdict in the enrichment sidecar before being trashed. This is one wasted
  batched LLM call. Negligible — feasibility is batched (5-10 jobs per call)
  and the verdict is cached.

- **The JD grading prompt is the single source of truth for clearance
  detection.** The prompt instruction: "If this JD requires security
  clearance (active clearance, clearance-eligible, TS/SCI, secret, ability
  to obtain, etc.), reply with just the word CLEARANCE — do not give a
  grade." This is already in the current prompt; no prompt change needed.

- **Simpler ingestion (step3):** The ingest node no longer calls
  `requires_clearance()`. It creates the listing folder unconditionally.
  Clearance is detected at step4 and routed to trash via conditional edge.
