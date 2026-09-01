# Veracity Synthesis Protocol (Call 2)

> Read by the automated `devin -p` truthfulness Call 2 (synthesis). This
> protocol covers Steps 3-4: synthesize + output JSON. The classification
> (Steps 1-2) is produced by a separate call — see
> `_config/veracity-classification-protocol.md`. See ADR-0011 for the
> two-call decomposition rationale.

## Your Input

You receive the classification JSON from Call 1 as fixed, committed input.
This contains the `per_claim` list with each claim's bucket, source, and
reason. **You cannot change these classifications.** Your job is to
synthesize the final verification result from them.

## Thinking Constraint

This is a counting task, not reasoning. Count the MATERIAL_OVERSTATEMENT
and FABRICATED verdicts, collect them into a list, output JSON. If you
spend more than a few seconds on this, you are overthinking — the
classifications are fixed and the rule is simple: zero unverifiable =
verified, one or more = not verified. Do not deliberate, do not re-read
the claims, do not explore scenarios. Just count and output.

## Step 3: Synthesis (compute from committed verdicts)

Work **only** from the committed per-claim verdicts from Call 1.

- **Do NOT introduce new factors that weren't assessed in Call 1.** If you
  notice something new, it's too late — it should have been a claim in Call 1.
- Count the MATERIAL_OVERSTATEMENT and FABRICATED verdicts. These are your
  unverifiable claims.
- If there are zero unverifiable claims → `verified: true`.
- If there are one or more → `verified: false`.

## Step 4: Output JSON (to stdout)

Output ONLY valid JSON to **stdout**. No reasoning text, no explanations,
no markdown fences, no preamble — start with `{` and end with `}`. Nothing
else. Do NOT write any files — the orchestrator captures your stdout and
writes the verification file.

```json
{
  "verified": false,
  "unverifiable_claims": [
    {
      "claim": "Managed 100-person engineering organization",
      "location": "Experience > Oracle > bullet 5",
      "bucket": "MATERIAL_OVERSTATEMENT",
      "source_checked": "LinkedIn",
      "reason": "LinkedIn says 'led a team of 50 engineers' — the resume inflates this to 100."
    }
  ],
  "summary": "2 unverifiable claims found. The 100-person org claim inflates the LinkedIn figure (50→100). The PCI DSS skill is fabricated — not in either source."
}
```

If all claims are verified:

```json
{
  "verified": true,
  "unverifiable_claims": [],
  "summary": "All claims verified against base resume and LinkedIn experience."
}
```

**Field definitions:**
- `verified`: `true` if zero unverifiable claims, `false` otherwise
- `unverifiable_claims`: only claims classified as MATERIAL_OVERSTATEMENT or FABRICATED. TRACEABLE and MINOR_VARIATION claims are NOT listed here. Pass these through from Call 1's `per_claim` list.
- `claim`: the exact text of the claim from the resume
- `location`: where on the resume it appears (section > company > bullet N, or "Skills table")
- `bucket`: MATERIAL_OVERSTATEMENT or FABRICATED
- `source_checked`: which source(s) you checked ("base resume", "LinkedIn", or "both")
- `reason`: one sentence explaining why it's unverifiable, quoting what the source actually says
- `summary`: one paragraph summarizing the verification result

## What NOT to Do

- Do not re-classify claims from Call 1. They are fixed input.
- Do not introduce new factors during synthesis that weren't assessed in Call 1.
- Do not re-read source material during this step.
- Do not write any files. Output JSON to stdout only — the orchestrator
  handles all file operations from your stdout output (ADR-0010).
