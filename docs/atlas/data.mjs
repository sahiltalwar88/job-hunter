// Single source of truth for the job-hunter atlas.
// Build: node docs/atlas/build.mjs  → writes docs/SYSTEM.md and docs/atlas.html
// Edit this file, rebuild, republish — never edit the generated files.

export const META = {
  title: 'job-hunter',
  artifactUrl: '',
  sourcePath: 'docs/atlas/data.mjs',
  buildCmd: 'node docs/atlas/build.mjs',
  stats: [
    { k: 'System', v: 'job-hunter · v1' },
    { k: 'Model roles', v: '2 (customizer-model, grader-model)' },
    { k: 'LLM tasks', v: '5' },
    { k: 'Pipeline stages', v: '13 active · 4 deferred' },
    { k: 'Engine', v: 'LangGraph StateGraph + Pydantic state' },
  ],
  intro: `_**This file is the living source of truth for the design.** The interactive atlas is built from the same data._`,
  onePara: `An AI agent workspace that automates job applications: pulls listings from a job scraper, grades them against a candidate profile, customizes resumes through an iterative grade-and-improve loop, and parks passing resumes for manual application. A LangGraph StateGraph orchestrates the per-job pipeline; LLMs handle 5 cognitive tasks. Hourly via systemd.`,
  costModel: [],
  deepDive: '',
  platformGives: 'Devin CLI (<code>devin -p</code>) for LLM invocation in non-interactive mode. Custom subagent profiles for debug mode. PreToolUse/Stop hooks for integrity enforcement. Systemd user timers for scheduling.',
  weOwn: 'The orchestrator (<code>pipeline/__main__.py</code> + <code>pipeline/</code> package), all pipeline scripts, the enrichment sidecar, the customizer/grader/truthfulness prompts and profiles, the hooks, and all documentation.',
  filesystem: `job-hunter/
  pipeline/           # pipeline package (infrastructure, steps, helpers)
  _config/          # grading/veracity protocols, glossary, conventions
  _config/_config/profile/   # base resume, full career history
  .devin/           # pipeline config, hooks, subagent profiles
  stages/1_listings/ # JDs awaiting grading
  stages/2_drafts/   # resumes in optimization loop
  stages/4_ready/    # passing resumes awaiting application
  stages/6_rejected/  # abandoned listings
  stages/7_trash/     # auto-rejected jobs
  data/             # enrichment sidecar (SQLite)
  docs/             # ADRs + this atlas`,
};

// Decisions render as the SYSTEM.md table and as the final chapter's "Decisions locked" list.
export const DECISIONS = [
  { axis: 'Architecture', decision: 'Code orchestrates, LLMs cognate — 5 LLM tasks only, all plumbing in Python', adr: '—' },
  { axis: 'Self-measurement', decision: 'Customizer runs count_lines during its own turn and self-trims', adr: '[0001](../adr/0001-self-measurement-in-customizer-turn.md)' },
  { axis: 'Exec restriction', decision: 'Universal whitelist hook (pipeline mode only) — two-tier: count_lines/pytest/mv/echo + git/gh/ls/cat/grep/etc.', adr: '[0002](../adr/0002-universal-exec-whitelist-hook.md)' },
  { axis: 'Line budget', decision: 'Target 70, hard ceiling 75 rendered lines', adr: '[0003](../adr/0003-line-budget-target-70-ceiling-75.md)' },
  { axis: 'Widow elimination', decision: 'Dropped entirely — cosmetic concern, not worth the time budget', adr: '[0004](../adr/0004-drop-widow-elimination.md)' },
  { axis: 'Enrichment storage', decision: 'SQLite sidecar instead of writing to all_jobs.json', adr: '—' },
  { axis: 'Agent separation', decision: 'Customizer, grader, and truthfulness verifier are distinct agents', adr: '—' },
  { axis: 'Pipeline engine', decision: 'LangGraph StateGraph with Pydantic state + SqliteSaver checkpointer', adr: '[0005](../adr/0005-langgraph-as-pipeline-engine.md)' },
  { axis: 'Clearance detection', decision: 'LLM-only (no regex) — JD grader returns CLEARANCE instead of a grade', adr: '[0007](../adr/0007-llm-only-clearance-check.md)' },
  { axis: 'Optimize termination', decision: 'LLM "can you improve?" check + hard limits (grade ≥ 9 + no core gaps, max iterations)', adr: '[0009](../adr/0009-optimize-cycle-termination.md)' },
];

export const GROUPS = [
  { id: 'ext', title: 'External' },
  { id: 'prep', title: 'Prep' },
  { id: 'disc', title: 'Discovery' },
  { id: 'refs', title: 'Reference pool' },
  { id: 'grade', title: 'JD grading' },
  { id: 'triage', title: 'Triage' },
  { id: 'custom', title: 'Customization' },
  { id: 'gates', title: 'Quality gates' },
  { id: 'out', title: 'Output' },
  { id: 'defer', title: 'Not yet switched on' },
];

