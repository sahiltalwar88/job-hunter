# Resume Customization Guide

<!-- ICM Layer 3 — read by both the automated `devin -p` customizer calls and the debug
     `customizer` subagent profile. Defines how resumes are customized for specific JDs. -->

## What to Emphasize

I specialize in creating high performance teams (and improving underperforming teams) and operating at massive, internet-scale. I am trying to get a job at the Director level or higher in Big Tech companies (e.g. Google, Anthropic, Stripe, etc), so tailor the applications to those.

When a JD emphasizes a specific theme, draw from the relevant part of my background:
- **Scale/throughput:** Lead with Oracle (30B API calls/day, 1M transactions/sec) and RedShelf (20M API calls/day, 8M users)
- **Team building/org design:** Lead with RedShelf (20% output with 25% fewer people) and Oracle (scaled to 50-person org, 4 departures in 4 years)
- **AI/agentic modernization:** Lead with RedShelf (AI-enabled SDLC, Claude/Codex/Cursor, NotebookLM)
- **Governance/metrics:** Lead with RedShelf (DORA/SPACE, CodeClimate, engineering governance framework)
- **Compliance/security:** Lead with Oracle (SOC2, GDPR, CCPA, zero-data-retention, threat mitigation)
- **Cloud migration/infra:** Lead with Oracle (SoftLayer → OCI migration) and RedShelf (cost-to-serve reduction)
- **Entrepreneurship/ownership:** Lead with Ungambled (bootstrapped, seed round, GTM, CAC optimization)

## What to Downplay or Omit

Downplay direct software engineering skills — as a senior level manager, those are no longer directly relevant to my day to day responsibilities. Specifically:
- Remove or condense specific technologies from the Skills table if they're IC-level tools (e.g. C#, .NET, JavaScript frameworks)
- Keep the Early Career Highlights section but don't expand it — it's there to show progression, not to be the focus. Exception: if the JD demands a specific skill/technology that only appears in an early role (per the full LinkedIn experience), surface that detail in the existing early-career bullet rather than adding new roles. Don't bloat the section beyond what the JD justifies.
- Don't add IC-level technical skills to the Skills table even if the JD asks for them — the Skills table should reflect leadership/strategy capabilities

## Formatting Preferences

- **Section order:** Preserve exactly — Experience → Early Career Highlights → Personal Projects → Skills → Education. Don't add, remove, or reorder top-level sections.
- **No summary/objective at the top.** The base resume doesn't have one. Don't add one.
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

- **Base resume** (`profile/base-resume/base-resume.md`) — the customization BASE. Every tailored resume starts from a fresh copy of this template. Preserve its structure, formatting, section order, and existing bullets as the starting point.
- **Full experience** (`profile/full-experience/full-experience.md`) — the SOURCING reference. Consult for material not on the base resume (new bullets, skills, technologies, earlier roles). Anything pulled in must appear verifiably in LinkedIn.
- **Few-shot examples** (`examples/customized-resumes/`) — hand-customized resumes that demonstrate the desired transformation patterns. Study these before customizing.
- **Never build a customized resume directly from the LinkedIn experience.** The output is always: base resume + pulled-in material, formatted to match the base. LinkedIn is a source, not a template.

## Customization Strategy

When customizing the resume for a specific JD, the agent may:
- **Preserve chronological job order.** Never reorder employers or roles — keep them in the exact order they appear in the base resume. Within a role, preserve the existing bullet order by default. Reorder bullets only when it creates a material improvement in JD alignment, and never move a bullet above the bullets that describe the role's fundamental scope, ownership, or core responsibilities.
- **Rewrite bullet wording** to mirror the JD's language (e.g. if the JD says "platform reliability," adjust a bullet that says "availability" to use "reliability" — but only if the meaning is identical)
- **De-emphasize or shorten bullets** that are irrelevant to the JD to stay within the 75 rendered line ceiling (see Length & Bullet Optimization Rules above)
- **Swap or modify items in the Skills table** to prioritize the capabilities the JD emphasizes, so long as it is clear that I actually have those skills based on my work experience
- **Condense the Early Career section** if space is needed for more relevant content above
- **Pull new bullets, skills, technologies, or earlier-role experience from the full LinkedIn experience** onto the base resume when the JD demands something not on the base — but only material that appears verifiably in LinkedIn. Format pulled material to match the base resume's style (no periods, same bullet structure).

