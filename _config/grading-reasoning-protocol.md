# Grading Reasoning Protocol (Call 1)

> Read by the automated `devin -p` grader Call 1 (reasoning). This protocol
> covers Steps 1-2: extract requirements + assess each criterion. The scoring
> (Steps 3-4) is handled by a separate call — see
> `_config/grading-scoring-protocol.md`. See ADR-0011 for the two-call
> decomposition rationale.

## Evaluator Framing

Be realistically harsh — as harsh as a recruiter taking 30 seconds to skim
the resume. Would they immediately see the key qualifications, or would they
have to hunt for them? If they'd have to hunt, that's a gap.

## Process (single pass)

Extract requirements, assess them, and output JSON. Do not over-analyze
or debate edge cases — commit to your first reasonable decision. Do not
simulate scenarios, re-read material you've already read, or explore
hypotheticals. Aim for under 500 tokens of output.

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

## Output JSON (to stdout)

Output ONLY valid JSON to **stdout**. No reasoning text, no explanations,
no markdown fences, no preamble — start with `{` and end with `}`. Nothing
else. Do NOT write any files — the orchestrator captures your stdout and
passes it to Call 2 (scoring).

```json
{
  "per_criterion": [
    {"requirement": "...", "tier": "core", "assessment": "DIRECT_HIT", "comment": "specific resume evidence"}
  ]
}
```

**Field definitions:**
- `per_criterion`: list of criterion objects, one per requirement extracted
- `requirement`: the requirement text from the JD
- `tier`: "core" or "preferred"
- `assessment`: one of DIRECT_HIT, ADDRESSED, PARTIAL, GAP
- `comment`: one-sentence comment citing specific resume evidence

## Clearance Detection (ADR-0007)

If the JD requires security clearance, output `CLEARANCE` instead of the
reasoning JSON.

## Anti-Deliberation Rules

- Do not invent requirements from responsibilities or role descriptions.
- Do not override the employer's requirement tiering.