// Structure = one isometric box.
export const NODES = [
  // ── External ──
  { id: 'JS', code: 'JS', name: 'Job Scraper', short: 'SCRAPER', group: 'ext', gx: 0, gy: 0, w: 3, d: 2, h: 30, kind: 'slab',
    one: 'The adjacent repo that feeds raw job listings.',
    what: 'A GitHub Actions pipeline that scrapes LinkedIn job postings on an hourly schedule and commits them to a rolling master file.',
    how: '<code>../job-scraper/scrape_jobs.py</code> → <code>output/all_jobs.json</code> (3.9MB+, 7-day rolling window). Read-only from job-hunter\'s perspective.',
    steps: [['Scrape', 'Hourly GitHub Actions workflow pulls new LinkedIn postings.'], ['Dedup', 'Jobs deduped by URL across search terms and geos.'], ['Commit', 'Results committed to all_jobs.json.']],
    cond: [] },

  { id: 'ST', code: 'ST', name: 'Systemd Timer', short: 'TIMER', group: 'ext', gx: 0, gy: 4, w: 2, d: 2, h: 28, kind: 'job',
    one: 'The hourly heartbeat that kicks off the pipeline.',
    what: 'A systemd user timer that triggers the pipeline service every hour with boot catch-up.',
    how: '<code>job-hunter-pipeline.timer</code> → <code>job-hunter-pipeline.service</code>. <mark>Hourly</mark> with <code>Persistent=true</code> for boot catch-up.',
    steps: [['Tick', 'Timer fires every hour.'], ['Launch', 'Starts <code>pipeline/__main__.py</code> as a service.']],
    cond: [] },

  // ── Prep ──
  { id: 'OR', code: 'OR', name: 'Orchestrator', short: 'ORCHESTRATOR', group: 'prep', gx: 4, gy: 1, w: 3, d: 3, h: 72, kind: 'tall',
    one: 'The brain — a LangGraph StateGraph that runs the per-job pipeline, with a plain-function prep phase.',
    what: 'The control plane: prep phase (feasibility, fetch, discover) runs as plain functions, then a per-job LangGraph StateGraph handles ingest, grading, customization, optimization loops, truthfulness, and routing. Each job runs as a separate graph invocation with SqliteSaver checkpointing for resumability. It invokes LLMs for cognitive tasks and handles all plumbing itself.',
    how: '<code>pipeline/__main__.py</code> + <code>pipeline/</code> package. Acquires a file lock, loads config from <code>config.json</code>, runs prep functions, invokes the per-job graph for each new job. Graph nodes call <code>devin -p</code> for LLM work via <code>pipeline/infrastructure/devin_cli.py</code> (wrapped by <code>pipeline/infrastructure/llm_interface.py</code>). Commits and pushes at the end.',
    steps: [['Lock', 'Acquire <code>.devin/pipeline.lock</code> to prevent concurrent runs.'], ['Config', 'Load thresholds, timeouts, model names from config.json.'], ['Prep 1-2', 'Feasibility check + fetch JDs (plain functions).'], ['Prep 3', 'Discover + dedup new jobs (plain function).'], ['Per-job graph', 'For each new job: ingest → grade JD → triage → customize → grade resume → optimize loop → truthfulness → stages/4_ready/stages/6_rejected.'], ['Checkpoint', 'SqliteSaver at <code>data/jobs.db</code> — interrupted runs resume per job.'], ['Finish', 'Commit, push, notify, release lock.']],
    cond: [
      { q: 'Should optimization iterations run in parallel for multiple jobs?', r: 'No — sequential is safer and cost is bounded by max_optimization_iterations (2026-08-24).' },
    ] },

  { id: 'ES', code: 'ES', name: 'Enrichment Sidecar', short: 'SIDECAR', group: 'prep', gx: 2, gy: 5, w: 3, d: 3, h: 36, kind: 'store',
    one: 'A SQLite database that stores pipeline enrichments separate from the scraper\'s output.',
    what: 'Holds feasibility tags and JD descriptions so the scraper\'s all_jobs.json stays read-only. Replaces the old architecture where enrichments were written directly to all_jobs.json (and lost on every scraper update).',
    how: '<code>data/enrichments.db</code>. Managed by <code>pipeline/infrastructure/enrichment_store.py</code>. Gitignored — regenerable from scraper data + LLM/HTTP fetches.',
    steps: [['Write feasibility', '<code>store.set_feasibility(url, feasible, tier, ...)</code>'], ['Write JD', '<code>store.set_description(url, text)</code>'], ['Read', '<code>store.merge_into_jobs(jobs)</code> merges sidecar data into job list.']],
    cond: [] },

  { id: 'FC', code: 'FC', name: 'Feasibility Checker', short: 'FEASIBILITY', group: 'prep', gx: 5, gy: 5, w: 2, d: 2, h: 20, kind: 'cards',
    one: 'LLM task 1 — tags each job as preferred, yes, or no.',
    what: 'An LLM that receives job metadata (title, company, location) and returns a tripartite verdict: "preferred" (Big Tech / top-tier fit), "yes" (fits but not Big Tech), or "no" (doesn\'t fit).',
    how: '<code>pipeline/infrastructure/feasibility_checker.py</code> (ported from scraper). Uses <code>DevinCLIChecker</code> with customizer-model via <code>llm.py</code>. Prompt in <code>config.json</code> → <code>feasibility_prompt</code>. Verdicts stored in the sidecar.',
    steps: [['Batch', 'Load unchecked jobs from sidecar.'], ['Call LLM', 'Send job metadata to customizer-model in batches.'], ['Parse', 'Extract tier verdict (preferred/yes/no).'], ['Store', 'Write feasible + feasibility fields to sidecar.']],
    cond: [] },

  { id: 'JF', code: 'JF', name: 'JD Fetcher', short: 'JD FETCH', group: 'prep', gx: 7, gy: 4, w: 2, d: 2, h: 20, kind: 'box',
    one: 'Fetches JD descriptions from LinkedIn for feasible jobs.',
    what: 'A code step that fetches full JD text for feasible jobs that don\'t already have descriptions. The backfill scraper saves title/company/location/URL only — no JD text.',
    how: '<code>pipeline/helpers/fetch_jds.py</code>. Reuses scraper\'s <code>fetch()</code> with built-in exponential backoff for LinkedIn 429s (30s × 2^attempt, up to 4 retries). Writes to the sidecar, not all_jobs.json.',
    steps: [['Find gaps', 'Query feasible jobs missing descriptions.'], ['Fetch', 'HTTP GET each JD URL with backoff.'], ['Store', 'Write description text to sidecar incrementally (crash-safe).']],
    cond: [] },

  // ── Discovery ──
  { id: 'CF', code: 'CF', name: 'Clearance Detection', short: 'CLEARANCE', group: 'disc', gx: 9, gy: 3, w: 2, d: 2, h: 28, kind: 'gate',
    one: 'LLM-only detection — auto-rejects clearance-required jobs during JD grading.',
    what: 'If a JD requires security clearance (active, eligible, TS/SCI, secret, ability to obtain, etc.), the JD grader LLM returns <code>CLEARANCE</code> instead of a grade. The conditional edge routes it to trash. No regex filter (ADR-0007).',
    how: 'LLM-only — the JD grading prompt instructs the LLM to return <code>CLEARANCE</code> if the job requires clearance. The graph\'s <code>route_after_jd_grade</code> checks <code>state.jd_grade.is_clearance</code> and routes to the trash node.',
    steps: [['Grade JD', 'LLM receives JD text.'], ['Detect', 'If clearance required → returns <code>CLEARANCE</code> instead of a grade.'], ['Reject', 'Graph routes to <code>stages/7_stages/7_trash/trash/&lt;lt;company-role&gt;/</code>.']],
    cond: [] },

  { id: 'LI', code: 'LI', name: 'Listings', short: 'LISTINGS', group: 'disc', gx: 10, gy: 5, w: 2, d: 2, h: 16, kind: 'screen',
    one: 'The folder where JD files await grading.',
    what: 'Each new feasible job gets a folder <code>stages/1_stages/1_listings/listings/&lt;lt;company-role&gt;/</code> with a <code>[TBD] job-description.md</code> file. After grading, the file is renamed to <code>[score] job-description.md</code>.',
    how: 'Filesystem. One folder per job listing. <code>[TBD]</code> prefix = ungraded, <code>[score]</code> prefix = graded.',
    steps: [['Ingest', 'Create folder + [TBD] JD file for each new feasible job.'], ['Grade', 'JD grader renames [TBD] → [score].'], ['Route', 'Triage moves the folder to stages/7_trash/, rejected/, or drafts/.']],
    cond: [] },

  // ── Reference pool ──
  { id: 'BR', code: 'BR', name: 'Base Resume', short: 'BASE RESUME', group: 'refs', gx: 8, gy: 8, w: 2, d: 2, h: 20, kind: 'box',
    one: 'The generic resume — the only customization base.',
    what: 'Every tailored resume starts from this template. The orchestrator pre-copies it to <code>stages/2_stages/2_drafts/drafts/&lt;lt;slug&gt;/[TBD] resume-v1.md</code> before the customizer runs. The customizer edits the copy in place. Also the primary JD grading reference.',
    how: '<code>_config/_config/profile/base-resume/base-resume.md</code>. Root-owned, immutable. 70 rendered lines.',
    steps: [],
    cond: [] },

  { id: 'LE', code: 'LE', name: 'LinkedIn Experience', short: 'LINKEDIN', group: 'refs', gx: 10, gy: 8, w: 2, d: 2, h: 20, kind: 'box',
    one: 'The full LinkedIn career history — the sourcing reference.',
    what: 'Consulted during JD grading (to know what\'s available) and customization (to pull in experience not on the base resume). Never used as a customization base. Truthfulness review verifies against this.',
    how: '<code>_config/_config/profile/full-experience/full-experience.md</code>. Root-owned, immutable.',
    steps: [],
    cond: [] },

  { id: 'CG', code: 'CG', name: 'Customization Protocol', short: 'PROTOCOL', group: 'refs', gx: 9, gy: 10, w: 2, d: 2, h: 20, kind: 'box',
    one: 'The rules for customizing a resume — the single source of truth for the customizer.',
    what: 'Defines formatting, ordering, truthfulness, strategy rules, line budget (target 70, ceiling 75), and the self-measurement instruction. The customizer follows it strictly.',
    how: '<code>_config/resume-customization-protocol.md</code>. The single source of truth for customization rules — line budget, self-measurement, reference pool semantics, formatting, truthfulness constraints.',
    steps: [],
    cond: [
      { q: 'Should the guide move to _config/ with the grading and veracity protocols?', r: 'Yes — agreed during grilling session (2026-08-24). Deferred to a separate task.' },
    ] },

  // ── JD Grading ──
  { id: 'JG', code: 'JG', name: 'JD Grader', short: 'JD GRADER', group: 'grade', gx: 12, gy: 3, w: 2, d: 2, h: 20, kind: 'cards',
    one: 'LLM task 2 — grades a JD against the candidate profile.',
    what: 'Receives JD text, reads the base resume and full career history, and returns a grade (0-10) with justification. The orchestrator parses the text output and does the rename/triage.',
    how: 'customizer-model via <code>devin -p</code>. Prompt: <code>build_jd_grading_prompt()</code> in <code>pipeline/step4_grade_jd.py</code>. Outputs <code>GRADE: N\\nJUSTIFICATION: ...</code> or <code>CLEARANCE</code> to stdout. Code parses and renames.',
    steps: [['Read profile', 'Read base resume + full career history.'], ['Grade', 'Assess fit: experience match, realistic obtainability, gap analysis.'], ['Output', 'Return GRADE: N + JUSTIFICATION to stdout.']],
    cond: [] },

  // ── Triage ──
  { id: 'TL', code: 'TL', name: 'Triage Logic', short: 'TRIAGE', group: 'triage', gx: 14, gy: 2, w: 2, d: 2, h: 28, kind: 'gate',
    one: 'Routes JDs by grade: trash, rejected, or drafts.',
    what: 'Code that moves the listing folder based on the JD grade. Grade &lt; 6 → trash. Grade 6-7.9 → rejected. Grade ≥ 8 → drafts (proceed to customization).',
    how: '<code>step5_triage_node()</code> in <code>pipeline/step5_triage.py</code>. Threshold from <code>config.jd_grade_threshold</code> (default 8). Uses <code>move_dir()</code> to relocate folders.',
    steps: [['Check grade', 'Compare JD grade against thresholds.'], ['Route', '&lt;6 → stages/7_trash/, 6-7.9 → stages/6_rejected/[JOB-FIT], ≥8 → stages/2_drafts/']],
    cond: [] },

  { id: 'TR', code: 'TR', name: 'Trash', short: 'TRASH', group: 'triage', gx: 15, gy: 0, w: 2, d: 2, h: 16, kind: 'screen',
    one: 'Auto-rejected jobs — clearance-required or JD grade &lt; 6.',
    what: 'Skimmable but periodically emptied. Two triggers: LLM detected a clearance requirement during JD grading, or JD grade below 6.',
    how: 'Filesystem: <code>stages/7_stages/7_trash/trash/&lt;lt;company-role&gt;/</code>. Periodically deleted by the user.',
    steps: [],
    cond: [] },

  { id: 'RJ', code: 'RJ', name: 'Rejected', short: 'REJECTED', group: 'triage', gx: 15, gy: 5, w: 2, d: 2, h: 16, kind: 'screen',
    one: 'Abandoned listings — weak fit or couldn\'t optimize the resume.',
    what: 'Two subfolder types: <code>[JOB-FIT]</code> (JD grade 6-7.9, weak overall fit) and <code>[RESUME]</code> (resume grade &lt; 9 after optimization loop). Kept for reference.',
    how: 'Filesystem: <code>stages/6_rejected/[JOB-FIT] &lt;company-role&gt;/</code> or <code>stages/6_rejected/[RESUME] &lt;company-role&gt;/</code>.',
    steps: [],
    cond: [] },

  // ── Customization ──
  { id: 'CU', code: 'CU', name: 'Customizer', short: 'CUSTOMIZER', group: 'custom', gx: 17, gy: 3, w: 2, d: 2, h: 20, kind: 'cards',
    one: 'LLM task 3 — edits a pre-copied resume from the reference pool to optimally match the JD.',
    what: 'The orchestrator pre-copies the base resume to <code>stages/2_stages/2_drafts/drafts/&lt;lt;slug&gt;/[TBD] resume-v1.md</code>. The customizer edits it in place — pulling relevant experience from LinkedIn, rephrasing, condensing, reordering. Self-measures with count_lines and self-trims if over 75 rendered lines. Also serves as the optimizer (re-invoked with grader feedback). The JD grade is passed to the first customization prompt.',
    how: 'customizer-model via <code>devin -p</code> (automated) or custom subagent profile <code>util/agent-profiles/customizer.md</code> (debug). Prompt: <code>build_customize_prompt()</code>. Follows the customization protocol strictly. Runs <code>count_lines</code> via exec for self-measurement.',
    steps: [['Read inputs', 'Pre-copied base resume + protocol + LinkedIn + JD + JD grade + few-shot examples.'], ['Customize', 'Edit the pre-copied resume in place. Draw from the reference pool to optimally match the JD. Rephrase, condense, reorder, pull — all valid.'], ['Self-measure', 'Run <code>python3 -m pipeline.helpers.count_lines --json</code> on the output.'], ['Self-trim', 'If &gt; 75 rendered lines, trim least JD-relevant bullets. Up to 3 passes.']],
    cond: [
      { q: 'What happens if count_lines reports >75 after 3 trim passes?', r: 'Accept and proceed — the grader is the quality gate, not the line count (2026-08-24).' },
    ] },

  { id: 'CL', code: 'CL', name: 'count_lines', short: 'LINE CTR', group: 'custom', gx: 18, gy: 5, w: 2, d: 2, h: 18, kind: 'box',
    one: 'A script that measures rendered line count — the customizer\'s only exec tool.',
    what: 'Takes a resume markdown file and outputs per-bullet character count, rendered line count, widow flags (diagnostic only), total rendered lines, and total bullet count. The customizer runs it during its own turn to self-measure and self-trim.',
    how: '<code>pipeline/helpers/count_lines</code>. Human-readable default, <code>--json</code> for machine-readable. <code>--wrap-chars</code> CLI arg (default 102). Detects <code>* </code> bullet syntax with trailing two-space line breaks stripped.',
    steps: [['Parse', 'Extract bullets from markdown (lines starting with <code>* </code>).'], ['Count', 'Per-bullet char count → ceil(chars/102) rendered lines.'], ['Flag', 'Widow flags for 103-115 and 205-217 ranges (diagnostic only).'], ['Output', 'Total rendered lines + per-bullet breakdown.']],
    cond: [] },

  { id: 'DR', code: 'DR', name: 'Drafts', short: 'DRAFTS', group: 'custom', gx: 17, gy: 7, w: 2, d: 2, h: 16, kind: 'screen',
    one: 'The folder where resumes go through the customization and grading loop.',
    what: 'Each job gets a folder <code>stages/2_stages/2_drafts/drafts/&lt;lt;company-role&gt;/</code> with <code>[TBD] resume-vN.md</code> files. After grading, renamed to <code>[score] resume-vN.md</code>. The optimization loop iterates here until the grade is ≥ 9 or no further improvement is possible.',
    how: 'Filesystem. <code>[TBD]</code> = ungraded, <code>[score]</code> = graded. Version numbers increment in the grade-improve loop (v1, v2, v3...).',
    steps: [],
    cond: [] },

  // ── Quality Gates ──
  { id: 'RG', code: 'RG', name: 'Resume Grader', short: 'RES GRADER', group: 'gates', gx: 20, gy: 3, w: 2, d: 2, h: 20, kind: 'cards',
    one: 'LLM task 4 — grades a customized resume against the JD with realistic recruiter framing.',
    what: 'Reads the grading protocol, resume, and JD. Grades in a single pass, writes JSON to .grading/, renames the resume file from [TBD] to [score], and appends to .grades.log. Be realistically harsh — as harsh as a recruiter taking 30 seconds to skim.',
    how: 'grader-model via <code>devin -p</code> (automated) or <code>util/agent-profiles/resume-grader.md</code> (debug). Prompt: <code>build_resume_grading_prompt()</code>. Protocol: <code>_config/grading-protocol.md</code>. Uses <code>mv</code> to rename and <code>echo >> .grades.log</code> to append.',
    steps: [['Read protocol', 'Load grading rubric from <code>_config/grading-protocol.md</code>.'], ['Read resume + JD', 'Load the customized resume and the job description.'], ['Grade', 'Single pass: extract requirements → assign verdicts → compute score.'], ['Write JSON', 'Write grade to <code>.grading/&lt;slug&gt;/grade-vN.json</code>.'], ['Rename', '<code>mv [TBD] resume-vN.md [score] resume-vN.md</code>'], ['Log', '<code>echo "..." >> .grades.log</code>']],
    cond: [] },

  { id: 'GL', code: 'GL', name: 'Grades Log', short: 'GRADES LOG', group: 'gates', gx: 21, gy: 5, w: 2, d: 2, h: 24, kind: 'store',
    one: 'An append-only audit trail of every grade ever assigned.',
    what: 'Every JD grade and resume grade is appended here with timestamp, file path, score, model, and hit/gap counts. Tracked in git — a permanent record.',
    how: '<code>.grades.log</code>. Appended by the resume grader via <code>echo >> .grades.log</code>. Format: <code>timestamp | path | grade=N | model=X | hits=N gaps=N</code>.',
    steps: [],
    cond: [] },

  { id: 'TV', code: 'TV', name: 'Truthfulness Reviewer', short: 'VERACITY', group: 'gates', gx: 20, gy: 7, w: 2, d: 2, h: 20, kind: 'cards',
    one: 'LLM task 5 — verifies every resume claim against the base resume and LinkedIn.',
    what: 'Reads the veracity protocol, customized resume, base resume, and full career history. Classifies every claim into one of 4 buckets. If all claims verified → replies VERIFIED. If not → replies UNVERIFIED with specific claims to fix. Mandatory before moving to stages/4_ready/.',
    how: 'grader-model via <code>devin -p</code> (automated) or <code>util/agent-profiles/truthfulness-reviewer.md</code> (debug). Protocol: <code>_config/veracity-protocol.md</code>. Writes JSON to <code>.veracity/&lt;slug&gt;/verification.json</code>.',
    steps: [['Read protocol', 'Load veracity protocol from <code>_config/veracity-protocol.md</code>.'], ['Read resume', 'Load the customized resume.'], ['Read sources', 'Load base resume + full career history.'], ['Classify', 'Per-claim: verified in base, verified in LinkedIn, partial, or unverified.'], ['Synthesize', 'All verified → VERIFIED. Any unverified → UNVERIFIED + list.'], ['Write JSON', 'Write result to <code>.veracity/&lt;slug&gt;/verification.json</code>']],
    cond: [] },

  { id: 'HK', code: 'HK', name: 'Hooks', short: 'HOOKS', group: 'gates', gx: 22, gy: 6, w: 2, d: 2, h: 32, kind: 'gate',
    one: 'Integrity enforcement — hard guarantees that agents can\'t tamper with scores or run unapproved commands.',
    what: 'Three PreToolUse hooks and one Stop hook that enforce the pipeline\'s integrity rules: no score tampering, no unapproved exec commands, no completion with failing grades.',
    how: '<code>.devin/hooks.v1.json</code> → <code>util/hooks/</code>. <code>block_score_tamper.py</code> (PreToolUse: blocks renaming/overwriting graded resumes). <code>restrict_exec.py</code> (PreToolUse: exec whitelist, pipeline mode only — tier 1: count_lines/pytest/mv/echo >> .grades.log; tier 2: git/gh/ls/cd/cat/head/tail/wc/pwd/diff/grep/find/which/file/echo/cp; chaining allowed if all whitelisted; debug mode allows all). <code>block_failing_grade.py</code> (Stop: blocks completion with ungraded/below-threshold resumes). <code>audit_log.py</code> (PostToolUse: logs file writes).',
    steps: [['PreToolUse: exec', 'Check command against two-tier whitelist (pipeline mode only). Block if not whitelisted.'], ['PreToolUse: edit/write', 'Block writes to [score] resume files.'], ['PostToolUse', 'Log file writes to pipeline files.'], ['Stop', 'Block if ungraded or <9 resumes remain in stages/2_drafts/.']],
    cond: [
      { q: 'How does the user know to remove the exec hook before debugging?', r: 'Documented in the customizer fix plan and ADR-0002. Debug mode is manual — the user removes the hook from hooks.v1.json (2026-08-24).' },
    ] },

  // ── Output ──
  { id: 'RD', code: 'RD', name: 'Ready', short: 'READY', group: 'out', gx: 24, gy: 4, w: 2, d: 2, h: 16, kind: 'screen',
    one: 'Passing resumes awaiting manual application.',
    what: 'A resume moves here only if grade ≥ 9 AND truthfulness verified. The entire folder (JD + resume) is moved from stages/2_drafts/ to stages/4_ready/. The user reviews and submits manually.',
    how: 'Filesystem: <code>stages/4_stages/4_ready/ready/&lt;lt;company-role&gt;/</code>. Moved by <code>step10_ready_node()</code> in <code>pipeline/steps/step10_finalize.py</code>.',
    steps: [],
    cond: [] },

  { id: 'NO', code: 'NO', name: 'Notifications', short: 'NOTIFY', group: 'out', gx: 24, gy: 7, w: 2, d: 2, h: 18, kind: 'box',
    one: 'Pushover alerts for run summaries and high-priority events.',
    what: 'Sends push notifications at the end of each pipeline run: summary of what was processed, and high-priority alerts for ready resumes or errors.',
    how: '<code>pipeline/infrastructure/notify.py</code>. Pushover adapter. No-ops without credentials. Config in <code>.devin/pipeline.env</code> (gitignored).',
    steps: [['Summary', 'Send run summary (jobs processed, grades, ready count).'], ['Alert', 'High-priority push for ready resumes or pipeline errors.']],
    cond: [] },

  // ── Deferred (ghosts) ──
  { id: 'FF', code: 'FF', name: 'Form Filler', short: 'FORM FILL', group: 'defer', ghost: true, gx: 26, gy: 5, w: 2, d: 2, h: 20, kind: 'screen',
    one: 'Deferred — will fill out job application forms up to the last page.',
    what: 'A future agent that navigates to the job posting, fills in application fields, and stops at the last page. The user reviews and submits manually. Not yet implemented.',
    how: 'Planned: <code>stages/3_in-progress/in-progress/&lt;lt;company-role&gt;/</code>. Semi-automated co-work with the user.',
    steps: [['Navigate', 'Go to job posting URL.'], ['Fill', 'Fill in application fields.'], ['Stop', 'Get to last page and STOP — do not submit.']],
    cond: ['When will form-filling be implemented?'] },

  { id: 'SU', code: 'SU', name: 'Submission', short: 'SUBMIT', group: 'defer', ghost: true, gx: 27, gy: 7, w: 2, d: 2, h: 16, kind: 'screen',
    one: 'Deferred — the user\'s manual record of submitted applications.',
    what: 'After the user reviews and submits an application, they record it here. The agent never writes to this folder — it is the user\'s manual record only.',
    how: 'Planned: <code>stages/5_submitted/submitted/&lt;lt;company-role&gt;/</code>. Manual step — agent never writes here.',
    steps: [],
    cond: [] },
];