The agent may NOT:
- Add new bullets, skills, technologies, or experience that appear nowhere in the base resume AND nowhere in the full LinkedIn experience. When in doubt, omit.
- Fabricate or infer experience from adjacent work — every added claim must be directly stated in the base resume or LinkedIn, not inferred.
- Add a summary, objective, or cover letter section
- Change company names, job titles, or employment dates

## Handling Gaps

When the JD asks for something not on the base resume:
- **In the full LinkedIn experience:** Pull the relevant experience from LinkedIn onto the base resume (e.g. JD asks for React/Node — Lanetix has React/Redux/Node/Express in the full experience; pull that onto the base, formatted to match).
- **Adjacent experience exists (in base or LinkedIn):** Highlight the adjacent experience (e.g. JD asks for Kubernetes, I have OCI migration and Docker-adjacent work — emphasize the cloud migration experience)
- **No relevant experience (in either doc):** Omit. Don't fabricate. Let the grade reflect the gap honestly.
- **Partial experience:** Emphasize the relevant portion of what I have without overstating it. Don't reword to imply deeper expertise than I have.

## Grading Criteria

### JD Grade (Step 2) — how well does this job fit my background?
- **9-10:** My experience directly maps to the role's core requirements at similar scale. The role is Director+ at a company where my background is a clear fit. I'd be a top candidate.
- **7-8:** Strong overlap on most requirements. A few gaps but nothing critical. Worth customizing a resume.
- **5-6:** Some overlap but significant gaps or the role is too junior/senior. Marginal.
- **Below 5:** Poor fit. Don't bother.
- **Threshold to proceed:** > 8

### Resume Grade (Step 4) — how well does the customized resume match the JD?
- **9-10:** Every core JD requirement is addressed by specific, quantified experience. No gaps on critical items. The resume reads like it was written for this specific role.
- **7-8:** Most core requirements are addressed. Minor gaps. The resume is well-tailored but not perfect.
- **5-6:** Partial tailoring. Some requirements unaddressed. The resume feels generic for this JD.
- **Below 5:** Poor match. The resume doesn't address the JD's core needs.
- **Threshold to proceed:** ≥ 9

**What the grade measures:** How likely it is that the resume will be chosen for an interview: demonstrated impact at similar scale, leadership scope alignment, keyword/language alignment with the JD, and absence of gaps on critical requirements. Not just keyword matching — the experience behind the keywords must be real and relevant.

## Hard Rules

1. **Analyze documents COMPLETELY rather than in isolated snippets.** Ensure that your changes are not duplicative of other bullets, and make sense holistically for both the section and the overall document.
2. **When modifying resumes, preserve the exact existing section header structure** (e.g., "Experience", "Education", "Skills"). Update content strictly within those existing headers without adding or modifying top-level sections.
3. **Ensure the formatting matches throughout.** For instance, don't add periods where there weren't any before, make sure the tabs and spacing match throughout, etc.
4. **Never invent or inflate metrics.** If a bullet says "20% improvement," don't change it to "30%" to better match the JD. The numbers are real.
5. **Never invent job titles, company names, or employment dates.** These are immutable facts.
6. **Never add skills, technologies, or experiences that appear nowhere in the base resume AND nowhere in the full LinkedIn experience.** Every added claim must be directly stated in the base resume or LinkedIn, not inferred from adjacent work. When in doubt, omit. If the JD asks for something I don't have in either doc, see "Handling Gaps" above.
7. **Don't change terms arbitrarily unless they have the same meaning.** For instance, don't replace "monolith" with "distributed" — those are not interchangeable. Only swap terms when the meaning is identical.

## Few-Shot Examples

Four hand-customized resumes are provided as quality examples in `examples/customized-resumes/`. Study these before customizing to learn the desired transformation patterns:

1. **Salesforce VP Engineering Delivery & Excellence** — Gold standard for surgical rewording. Demonstrates clean JD language mirroring for a VP delivery/excellence role without over-transformation. (69 rendered lines, 34 bullets)
2. **IonQ Software Engineering Director, Cloud Services** — Demonstrates section-adding (Personal Projects from LinkedIn), condensing (Ungambled 6→2 bullets), and LinkedIn-pulling (incident response, architecture reviews). (75 rendered lines, 36 bullets)
3. **Core & Ads Serving Platform Director** — Demonstrates domain-specific tailoring for AdTech with architectural language, and LinkedIn-pulling from early career (Lanetix experiment). (72 rendered lines, 35 bullets)
4. **LaunchDarkly ED, Core** — Demonstrates domain-specific tailoring for platform engineering / feature flagging, and LinkedIn-pulling from early career (Lanetix experiment). (71 rendered lines, 35 bullets)

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
