# Optimize cycle termination with LLM improvement check

**Status:** accepted

The optimize loop (step6 → step7 → step8 → back to step6 or forward to
step9) terminates when: (1) the grade is ≥ 9 AND no core gaps, (2) the
iteration count reaches `max_optimization_iterations`, OR (3) the LLM
confirms no further truthful improvement is possible. This adds an LLM
"can you improve?" check before routing back to customization, making the
loop faithful to `overall-approach.md` step 10's "keep iterating until no
further truthful improvement is possible" rule.

## Considered Options

- **Grade + gaps + iteration count only:** Continue if
  `grade < 9 OR has_core_gaps` AND `len(resume_versions) < max_iter`. Done
  otherwise. This is what the current `step_optimize_loop` does — it exits
  at ≥9 + no core gaps. Rejected because it deviates from
  `overall-approach.md` step 10, which says "keep iterating until no further
  truthful improvement is possible, regardless of grade." A resume at grade
  9.2 with no core gaps might still be truthfully improvable to 9.5.

- **Always iterate to max, keep best:** Route continues until `max_iter`
  regardless of grade. Step10 picks the best version. Faithful to
  overall-approach.md but wastes LLM calls when the grade is already 9+ with
  no gaps and the LLM has no improvements to suggest. Rejected for cost —
  each iteration is two LLM calls (customize + grade), and 3 iterations ×
  N jobs × hourly runs adds up.

- **Grade + gaps + iteration count + LLM "can you improve?" check (chosen):**
  Before routing back to customization, the optimize-decision node asks the
  LLM: "Given the current resume, the JD, and the grader's feedback, can you
  truthfully improve this resume further? Answer YES or NO." If NO, exit the
  loop even if under `max_iter`. If YES, continue. This is one additional LLM
  call per iteration — a cheap "can you improve?" call vs. the expensive
  customize + grade pair it potentially saves. Faithful to
  overall-approach.md step 10. If the extra call becomes a problem, it can
  be commented out and the loop falls back to grade + gaps + iteration count.

## Consequences

- **One extra LLM call per optimize iteration:** The "can you improve?"
  check is a short prompt (current resume + JD + grader feedback → YES/NO).
  It saves the two-call customize+grade pair when the answer is NO. Net cost
  is negative when the LLM correctly identifies no-improvement cases, and
  positive (one extra call) when it says YES and the loop continues. The
  prompt is simple enough that the cheapest model can handle it.

- **Deviation from current code behavior:** The current `step_optimize_loop`
  exits at ≥9 + no core gaps. The new loop continues past ≥9 if the LLM says
  improvement is possible. This is a behavior change — resumes that currently
  stop at 9.0 might continue to 9.3. This is intentional and aligns with
  `overall-approach.md` step 10.

- **`overall-approach.md` step 10 is now faithfully implemented.** The
  current code deviates from the documented approach. This ADR documents
  that the LangGraph refactor corrects the deviation. No change to
  `overall-approach.md` is needed — the code now matches the doc.

- **Fallback if the LLM call is problematic:** If the "can you improve?"
  call adds latency or cost that's unacceptable, comment out the check in
  the optimize-decision node. The loop falls back to grade + gaps +
  iteration count (the current behavior). This is a one-line change in the
  routing function.

- **The "can you improve?" prompt must enforce truthfulness:** The prompt
  must instruct the LLM to only say YES if it can identify a *truthful*
  improvement (sourcing from `profile/full-experience/` only). Without this,
  the LLM might suggest fabricated improvements, defeating the truthfulness
  guarantee. The prompt includes the same truthfulness constraint as the
  customization prompt.