// Flows for the final "whole system" chapter.
export const FLOWS = [
  { id: 'ingest', name: 'Job ingestion', hops: [
    ['JS', 'OR', 'raw jobs', { source: 'all_jobs.json', count: 2453 }, 'xy'],
    ['OR', 'FC', 'unchecked jobs', { batch_size: 10 }, 'xy'],
    ['FC', 'ES', 'feasibility verdicts', { tier: 'preferred|yes|no' }, 'yx'],
    ['OR', 'JF', 'feasible jobs', { filter: 'feasible=true' }, 'xy'],
    ['JF', 'ES', 'JD descriptions', { source: 'LinkedIn', backoff: '30s×2^n' }, 'yx'],
  ]},
  { id: 'discover', name: 'Discovery + clearance', hops: [
    ['OR', 'LI', 'new feasible job', { url: 'linkedin.com/...', jd_text: '...' }, 'xy'],
    ['JG', 'CF', 'CLEARANCE?', { check: 'LLM detects clearance' }, 'yx'],
    ['CF', 'TR', 'clearance required', { reason: 'TS/SCI detected by LLM' }, 'yx'],
    ['LI', 'JG', 'cleared', { folder: 'stages/1_listings/<slug>/', file: '[TBD] job-description.md' }, 'xy'],
  ]},
  { id: 'jdgrade', name: 'JD grading + triage', hops: [
    ['OR', 'JG', 'JD text', { slug: 'google-director-of-engineering' }, 'xy'],
    ['JG', 'BR', 'read profile', { file: 'base-resume.md' }, 'xy'],
    ['JG', 'LE', 'read LinkedIn', { file: 'full-experience.md' }, 'yx'],
    ['JG', 'OR', 'GRADE: 8.5', { grade: 8.5, justification: '...' }, 'yx'],
    ['OR', 'TL', 'grade', { score: 8.5 }, 'xy'],
    ['TL', 'DR', '≥8 → drafts', { action: 'move_dir' }, 'xy'],
  ]},
  { id: 'customize', name: 'Resume customization', hops: [
    ['OR', 'CU', 'customize prompt', { slug: '...', jd_text: '...' }, 'xy'],
    ['CU', 'BR', 'read base', { file: 'base-resume.md' }, 'xy'],
    ['CU', 'LE', 'read LinkedIn', { file: 'full-experience.md' }, 'yx'],
    ['CU', 'CG', 'read guide', { file: 'resume-customization-protocol.md' }, 'xy'],
    ['CU', 'DR', 'write resume', { file: '[TBD] resume-v1.md' }, 'yx'],
    ['CU', 'CL', 'measure', { cmd: 'python3 -m pipeline.helpers.count_lines --json' }, 'xy'],
    ['CL', 'CU', 'line count', { total: 72, ceiling: 75 }, 'yx'],
    ['CU', 'DR', 'rewrite (trimmed)', { file: '[TBD] resume-v1.md', lines: 70 }, 'yx'],
  ]},
  { id: 'resgrade', name: 'Resume grading', hops: [
    ['OR', 'RG', 'grade prompt', { slug: '...', version: 'v1' }, 'xy'],
    ['RG', 'DR', 'rename', { from: '[TBD] resume-v1.md', to: '[9.0] resume-v1.md' }, 'yx'],
    ['RG', 'GL', 'append log', { entry: 'timestamp | ... | grade=9.0 | model=grader-model' }, 'xy'],
  ]},
  { id: 'verify', name: 'Truthfulness + ready', hops: [
    ['OR', 'TV', 'verify prompt', { slug: '...' }, 'xy'],
    ['TV', 'BR', 'read base', { file: 'base-resume.md' }, 'xy'],
    ['TV', 'LE', 'read LinkedIn', { file: 'full-experience.md' }, 'yx'],
    ['TV', 'OR', 'VERIFIED', { result: 'all claims verified' }, 'yx'],
    ['OR', 'RD', 'move to ready', { action: 'move_dir stages/2_drafts/ → stages/4_ready/' }, 'xy'],
    ['OR', 'NO', 'notification', { type: 'high-priority', message: 'resume ready' }, 'yx'],
  ]},
];

