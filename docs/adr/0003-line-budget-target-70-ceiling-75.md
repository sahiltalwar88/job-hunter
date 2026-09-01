# Line budget: target 70, ceiling 75

**Status:** proposed

The customization protocol's original "38 rendered lines" hard limit was wrong — the 4 referenced few-shot examples are actually 69–75 rendered lines (verified by computation), and the base resume itself is 65. We replace the 38-limit with a target of 70 and a hard ceiling of 75 (the largest example). The customizer self-measures with `count_lines.py` and trims if over 75.

## Considered Options

- **Ceiling 65, target 60:** The plan's first guess. Rejected after computation showed 3 of 4 reference examples exceed 65 — a ceiling that criminalizes the reference material is fighting its own examples.
- **Ceiling 66:** The user's initial guess ("match the biggest example"). Rejected after computation showed the biggest is 75, not 66.
- **Ceiling 75, target 60:** Rejected because the base resume is 65 — a target of 60 means the customizer starts over target and must prune before adding anything.
- **Ceiling 75, target 70 (chosen):** Target 70 gives the customizer a realistic aim (slightly below the max example, which rendered to exactly 2 pages). Ceiling 75 is the "must trim if over" line. All reference examples are compliant.

## Consequences

- The few-shot examples are the ground truth for "what a good customized resume looks like." The ceiling matches them exactly.
- The customizer aims for 70, giving ~5 lines of headroom before the ceiling. This is enough room for LinkedIn-pulled additions without immediate trimming.
- The base resume (65 lines) is under both target and ceiling — the customizer has room to grow it.
