# Grading Scoring Protocol (Call 2)

> Read by the automated `devin -p` grader Call 2 (scoring). This protocol
> covers Steps 3-4: compute score + output JSON. The reasoning (Steps 1-2)
> is produced by a separate call — see
> `_config/grading-reasoning-protocol.md`. See ADR-0011 for the two-call
> decomposition rationale.

## Your Input

You receive the reasoning JSON from Call 1 as fixed, committed input. This
contains the `per_criterion` list with each requirement's tier, assessment
verdict, and comment. **You cannot change these verdicts.** Your job is to
compute the score from them.

## Thinking Constraint

This is arithmetic, not reasoning. Count the verdicts, apply the formula,
output the number. If you spend more than a few seconds on this, you are
overthinking — the formula is deterministic and the input is fixed. Do not
deliberate, do not explore scenarios, do not re-read the per-criterion
comments. Just compute.

## Step 3: Compute score

**Ceiling** (first match, hard cap):
- Any core GAP → ceiling = 8.0
- Any core PARTIAL → ceiling = 9.0
- Any preferred GAP → ceiling = 9.5
- No GAPs → ceiling = 10.0

**Core score** = `(sum of core points / num core reqs) × 10`

**Preferred bonus** = `sum of preferred points`, capped at +1.0
(DIRECT_HIT=0.2, ADDRESSED=0.1, PARTIAL=0.05, GAP=0)

**Quantitative base** = `core score + preferred bonus`, clamped to ceiling.

**Subjective adjustment** (deterministic — pick the most severe, do not deliberate):
- Any core GAP → -1.0
- Any core PARTIAL → -0.5
- Any preferred GAP → -0.2
- No GAPs at all → +0.3 (polish bonus for exceptional JD alignment)
- Apply only one. Do not combine.

**Final score** = `quantitative base + subjective adjustment`, clamped to ceiling.

## Step 4: Output JSON (to stdout)

Output ONLY valid JSON to **stdout**. No reasoning text, no explanations,
no markdown fences, no preamble — start with `{` and end with `}`. Nothing
else. Do NOT write any files — the orchestrator captures your stdout and
performs all file operations (writing the grade file, renaming the resume,
appending to `.grades.log`).

```json
{
  "grade": 9.3,
  "ceiling": 9.5,
  "core_score": 9.44,
  "preferred_bonus": 1.0,
  "quantitative_base": 9.5,
  "subjective_adjustment": -0.2,
  "per_criterion": [
    {"requirement": "...", "tier": "core", "assessment": "DIRECT_HIT", "comment": "specific resume evidence"}
  ],
  "subjective_justification": "one sentence — which verdict drives the adjustment",
  "justification": "one paragraph summary"
}
```

The `per_criterion` list must be passed through from Call 1's reasoning
JSON unchanged. You are adding the computed score fields around it.

## Threshold

**≥ 9** to proceed to truthfulness review. A 9 means every core requirement is
at least ADDRESSED with real evidence, preferred coverage is strong, and no
load-bearing gap drags the score below threshold.

## Anti-Deliberation Rules

- Do not deliberate on edge cases. Compute once and commit.
- Do not re-assess or revise verdicts from Call 1. They are fixed input.
- Do not simulate multiple scoring scenarios.
- Do not invent new requirements or re-classify existing ones.