// Chapters = progressive disclosure.
export const CH = [
  { id: 'feed', title: 'The feed', reveal: ['JS', 'OR', 'ST'],
    lede: `Strip everything away and this is the heartbeat: a scraper feeds jobs, a timer kicks the orchestrator every hour.`,
    story: `<p>The <mark>orchestrator</mark> is the brain — a Python script that owns all state and sequences every step. It does not think; it calls LLMs for that. The scraper is an adjacent repo, read-only from here.</p>`,
    flow: [['ST', 'OR', 'hourly tick', { trigger: 'systemd timer' }], ['JS', 'OR', 'raw jobs', { source: 'all_jobs.json' }]] },

  { id: 'feasible', title: 'Is it feasible?', reveal: ['ES', 'FC'],
    lede: `Before doing anything, the orchestrator tags each job: is this a role the candidate could plausibly get?`,
    story: `<p>The <mark>feasibility checker</mark> is the first LLM task — it receives job metadata (title, company, location) and returns a tripartite verdict. Verdicts go to the sidecar, not back to the scraper's file.</p>`,
    flow: [['OR', 'FC', 'unchecked jobs', { batch: 10 }], ['FC', 'ES', 'verdicts', { tier: 'preferred|yes|no' }]] },

  { id: 'fetchjd', title: 'Getting the JD', reveal: ['JF', 'CF'],
    lede: `The scraper saves titles only. The orchestrator fetches full JD text. Clearance is detected by the LLM during JD grading — no regex filter.`,
    story: `<p><mark>Clearance-required jobs go to trash</mark> — the JD grader LLM returns <code>CLEARANCE</code> instead of a grade, and the graph routes to trash. No regex filter (ADR-0007). The JD fetcher has built-in backoff for LinkedIn rate-limiting.</p>`,
    flow: [['OR', 'JF', 'feasible jobs', {}], ['JF', 'ES', 'JD text', { backoff: '30s×2^n' }], ['JG', 'CF', 'CLEARANCE?', { check: 'LLM' }], ['CF', 'TR', 'clearance → trash', { reason: 'TS/SCI' }]] },

  { id: 'worthit', title: 'Is it worth applying?', reveal: ['JG', 'BR', 'LE'],
    lede: `The JD grader reads the candidate's profile and returns a score. The base resume and full career history are the reference material.`,
    story: `<p>The <mark>JD grader</mark> is LLM task 2. It reads both the base resume (what's on the generic resume) and the full career history (what's available but not surfaced). The grade reflects what the candidate could bring, not just what's currently on the base.</p>`,
    flow: [['OR', 'JG', 'JD text', { slug: '...' }], ['JG', 'BR', 'read profile', {}], ['JG', 'LE', 'read LinkedIn', {}], ['JG', 'OR', 'GRADE: 8.5', { grade: 8.5 }]] },

  { id: 'sort', title: 'Sorting the pile', reveal: ['TL', 'TR', 'RJ'],
    lede: `The orchestrator routes by grade: trash, rejected, or drafts. Only ≥ 8 proceeds to customization.`,
    story: `<p>Grade &lt; 6 → <mark>trash</mark>. Grade 6-7.9 → <mark>rejected</mark> (weak fit). Grade ≥ 8 → drafts (worth customizing a resume). The triage is pure code — no LLM involved.</p>`,
    flow: [['JG', 'TL', 'grade', { score: 8.5 }], ['TL', 'RJ', '6-7.9 → rejected', {}], ['TL', 'DR', '≥8 → drafts', {}]] },

  { id: 'pool', title: 'The reference pool', reveal: ['CG', 'DR'],
    lede: `Three documents form the pool the customizer draws from. The drafts folder is where the work happens.`,
    story: `<p>The <mark>base resume is the only customization base</mark> — every tailored resume starts from it. The full career history supplies material not on the base. The customization protocol is the rulebook. The customizer treats all three as a reference pool to optimally match the JD.</p>`,
    flow: [] },

  { id: 'write', title: 'Customizing the resume', reveal: ['CU', 'CL'],
    lede: `The orchestrator pre-copies the base resume. The customizer edits it in place, measures with count_lines, and self-trims if over 75 lines.`,
    story: `<p>The <mark>customizer</mark> is LLM task 3 — and also the optimizer (same agent, re-invoked with grader feedback). The orchestrator pre-copies the base resume to <code>[TBD] resume-v1.md</code> and passes the JD grade. The customizer edits the copy in place — no recreating from scratch. It runs <code>count_lines</code> during its own turn to self-measure, keeping full context for smarter trim decisions. Up to 3 trim passes before accepting.</p>`,
    flow: [['OR', 'DR', 'pre-copy base', { file: '[TBD] resume-v1.md' }], ['OR', 'CU', 'customize (edit in place)', { slug: '...', jdGrade: 8.5 }], ['CU', 'CL', 'measure', { cmd: 'count_lines --json' }], ['CL', 'CU', '72 lines', { total: 72, ceiling: 75 }]] },

  { id: 'resgrade', title: 'Grading the resume', reveal: ['RG', 'GL'],
    lede: `A separate agent grades the resume with realistic recruiter harshness. It owns the score rename.`,
    story: `<p>The <mark>resume grader</mark> is LLM task 4, running on grader-model for independent evaluation. It grades in a single pass, writes JSON, renames the file from [TBD] to [score], and appends to the audit log. The customizer never grades — that's the grader's job.</p>`,
    flow: [['OR', 'RG', 'grade prompt', { version: 'v1' }], ['RG', 'DR', 'rename', { from: '[TBD]', to: '[9.0]' }], ['RG', 'GL', 'append log', { entry: 'grade=9.0' }]] },

  { id: 'truth', title: 'Is it true?', reveal: ['TV', 'HK'],
    lede: `A truthfulness verifier checks every claim against the LinkedIn superset. Hooks enforce integrity throughout.`,
    story: `<p>The <mark>truthfulness reviewer</mark> is LLM task 5 — mandatory before moving to stages/4_ready/. The <mark>hooks</mark> are the hard guarantees: no score tampering, no unapproved exec, no completion with failing grades. Together they ensure the resume is both good (≥9) and honest.</p>`,
    flow: [['OR', 'TV', 'verify', { slug: '...' }], ['TV', 'BR', 'read base', {}], ['TV', 'LE', 'read LinkedIn', {}], ['TV', 'OR', 'VERIFIED', { result: 'all claims verified' }]] },

  { id: 'done', title: 'Done', reveal: ['RD', 'NO'],
    lede: `Passing resumes land in stages/4_ready/. The user gets a push notification.`,
    story: `<p>A resume moves to <mark>stages/4_ready/</mark> only if grade ≥ 9 AND truthfulness verified. The user reviews and submits manually — the agent never submits.</p>`,
    flow: [['OR', 'RD', 'move to ready', { action: 'move_dir' }], ['OR', 'NO', 'notification', { type: 'high-priority' }]] },

  { id: 'later', title: 'Later', reveal: ['FF', 'SU'],
    lede: `Designed for, not switched on. Form-filling and submission are deferred.`,
    story: `<p>The <mark>form filler</mark> will navigate to job postings, fill in fields, and stop at the last page. The user reviews and submits. <code>stages/5_submitted/</code> is the user's manual record — the agent never writes there.</p>`,
    flow: [['RD', 'FF', 'ready resume', {}], ['FF', 'SU', 'user submits', { manual: true }]] },

  { id: 'all', title: 'The whole system', reveal: [],
    lede: `Everything at once, for free exploration.`,
    story: `<p>Choose which flow runs (bottom left). Hover anything; click to pin; → goes inside. The <mark>Open questions</mark> tab lists every question by ID.</p>`,
    flow: null },
];

