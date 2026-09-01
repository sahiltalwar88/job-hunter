# JD Grading Reasoning Protocol (Call 1)

> Read by the automated `devin -p` JD grader Call 1 (reasoning). This protocol
> covers Steps 1-2: extract requirements + assess each criterion against the
> candidate's full background. The scoring (Steps 3-4) is handled by a separate
> call — see `_config/jd-grading-scoring-protocol.md`.

## Evaluator Framing

You are evaluating whether this job is a good fit for the candidate — not
whether a specific resume is well-tailored. You have access to the candidate's
full background (base resume + LinkedIn). A requirement is a DIRECT_HIT if the
candidate's experience matches it anywhere in either source, even if it's not
on the base resume.

## Thinking Constraint

This is a mechanical extraction and assessment task. Extract requirements,
assess each against the candidate's background, output JSON. Do not
over-analyze or debate edge cases — commit to your first reasonable decision.
Do not simulate scenarios, re-read material you've already read, or explore
hypotheticals. Aim for under 500 tokens of output.

## Process (single pass)

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
specific evidence from the candidate's background (base resume or LinkedIn):

| Verdict | Points | Meaning |
|---------|--------|---------|
| DIRECT_HIT | 1.0 | Candidate's experience directly matches this requirement. Evidence is specific and quantified. Found in base resume or LinkedIn. |
| ADDRESSED | 0.5 | Candidate has relevant experience but it's not a direct match. Evidence present but not as strong. |
| PARTIAL | 0.25 | Candidate has some relevant experience but significant aspects are missing. |
| GAP | 0.0 | Candidate has no relevant experience in either source. |

**Key difference from resume grading:** You are evaluating the candidate's
full background, not a specific resume. A requirement is a DIRECT_HIT if the
experience appears anywhere in the base resume OR the LinkedIn — even if it's
not on the base resume alone. This is a more lenient assessment than resume
grading because you have access to the full career history.

## Output JSON (to stdout)

Output ONLY valid JSON to **stdout**. No reasoning text, no explanations,
no markdown fences, no preamble — start with `{` and end with `}`. Nothing
else. Do NOT write any files — the orchestrator captures your stdout and
passes it to Call 2 (scoring).

```json
{
  "per_criterion": [
    {"requirement": "...", "tier": "core", "assessment": "DIRECT_HIT", "comment": "specific evidence from base resume or LinkedIn"}
  ]
}
```

**Field definitions:**
- `per_criterion`: list of criterion objects, one per requirement extracted
- `requirement`: the requirement text from the JD
- `tier`: "core" or "preferred"
- `assessment`: one of DIRECT_HIT, ADDRESSED, PARTIAL, GAP
- `comment`: one-sentence comment citing specific evidence from the candidate's background

## Clearance Detection (ADR-0007)

If the JD requires security clearance, output `CLEARANCE` instead of the
reasoning JSON.

## Anti-Deliberation Rules

- Do not invent requirements from responsibilities or role descriptions.
- Do not override the employer's requirement tiering.
