# Grading Protocol

> Read by both the automated `devin -p` grader calls and the debug
> `resume-grader` subagent profile.
>
> **Note (ADR-0011):** The automated pipeline now uses a two-call
> decomposition — see `_config/grading-reasoning-protocol.md` (Call 1:
> extract + assess) and `_config/grading-scoring-protocol.md` (Call 2:
> compute + output). This file remains as the single-call reference for
> the debug `resume-grader` subagent profile, which still uses a single
> call in interactive debug sessions.

## Evaluator Framing

Be realistically harsh — as harsh as a recruiter taking 30 seconds to skim
the resume. Would they immediately see the key qualifications, or would they
have to hunt for them? If they'd have to hunt, that's a gap.

## Process (single pass)

Extract requirements, assess them, compute the score, and output JSON. Do
not over-analyze or debate edge cases — commit to your first reasonable
decision. Do not simulate scenarios, re-read material you've already read,
or explore hypotheticals. Aim for under 500 tokens of output.

### Step 1: Extract requirements

Extract requirements from the JD's **qualifications sections only**
("Requirements", "Qualifications", "Ideal Experience", "Basic
Qualifications", "Minimum Qualifications"). Do NOT extract from
"Key Responsibilities", "About the Role", or "What We Offer" — those are
job duties, not candidate requirements.

Classify each as **Core** or **Preferred** based on the employer's framing:
- Core: "required", "must have", "minimum", "essential", "Basic Qualifications"
- Preferred: "preferred", "nice to have", "bonus", "would be a plus"

If ambiguous, classify as Core. Maximum 10 requirements — pick the most
important if the JD has more.

### Step 2: Assess each criterion

For each requirement, assign one verdict with a one-sentence comment citing
specific resume evidence:

| Verdict | Points | Meaning |
|---------|--------|---------|
| DIRECT_HIT | 1.0 | Explicitly addressed with specific, quantified evidence. Recruiter sees it immediately. |
| ADDRESSED | 0.5 | Addressed but not as strongly. Evidence present but not prominent. |
| PARTIAL | 0.25 | Partially addressed. Some aspects covered, others missing. |
| GAP | 0.0 | Not addressed, or so weak a recruiter wouldn't count it. |

### Step 3: Compute score

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

### Step 4: Output JSON

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

## Threshold

**≥ 9** to proceed to truthfulness review. A 9 means every core requirement is
at least ADDRESSED with real evidence, preferred coverage is strong, and no
load-bearing gap drags the score below threshold.

## Clearance Detection (ADR-0007)

If the JD requires security clearance, output `CLEARANCE` instead of a grade.

## Anti-Deliberation Rules

- Do not invent requirements from responsibilities or role descriptions.
- Do not override the employer's requirement tiering.
