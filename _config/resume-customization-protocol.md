# Resume Customization Guide

<!-- ICM Layer 3 — read by both the automated `devin -p` customizer calls and the debug
     `customizer` subagent profile. Defines how resumes are customized for specific JDs. -->

## What to Emphasize

Lead with the base resume's strongest experience that matches the JD's core requirements. When a JD emphasizes a specific theme (scale, team building, a technology, a domain), draw from the part of the candidate's background that best demonstrates it, using the base resume and full experience.

## What to Downplay or Omit

Condense or omit material that doesn't support the JD's requirements. Keep the base resume's early-career material brief unless the JD asks for a skill that only appears there (per the full experience); in that case surface that detail in the existing bullet rather than adding new roles.

## Formatting Preferences

- **Section order:** Preserve the base resume's top-level sections and their order exactly. Don't add, remove, or reorder top-level sections.
- **Summary/objective:** Don't add one if the base resume doesn't have one.
- **Bullet style:** No periods at the end of bullets. Match the base resume exactly.
- **Skills table:** Keep the two-column table format. Don't convert to a flat list.
- **Font/spacing:** Not relevant — resumes are markdown only, graded and reviewed via text. No PDF generation step in the current pipeline.

### Length & Bullet Optimization Rules

* **Target:** 70 rendered lines. **Hard ceiling:** 75 rendered lines. The few-shot examples range from 69–75 rendered lines and serve as the practical reference.
* **Character Wrap Threshold:** Bullet lines wrap at **102 characters** per line (Calibri 11pt, 0.5" margins).
  * ≤ 102 characters = 1 line
  * 103–204 characters = 2 lines
  * 205–306 characters = 3 lines

### Self-Measurement & Trimming Protocol

1. **Calculate Rendered Line Count:**
   $$\text{Total Lines} = \sum \text{ceil}\left(\frac{\text{Bullet Character Length}}{102}\right)$$

2. **Self-measure with `count_lines.py`:** After writing the resume, run `python3 -m pipeline.helpers.count_lines <path> --json` to measure rendered lines. Do not attempt to count lines manually — LLMs cannot reliably count rendered lines.

3. **Self-trim if over the ceiling:** If total exceeds 75 rendered lines, trim the least JD-relevant bullets until ≤ 75. Target ~70. Up to 3 trim passes. If still over 75 after 3 passes, accept and proceed — the grader is the quality gate, not the line count.

## Tone

Match the tone in the generic resume exactly - not too many buzzwords, minimal corporate jargon, to the point but not too informal.

## Source Material — The Reference Pool

Three documents form the reference pool the customizer draws from to optimally match the JD. The customizer's job is to select and rephrase the strongest truthful material from this pool — rephrasing, condensing, reordering, and pulling in material as needed, as long as every claim is truthful (appears in the base resume or LinkedIn).

- **Base resume** (`profile/base-resume/`) — the customization BASE. Every tailored resume starts from a fresh copy of this template. Preserve its structure, formatting, section order, and existing bullets as the starting point.
- **Full experience** (`profile/full-experience/`) — the SOURCING reference. Consult for material not on the base resume (new bullets, skills, technologies, earlier roles). Anything pulled in must appear verifiably in LinkedIn.
- **Few-shot examples** (`examples/customized-resumes/`) — hand-customized resumes that demonstrate the desired transformation patterns. Study these before customizing.
- **Never build a customized resume directly from the LinkedIn experience.** The output is always: base resume + pulled-in material, formatted to match the base. LinkedIn is a source, not a template.

## Customization Strategy

When customizing the resume for a specific JD, the agent may:
- **Preserve chronological job order.** Never reorder employers or roles — keep them in the exact order they appear in the base resume. Within a role, preserve the existing bullet order by default. Reorder bullets only when it creates a material improvement in JD alignment, and never move a bullet above the bullets that describe the role's fundamental scope, ownership, or core responsibilities.
- **Rewrite bullet wording** to mirror the JD's language (e.g. if the JD says "platform reliability," adjust a bullet that says "availability" to use "reliability" — but only if the meaning is identical)
- **De-emphasize or shorten bullets** that are irrelevant to the JD to stay within the 75 rendered line ceiling (see Length & Bullet Optimization Rules above)
- **Swap or modify items in the Skills table** to prioritize the capabilities the JD emphasizes, so long as the candidate's work experience clearly shows those skills
- **Condense the Early Career section** if space is needed for more relevant content above
- **Pull new bullets, skills, technologies, or earlier-role experience from the full LinkedIn experience** onto the base resume when the JD demands something not on the base — but only material that appears verifiably in LinkedIn. Format pulled material to match the base resume's style (no periods, same bullet structure).

The agent may NOT:
- Add new bullets, skills, technologies, or experience that appear nowhere in the base resume AND nowhere in the full LinkedIn experience. When in doubt, omit.
- Fabricate or infer experience from adjacent work — every added claim must be directly stated in the base resume or LinkedIn, not inferred.
- Add a summary, objective, or cover letter section
- Change company names, job titles, or employment dates

## Handling Gaps

When the JD asks for something not on the base resume:
- **In the full LinkedIn experience:** Pull the relevant experience from LinkedIn onto the base resume (e.g. the JD asks for a framework the base resume omits but the full experience lists under an earlier role — pull that onto the base, formatted to match).
- **Adjacent experience exists (in base or LinkedIn):** Highlight the adjacent experience without claiming the missing one (e.g. the JD asks for a specific orchestration tool the candidate never used, but they led a related infrastructure migration — emphasize the migration)
- **No relevant experience (in either doc):** Omit. Don't fabricate. Let the grade reflect the gap honestly.
- **Partial experience:** Emphasize the relevant portion of what the candidate has without overstating it. Don't reword to imply deeper expertise than they have.

## Hard Rules

1. **Analyze documents COMPLETELY rather than in isolated snippets.** Ensure that your changes are not duplicative of other bullets, and make sense holistically for both the section and the overall document.
2. **When modifying resumes, preserve the exact existing section header structure** (e.g., "Experience", "Education", "Skills"). Update content strictly within those existing headers without adding or modifying top-level sections.
3. **Ensure the formatting matches throughout.** For instance, don't add periods where there weren't any before, make sure the tabs and spacing match throughout, etc.
4. **Never invent or inflate metrics.** If a bullet says "20% improvement," don't change it to "30%" to better match the JD. The numbers are real.
5. **Never invent job titles, company names, or employment dates.** These are immutable facts.
6. **Never add skills, technologies, or experiences that appear nowhere in the base resume AND nowhere in the full LinkedIn experience.** Every added claim must be directly stated in the base resume or LinkedIn, not inferred from adjacent work. When in doubt, omit. If the JD asks for something the candidate doesn't have in either doc, see "Handling Gaps" above.
7. **Don't change terms arbitrarily unless they have the same meaning.** For instance, don't replace "monolith" with "distributed" — those are not interchangeable. Only swap terms when the meaning is identical.

## Few-Shot Examples

Hand-customized resumes may be provided as quality examples in `examples/customized-resumes/`. When they are, study them before customizing to learn the desired transformation patterns.

What to learn from these examples:
- Rewording bullets to mirror JD language while preserving meaning
- When to pull from LinkedIn vs. when to reword existing bullets
- When to condense vs. when to expand
- How to reorganize the Skills table for JD alignment
- Format fidelity to the base resume (no summaries, no JD contamination, no periods, correct section order)

What NOT to do (anti-patterns from rejected examples):
- Do not copy JD text into the resume body
- Do not add summary/objective sections
- Do not add "proprietary" or "consulting-speak" phrasing
- Do not duplicate bullets within a role
