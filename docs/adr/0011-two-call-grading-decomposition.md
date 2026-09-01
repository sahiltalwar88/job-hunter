# Two-call grading/truthfulness decomposition

**Status:** accepted

The grading and truthfulness protocols are decomposed from a single LLM call
into two chained calls. Call 1 produces structured reasoning; Call 2 takes
that reasoning as fixed input and produces the final score/verdict. The
orchestrator (step nodes) chains the calls, passing Call 1's output as context
to Call 2.

## Considered Options

- **Single call with multi-step prompt (current architecture):** The grading
  protocol instructs the LLM to follow a 4-step process (extract requirements →
  assess each → compute score → output JSON) in a single call. The
  truthfulness protocol similarly defines a 4-step process (load sources →
  classify claims → synthesize → output). Only the final JSON is verified —
  there is no mechanism to ensure the LLM actually performed the intermediate
  reasoning steps. A model could skip directly to a score, bypassing the
  system's "hard gates." Rejected because the integrity of the grading and
  truthfulness gates depends on the reasoning actually happening, not just the
  final output.

- **Two-call split (chosen):** Each protocol splits into two LLM calls:
  - **Grading:** Call 1 (reasoning) extracts requirements from the JD and
    assesses each against the resume, producing a structured JSON with the
    requirements list, per-criterion verdicts, and comments. Call 2 (scoring)
    takes Call 1's reasoning as fixed input and computes the final score using
    the protocol formula, producing the grade JSON.
  - **Truthfulness:** Call 1 (classification) loads sources and classifies each
    claim into one of 4 buckets, producing per-claim verdicts JSON. Call 2
    (synthesis) takes Call 1's verdicts as fixed input, counts unverifiable
    claims, and produces the verification JSON.

  The orchestrator captures Call 1's output and injects it as fixed context
  into Call 2's prompt. Call 2 cannot introduce new requirements or re-classify
  claims — it works only from what Call 1 produced.

- **Full chain (3+ calls):** Separate LLM call per reasoning step (extract →
  assess → score for grading; load → classify → synthesize for truthfulness).
  Most robust, but 3x the LLM calls per grade. Rejected as overkill — the
  two-call split already captures the key intermediate output (the reasoning)
  and enforces that it happened before the score is computed. The third call
  (separate "compute" from "output") adds latency and cost for marginal
  benefit.

- **Single call + reasoning verification:** Keep the single call but require
  the LLM to include intermediate reasoning in the JSON output, and have the
  orchestrator validate that the reasoning is present and non-trivial. No
  extra LLM calls. Rejected because it's weaker than the two-call split — the
  LLM could still generate reasoning post-hoc to justify a pre-chosen score.
  The two-call split makes this impossible because Call 2 must work from
  Call 1's committed output.

## Consequences

- **Doubles LLM calls for grading and truthfulness.** Each grade now requires
  2 LLM calls instead of 1. Each truthfulness review requires 2 instead of 1.
  This doubles the LLM cost and latency for these steps. Accepted as the price
  of integrity — the grading and truthfulness gates are the system's hard
  gates, and ensuring they actually perform their reasoning is worth the cost.

- **Protocol files split.** `_config/grading-protocol.md` splits into
  `_config/grading-reasoning-protocol.md` (Steps 1-2: extract + assess) and
  `_config/grading-scoring-protocol.md` (Steps 3-4: compute + output).
  `_config/veracity-protocol.md` splits similarly into classification and
  synthesis protocols.

- **Step nodes chain two calls.** `step7_grade_resume.py` and
  `step9_truthfulness.py` build two prompts each, make two sequential LLM
  calls, and pass Call 1's output into Call 2's prompt. Both calls use
  `normal` permission mode (per ADR-0010) — neither writes files.

- **`.grades.log` may log both calls.** The audit trail can record the
  reasoning model and scoring model separately (they may use different models
  — e.g., a stronger model for reasoning, a cheaper one for the deterministic
  scoring step).

- **FakeLLM tests need two responses.** Test doubles must return reasoning JSON
  for Call 1 and score JSON for Call 2. The FakeLLM's prompt-substring matching
  handles this naturally — different prompts get different responses.

- **Call 1 output is stored.** The reasoning JSON from Call 1 is persisted
  (in `.grading/{slug}/reasoning-vN.json` or in the SQLite audit table per W7)
  so the intermediate reasoning is auditable and can be reviewed if a grade is
  disputed.
