"""Test feasibility_checker.py — DevinCLIChecker using the pipeline LLM protocol."""
import json
import pytest

from pipeline.infrastructure.feasibility_checker import (
    DevinCLIChecker,
    FeasibilityChecker,
    FeasibilityResult,
)
from pipeline.infrastructure.llm_interface import FakeLLM


SAMPLE_JOBS = [
    {"url": "https://linkedin.com/jobs/view/1", "title": "Director", "company": "Acme", "location": "SF"},
    {"url": "https://linkedin.com/jobs/view/2", "title": "VP", "company": "Beta", "location": "NYC"},
    {"url": "https://linkedin.com/jobs/view/3", "title": "Manager", "company": "Gamma", "location": "Remote"},
]


def _make_checker(llm_output: str | None, error: str | None = None,
                   prompt: str = "test prompt") -> DevinCLIChecker:
    """Build a checker with a FakeLLM that returns the given output."""
    llm = FakeLLM()
    if llm_output is not None:
        llm.add_response("You are a job filter", llm_output)
    if error:
        llm.add_response("You are a job filter", error)
    return DevinCLIChecker(llm=llm, prompt=prompt, model="test", timeout=1, workspace=".")


class TestDevinCLIChecker:
    def test_empty_jobs_returns_empty(self):
        checker = _make_checker("[]")
        assert checker.check_batch([]) == {}

    def test_empty_prompt_returns_empty(self):
        checker = _make_checker("[]", prompt="")
        result = checker.check_batch(SAMPLE_JOBS)
        assert result == {}

    def test_parses_json_verdicts(self):
        """Should parse JSON array with verdict + rationale."""
        llm_output = json.dumps([
            {"index": 1, "verdict": "PREFERRED", "rationale": "Big Tech fit."},
            {"index": 2, "verdict": "YES", "rationale": "Good profile match."},
            {"index": 3, "verdict": "NO", "rationale": "Wrong seniority."},
        ])
        checker = _make_checker(llm_output)
        verdicts = checker.check_batch(SAMPLE_JOBS)
        assert verdicts[SAMPLE_JOBS[0]["url"]] == ("PREFERRED", "Big Tech fit.")
        assert verdicts[SAMPLE_JOBS[1]["url"]] == ("YES", "Good profile match.")
        assert verdicts[SAMPLE_JOBS[2]["url"]] == ("NO", "Wrong seniority.")

    def test_parses_json_without_rationale(self):
        """Entries without rationale should default to empty string."""
        llm_output = json.dumps([
            {"index": 1, "verdict": "PREFERRED"},
            {"index": 2, "verdict": "YES"},
            {"index": 3, "verdict": "NO"},
        ])
        checker = _make_checker(llm_output)
        verdicts = checker.check_batch(SAMPLE_JOBS)
        assert verdicts[SAMPLE_JOBS[0]["url"]] == ("PREFERRED", "")
        assert verdicts[SAMPLE_JOBS[1]["url"]] == ("YES", "")
        assert verdicts[SAMPLE_JOBS[2]["url"]] == ("NO", "")

    def test_case_insensitive_verdicts(self):
        """Should normalize lowercase verdicts to uppercase."""
        llm_output = json.dumps([
            {"index": 1, "verdict": "preferred"},
            {"index": 2, "verdict": "yes"},
            {"index": 3, "verdict": "no"},
        ])
        checker = _make_checker(llm_output)
        verdicts = checker.check_batch(SAMPLE_JOBS)
        assert verdicts[SAMPLE_JOBS[0]["url"]] == ("PREFERRED", "")
        assert verdicts[SAMPLE_JOBS[1]["url"]] == ("YES", "")
        assert verdicts[SAMPLE_JOBS[2]["url"]] == ("NO", "")

    def test_partial_verdicts(self):
        """Jobs not in the LLM output should not be in the result dict."""
        llm_output = json.dumps([
            {"index": 1, "verdict": "PREFERRED", "rationale": "Great fit."},
        ])
        checker = _make_checker(llm_output)
        verdicts = checker.check_batch(SAMPLE_JOBS)
        assert len(verdicts) == 1
        assert verdicts[SAMPLE_JOBS[0]["url"]] == ("PREFERRED", "Great fit.")

    def test_llm_failure_returns_empty(self):
        """If the LLM returns an error, should return empty dict."""
        llm = FakeLLM()  # no responses — will return error
        checker = DevinCLIChecker(llm=llm, prompt="test prompt", model="test",
                                  timeout=1, workspace=".")
        verdicts = checker.check_batch(SAMPLE_JOBS)
        assert verdicts == {}

    def test_prompt_includes_job_details(self):
        """The prompt sent to the LLM should include title, company, location."""
        llm = FakeLLM()
        llm.add_response("You are a job filter", json.dumps([{"index": 1, "verdict": "YES"}]))
        checker = DevinCLIChecker(llm=llm, prompt="My custom prompt", model="test",
                                  timeout=1, workspace=".")
        checker.check_batch(SAMPLE_JOBS)
        prompt = llm.calls[0]
        assert "My custom prompt" in prompt
        assert "Director" in prompt
        assert "Acme" in prompt
        assert "SF" in prompt

    def test_prompt_requests_json(self):
        """The prompt should instruct the LLM to return JSON."""
        llm = FakeLLM()
        llm.add_response("You are a job filter", json.dumps([{"index": 1, "verdict": "YES"}]))
        checker = DevinCLIChecker(llm=llm, prompt="test prompt", model="test",
                                  timeout=1, workspace=".")
        checker.check_batch(SAMPLE_JOBS)
        prompt = llm.calls[0]
        assert "JSON" in prompt
        assert "index" in prompt
        assert "verdict" in prompt
        assert "rationale" in prompt

    def test_batch_size(self):
        assert DevinCLIChecker.BATCH_SIZE == 10

    def test_ignores_out_of_range_indices(self):
        """Index 99 should not cause a KeyError or IndexError."""
        llm_output = json.dumps([
            {"index": 99, "verdict": "YES"},
            {"index": 1, "verdict": "PREFERRED", "rationale": "Good."},
        ])
        checker = _make_checker(llm_output)
        verdicts = checker.check_batch(SAMPLE_JOBS)
        assert len(verdicts) == 1
        assert verdicts[SAMPLE_JOBS[0]["url"]] == ("PREFERRED", "Good.")

    def test_malformed_json_raises(self):
        """Non-JSON output should raise ValueError."""
        checker = _make_checker("Sorry, I can't help with that.")
        with pytest.raises(ValueError, match="(?i)json parse"):
            checker.check_batch(SAMPLE_JOBS)

    def test_empty_json_array_returns_empty(self):
        """Empty JSON array is valid — returns empty dict."""
        checker = _make_checker("[]")
        verdicts = checker.check_batch(SAMPLE_JOBS)
        assert verdicts == {}

    def test_strips_markdown_code_blocks(self):
        """JSON wrapped in ```json ... ``` should be parsed correctly."""
        llm_output = '```json\n[\n  {"index": 1, "verdict": "PREFERRED", "rationale": "Good."}\n]\n```'
        checker = _make_checker(llm_output)
        verdicts = checker.check_batch(SAMPLE_JOBS)
        assert verdicts[SAMPLE_JOBS[0]["url"]] == ("PREFERRED", "Good.")

    def test_strips_bare_code_blocks(self):
        """JSON wrapped in bare ``` ... ``` (no json tag) should parse."""
        llm_output = '```\n[{"index": 1, "verdict": "YES", "rationale": "Fit."}]\n```'
        checker = _make_checker(llm_output)
        verdicts = checker.check_batch(SAMPLE_JOBS)
        assert verdicts[SAMPLE_JOBS[0]["url"]] == ("YES", "Fit.")

    def test_skips_malformed_entries(self, capsys):
        """Entries with invalid verdict values should be skipped + logged."""
        llm_output = json.dumps([
            {"index": 1, "verdict": "PREFERRED", "rationale": "Good."},
            {"index": 2, "verdict": "MAYBE", "rationale": "Uncertain."},  # invalid
            {"index": 3, "verdict": "NO", "rationale": "Bad."},
        ])
        checker = _make_checker(llm_output)
        verdicts = checker.check_batch(SAMPLE_JOBS)
        assert len(verdicts) == 2  # entry 2 skipped
        assert SAMPLE_JOBS[0]["url"] in verdicts
        assert SAMPLE_JOBS[1]["url"] not in verdicts  # the MAYBE one
        assert SAMPLE_JOBS[2]["url"] in verdicts
        captured = capsys.readouterr()
        assert "malformed" in captured.out.lower() or "skipping" in captured.out.lower()

    def test_skips_entries_missing_index(self, capsys):
        """Entries missing the 'index' field should be skipped + logged."""
        llm_output = json.dumps([
            {"verdict": "PREFERRED", "rationale": "Good."},  # no index
            {"index": 1, "verdict": "YES", "rationale": "Fit."},
        ])
        checker = _make_checker(llm_output)
        verdicts = checker.check_batch(SAMPLE_JOBS)
        assert len(verdicts) == 1
        captured = capsys.readouterr()
        assert "malformed" in captured.out.lower() or "skipping" in captured.out.lower()

    def test_non_array_json_raises(self):
        """JSON object (not array) should raise ValueError."""
        checker = _make_checker('{"1": "PREFERRED"}')
        with pytest.raises(ValueError, match="(?i)expected.*array"):
            checker.check_batch(SAMPLE_JOBS)

    def test_logs_zero_valid_verdicts_warning(self, capsys):
        """When all entries are malformed, log a warning."""
        llm_output = json.dumps([
            {"index": 1, "verdict": "MAYBE"},
            {"index": 2, "verdict": "PERHAPS"},
        ])
        checker = _make_checker(llm_output)
        verdicts = checker.check_batch(SAMPLE_JOBS)
        assert verdicts == {}
        captured = capsys.readouterr()
        assert "0 valid verdicts" in captured.out


class TestFeasibilityResult:
    def test_valid_result(self):
        r = FeasibilityResult(index=1, verdict="PREFERRED", rationale="Good.")
        assert r.index == 1
        assert r.verdict == "PREFERRED"
        assert r.rationale == "Good."

    def test_rationale_defaults_empty(self):
        r = FeasibilityResult(index=1, verdict="YES")
        assert r.rationale == ""

    def test_lowercase_verdict_normalized(self):
        r = FeasibilityResult(index=1, verdict="preferred")
        assert r.verdict == "PREFERRED"

    def test_invalid_verdict_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FeasibilityResult(index=1, verdict="MAYBE")

    def test_missing_index_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FeasibilityResult(verdict="YES")


class TestFeasibilityCheckerABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            FeasibilityChecker()