export const HOW_HTML = `<div class="eyebrow">job-hunter · v1</div><h1 class="t">How it's built</h1><div class="sub">the shape and what sits around it</div>
<h3 class="sec">Architecture</h3>
<p>Code orchestrates, LLMs cognate. <code>pipeline/__main__.py</code> + the <code>pipeline/</code> package handle all plumbing (file I/O, HTTP fetching, state management, routing) via Python. A LangGraph StateGraph runs the per-job pipeline with conditional edges and an optimize cycle. LLM agents are invoked via <code>devin -p</code> for 5 cognitive tasks only: feasibility checking, JD grading, resume customization, resume grading, and truthfulness verification.</p>
<h3 class="sec">Filesystem</h3>
<pre>job-hunter/
  pipeline/           # pipeline package (infrastructure, steps, helpers)
  _config/          # grading/veracity protocols, glossary
  _config/profile/          # base resume, LinkedIn, customization protocol
  .devin/           # pipeline config, hooks, subagent profiles
  stages/1_listings/ # JDs awaiting grading
  stages/2_drafts/   # resumes in optimization loop
  stages/4_ready/    # passing resumes awaiting application
  stages/6_rejected/  # abandoned listings
  stages/7_trash/     # auto-rejected jobs
  data/             # enrichment sidecar (SQLite)
  docs/             # ADRs + this atlas</pre>
<h3 class="sec">Models</h3>
<p><b>customizer-model</b> — feasibility checking, JD grading, resume customization (the "doing" tasks). <b>grader-model</b> — resume grading, truthfulness verification (the "judging" tasks, isolated for independence).</p>`;
