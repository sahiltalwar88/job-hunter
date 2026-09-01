# Drop widow elimination from the customization pipeline

**Status:** proposed

Widow elimination — rephrasing bullets in the 103–115 and 205–217 character ranges to avoid 1–2 trailing words wrapping to a new line — is removed from the customization protocol entirely. The customizer no longer attempts it. `count_lines.py` reports widow flags as diagnostic information only; no agent or script acts on them.

## Considered Options

- **Keep widow elimination as a post-processing step:** Parent agent runs `count_lines.py`, identifies widows, and either fixes them manually or launches a "widow-fix" subagent. Rejected because it adds a second customization agent for a cosmetic problem, and the few-shot examples (the reference set) almost certainly contain widows — the reference material doesn't obey the rule.
- **Keep widow elimination in the guide but remove it from the customizer's active responsibilities:** Move it to a "post-customization" section. Rejected as half-measure — if nobody acts on it, it's dead documentation.
- **Drop entirely (chosen):** Widow elimination consumed the bulk of the Cognichip run's time budget for no measurable payoff. The downstream goal is a resume a recruiter reads in 30 seconds — widows don't affect that. The line budget (ADR-0003) is enforced via `count_lines.py`; widow flags remain as diagnostic output for manual review if desired.

## Consequences

- `count_lines.py` still computes widow flags (cheap, useful for manual review), but no automated action is taken on them.
- The customization protocol is simpler — one fewer rule for the customizer to worry about.
- If widow elimination becomes important later, it can be re-added as a post-processing step without changing the customizer's responsibilities.
