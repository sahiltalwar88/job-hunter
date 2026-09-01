#!/usr/bin/env python3
"""Feasibility checker — LLM-based filter for relevant roles.

Ported from job-scraper's scrape_jobs.py. Uses the pipeline's LLM protocol
(RealLLM in production, FakeLLM in tests) for the actual LLM call, giving
it audit logging, permission mode control, workspace isolation, and
testability via dependency injection.

The feasibility prompt is candidate-specific and lives in config.json
(→ feasibility_prompt field). The scraper keeps its own copy of this class
for upstream sync, but job-hunter's pipeline uses this one.

Output format: the LLM is instructed to return a JSON array of objects with
`index`, `verdict`, and `rationale` fields. The response is parsed with
Pydantic (FeasibilityResult) for strict validation. Markdown code fences
are stripped by the shared parse_llm_json utility. Malformed JSON raises
an exception (caught by step1_feasibility's per-batch try/except).
"""
import abc
import json

from pydantic import BaseModel, ValidationError, field_validator


class FeasibilityResult(BaseModel):
    """One LLM feasibility verdict — validated via Pydantic (W9)."""

    index: int
    verdict: str
    rationale: str = ""

    @field_validator("verdict")
    @classmethod
    def validate_verdict(cls, v: str) -> str:
        v_upper = v.strip().upper()
        if v_upper not in ("PREFERRED", "YES", "NO"):
            raise ValueError(
                f"verdict must be PREFERRED, YES, or NO — got '{v}'"
            )
        return v_upper


class FeasibilityChecker(abc.ABC):
    """Check whether a job is plausibly relevant to the configured search.

    Verdicts are tripartite strings: "preferred", "yes", or "no".
    "preferred" = Big Tech / top-tier fit; "yes" = fits but not Big Tech;
    "no" = doesn't fit. Boolean verdicts are accepted for backward compat
    (True → "yes", False → "no").
    """

    @abc.abstractmethod
    def check_batch(self, jobs: list[dict]) -> dict[str, tuple[str, str]]:
        """Return {url: (verdict_str, rationale_str)} for each job.

        verdict_str is "preferred", "yes", or "no". rationale_str is a
        one-sentence explanation of the verdict (W9). Jobs not in the
        result dict default to ("yes", "") (safe default).
        """
        ...


class DevinCLIChecker(FeasibilityChecker):
    """Uses the pipeline's LLM protocol for feasibility checks.

    Batches 10 jobs per call to minimize subprocess overhead. The LLM
    callable is injected (RealLLM in production, FakeLLM in tests) for
    audit logging, permission mode control, and testability.
    """

    BATCH_SIZE = 10

    def __init__(self, llm, model: str = "customizer-model", prompt: str = "",
                 timeout: int = 60, workspace: str = "."):
        """Args:
            llm: LLM callable (RealLLM or FakeLLM) following the pipeline
                 LLM protocol — __call__(prompt, *, model, timeout, workspace,
                 retries, retry_delay, permission_mode, ...).
            model: Model name to pass to the LLM.
            prompt: Feasibility prompt (candidate-specific, from config).
            timeout: Per-call timeout in seconds.
            workspace: Working directory for the LLM session.
        """
        self.llm = llm
        self.model = model
        self.prompt = prompt
        self.timeout = timeout
        self.workspace = workspace

    def check_batch(self, jobs: list[dict]) -> dict[str, tuple[str, str]]:
        if not jobs:
            return {}
        if not self.prompt:
            print("  ⚠️  No feasibility prompt configured in config.json.")
            print("      Add a 'feasibility_prompt' field describing what makes a job relevant.")
            return {}
        lines = [
            f"You are a job filter. For each job below, return a JSON array "
            f"of objects with 'index', 'verdict', and 'rationale' fields. "
            f"{self.prompt}\n"
            "The 'index' is the job number (1-based). The 'verdict' must be "
            "PREFERRED, YES, or NO. The 'rationale' is a brief one-sentence "
            "explanation.\n"
            "Return ONLY the JSON array. Do NOT wrap it in markdown code blocks. "
            'Example: [{"index": 1, "verdict": "PREFERRED", "rationale": "Big Tech engineering leadership."}]\n'
        ]
        for i, job in enumerate(jobs, 1):
            lines.append(
                f"{i}. Title: {job.get('title', '?')} | "
                f"Company: {job.get('company', '?')} | "
                f"Location: {job.get('location', '?')}"
            )
        prompt = "\n".join(lines)
        output, error = self.llm(
            prompt, model=self.model, timeout=self.timeout,
            workspace=self.workspace, retries=1,
            permission_mode="normal",
        )
        if error:
            print(f"  ⚠️  Feasibility batch failed: {error}")
            return {}

        # Parse JSON using the shared fence-tolerant parser
        from pipeline.infrastructure.llm_interface import parse_llm_json
        data = parse_llm_json(output)
        if data is None:
            preview = (output or "").strip()[:500]
            print(f"  ⚠️  Feasibility batch: JSON parse failed")
            print(f"      Raw output preview: {preview!r}")
            raise ValueError(
                f"Feasibility batch JSON parse failed. "
                f"Raw output: {preview!r}"
            )

        if not isinstance(data, list):
            raise ValueError(
                f"Feasibility batch: expected JSON array, got {type(data).__name__}"
            )

        # Validate each entry with Pydantic — strict, skip+log malformed (Q9=a)
        verdicts: dict[str, tuple[str, str]] = {}
        for entry in data:
            try:
                result = FeasibilityResult(**entry)
            except (ValidationError, TypeError) as e:
                print(f"  ⚠️  Skipping malformed feasibility entry: {e}")
                continue
            idx = result.index - 1  # 1-based → 0-based
            if 0 <= idx < len(jobs):
                url = jobs[idx].get("url", "")
                verdicts[url] = (result.verdict, result.rationale)

        # If we parsed zero verdicts from non-empty data, log for diagnosability
        if not verdicts and data:
            print(f"  ⚠️  Feasibility batch: {len(data)} entries but 0 valid verdicts.")

        return verdicts
