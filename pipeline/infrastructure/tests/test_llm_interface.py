"""Tests for pipeline.llm — LLM protocol, NodeDeps, get_deps, RealLLM.

FakeLLM is test infrastructure (30 lines, substring matching). Its behavior
is implicitly verified by every pipeline test that uses it successfully —
it doesn't need its own unit tests.
"""
import pytest

from pipeline.infrastructure.config import PipelineConfig
from pipeline.infrastructure.llm_interface import FakeLLM, NodeDeps, RealLLM, get_deps, parse_llm_json
from pipeline.infrastructure.paths import Paths
from pipeline.infrastructure.config import DEFAULT_LLM_MODEL


def test_node_deps_is_frozen(fake_llm, logger, config, tmp_paths):
    deps = NodeDeps(llm=fake_llm, logger=logger, config=config, paths=tmp_paths)
    with pytest.raises(Exception):
        deps.llm = FakeLLM()


def test_get_deps_extracts_from_config(fake_llm, logger, config, tmp_paths):
    runnable_config = {
        "configurable": {
            "llm": fake_llm,
            "logger": logger,
            "config": config,
            "paths": tmp_paths,
        }
    }
    deps = get_deps(runnable_config)
    assert deps.llm == fake_llm
    assert deps.logger == logger
    assert deps.config == config
    assert deps.paths == tmp_paths


def test_real_llm_is_callable():
    real = RealLLM()
    assert callable(real)


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_real_llm_dispatches_to_configured_provider(monkeypatch, provider):
    captured = {}

    def fake_call_llm_safe(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return f"{provider} output", None

    import sys
    import types

    module_name = f"pipeline.infrastructure.{provider}_cli"
    fake_module = types.ModuleType(module_name)
    fake_module.call_llm_safe = fake_call_llm_safe
    monkeypatch.setitem(sys.modules, module_name, fake_module)

    output, error = RealLLM(provider=provider)(
        "test prompt", model="default", timeout=1, workspace="."
    )

    assert output == f"{provider} output"
    assert error is None
    assert captured["prompt"] == "test prompt"


def test_real_llm_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        RealLLM(provider="other")


# ─── permission_mode pass-through (ADR-0010) ────────────────────────────────


def test_real_llm_passes_permission_mode(monkeypatch):
    """RealLLM should pass permission_mode to devin_cli.call_llm_safe."""
    captured = {}

    def fake_call_llm_safe(prompt, **kwargs):
        captured.update(kwargs)
        return "output", None

    # devin_cli is imported lazily inside RealLLM.__call__, so patch the module
    # that gets imported. We inject a fake module into sys.modules.
    import sys
    import types

    fake_module = types.ModuleType("pipeline.infrastructure.devin_cli")
    fake_module.call_llm_safe = fake_call_llm_safe
    monkeypatch.setitem(sys.modules, "pipeline.infrastructure.devin_cli", fake_module)

    real = RealLLM()
    real("test prompt", model="x", timeout=1, workspace=".", permission_mode="normal")

    assert captured.get("permission_mode") == "normal"


def test_real_llm_defaults_permission_mode_dangerous(monkeypatch):
    """RealLLM should default permission_mode to 'dangerous' (backward compat)."""
    captured = {}

    def fake_call_llm_safe(prompt, **kwargs):
        captured.update(kwargs)
        return "output", None

    import sys
    import types

    fake_module = types.ModuleType("pipeline.infrastructure.devin_cli")
    fake_module.call_llm_safe = fake_call_llm_safe
    monkeypatch.setitem(sys.modules, "pipeline.infrastructure.devin_cli", fake_module)

    real = RealLLM()
    real("test prompt", model="x", timeout=1, workspace=".")

    assert captured.get("permission_mode") == "dangerous"


def test_real_llm_passes_config_path(monkeypatch):
    """RealLLM should pass config_path to devin_cli.call_llm_safe."""
    captured = {}

    def fake_call_llm_safe(prompt, **kwargs):
        captured.update(kwargs)
        return "output", None

    import sys
    import types

    fake_module = types.ModuleType("pipeline.infrastructure.devin_cli")
    fake_module.call_llm_safe = fake_call_llm_safe
    monkeypatch.setitem(sys.modules, "pipeline.infrastructure.devin_cli", fake_module)

    real = RealLLM()
    real("test", model="x", timeout=1, workspace=".", config_path="/cfg.json")

    assert captured.get("config_path") == "/cfg.json"


# ─── LLM call audit logging (W7) ─────────────────────────────────────────────


def test_real_llm_logs_to_audit_table(monkeypatch, tmp_path):
    """RealLLM with a db_path logs every call to the llm_calls table."""
    def fake_call_llm_safe(prompt, **kwargs):
        return "GRADE: 8.5", None

    import sys
    import types

    fake_module = types.ModuleType("pipeline.infrastructure.devin_cli")
    fake_module.call_llm_safe = fake_call_llm_safe
    monkeypatch.setitem(sys.modules, "pipeline.infrastructure.devin_cli", fake_module)

    db = str(tmp_path / "jobs.db")
    real = RealLLM(db_path=db)
    real("Grade this JD", model=DEFAULT_LLM_MODEL, timeout=120, workspace=".",
         job_slug="google-engineer", step="grade_jd")

    from pipeline.infrastructure.audit_store import AuditStore
    audit = AuditStore(db)
    calls = audit.query_llm_calls("google-engineer")
    audit.close()
    assert len(calls) == 1
    call = calls[0]
    assert call["step"] == "grade_jd"
    assert call["model"] == DEFAULT_LLM_MODEL
    assert call["prompt"] == "Grade this JD"
    assert call["response"] == "GRADE: 8.5"
    assert call["job_slug"] == "google-engineer"
    assert call["duration_ms"] is not None
    assert call["duration_ms"] >= 0
    assert call["call_id"] == "grade_jd#1"
    assert call["status"] == "ok"
    assert call["error"] is None


def test_real_llm_logs_error_response(monkeypatch, tmp_path):
    """RealLLM logs the error string as response when the call fails."""
    from pipeline.infrastructure.devin_cli import LLMError

    def fake_call_llm_safe(prompt, **kwargs):
        return None, LLMError("timeout")

    import sys
    import types

    fake_module = types.ModuleType("pipeline.infrastructure.devin_cli")
    fake_module.call_llm_safe = fake_call_llm_safe
    monkeypatch.setitem(sys.modules, "pipeline.infrastructure.devin_cli", fake_module)

    db = str(tmp_path / "jobs.db")
    real = RealLLM(db_path=db)
    out, err = real("test", model="x", timeout=1, workspace=".",
                    job_slug="co", step="grade_resume")
    assert out is None
    assert err is not None

    from pipeline.infrastructure.audit_store import AuditStore
    audit = AuditStore(db)
    calls = audit.query_llm_calls("co")
    audit.close()
    assert len(calls) == 1
    assert calls[0]["response"] is None
    assert calls[0]["status"] == "error"
    assert calls[0]["error"] is not None
    assert "timeout" in calls[0]["error"]


def test_real_llm_no_db_path_skips_logging(monkeypatch):
    """RealLLM without a db_path doesn't log (and doesn't error)."""
    def fake_call_llm_safe(prompt, **kwargs):
        return "output", None

    import sys
    import types

    fake_module = types.ModuleType("pipeline.infrastructure.devin_cli")
    fake_module.call_llm_safe = fake_call_llm_safe
    monkeypatch.setitem(sys.modules, "pipeline.infrastructure.devin_cli", fake_module)

    real = RealLLM()  # no db_path
    out, err = real("test", model="x", timeout=1, workspace=".",
                    job_slug="co", step="test")
    assert out == "output"
    assert err is None


def test_real_llm_log_failure_doesnt_affect_call(monkeypatch, tmp_path):
    """If audit logging fails, the LLM call result is unaffected."""
    def fake_call_llm_safe(prompt, **kwargs):
        return "output", None

    import sys
    import types

    fake_module = types.ModuleType("pipeline.infrastructure.devin_cli")
    fake_module.call_llm_safe = fake_call_llm_safe
    monkeypatch.setitem(sys.modules, "pipeline.infrastructure.devin_cli", fake_module)

    # Point at a path that can't be created — audit logging will fail silently
    real = RealLLM(db_path="/nonexistent/dir/jobs.db")
    out, err = real("test", model="x", timeout=1, workspace=".",
                    job_slug="co", step="test")
    assert out == "output"
    assert err is None


# ─── RealLLM timing recording ──────────────────────────────────────────────


def test_real_llm_records_timing_on_success(monkeypatch):
    """RealLLM.records timing data in self.calls on a successful call."""
    def fake_call_llm_safe(prompt, **kwargs):
        return "output", None

    import sys
    import types

    fake_module = types.ModuleType("pipeline.infrastructure.devin_cli")
    fake_module.call_llm_safe = fake_call_llm_safe
    monkeypatch.setitem(sys.modules, "pipeline.infrastructure.devin_cli", fake_module)

    real = RealLLM()
    real("test prompt", model=DEFAULT_LLM_MODEL, timeout=120, workspace=".",
         job_slug="acme", step="grade-resume")

    assert len(real.calls) == 1
    entry = real.calls[0]
    assert entry["step"] == "grade-resume"
    assert entry["call_index"] == 1
    assert entry["call_id"] == "grade-resume#1"
    assert entry["timeout_s"] == 120
    assert entry["status"] == "ok"
    assert entry["model"] == DEFAULT_LLM_MODEL
    assert entry["job_slug"] == "acme"
    assert entry["duration_s"] >= 0
    assert real.last_output == "output"


def test_real_llm_records_timing_on_error(monkeypatch):
    """RealLLM.records timing data in self.calls even on error."""
    from pipeline.infrastructure.devin_cli import LLMError

    def fake_call_llm_safe(prompt, **kwargs):
        return None, LLMError("timed out")

    import sys
    import types

    fake_module = types.ModuleType("pipeline.infrastructure.devin_cli")
    fake_module.call_llm_safe = fake_call_llm_safe
    monkeypatch.setitem(sys.modules, "pipeline.infrastructure.devin_cli", fake_module)

    real = RealLLM()
    out, err = real("test", model="x", timeout=30, workspace=".",
                    job_slug="co", step="feasibility")
    assert out is None
    assert err is not None

    assert len(real.calls) == 1
    entry = real.calls[0]
    assert entry["step"] == "feasibility"
    assert entry["status"].startswith("error:")
    assert "timed out" in entry["status"]
    assert real.last_output is None


def test_real_llm_tracks_per_step_call_index(monkeypatch):
    """RealLLM tracks per-step call indices across multiple calls."""
    def fake_call_llm_safe(prompt, **kwargs):
        return "output", None

    import sys
    import types

    fake_module = types.ModuleType("pipeline.infrastructure.devin_cli")
    fake_module.call_llm_safe = fake_call_llm_safe
    monkeypatch.setitem(sys.modules, "pipeline.infrastructure.devin_cli", fake_module)

    real = RealLLM()
    # Two calls for grade-jd, one for customize
    real("p1", model="x", timeout=1, workspace=".", step="grade-jd")
    real("p2", model="x", timeout=1, workspace=".", step="grade-jd")
    real("p3", model="x", timeout=1, workspace=".", step="customize")

    assert len(real.calls) == 3
    assert real.calls[0]["call_id"] == "grade-jd#1"
    assert real.calls[1]["call_id"] == "grade-jd#2"
    assert real.calls[2]["call_id"] == "customize#1"


def test_real_llm_step_defaults_to_unknown(monkeypatch):
    """RealLLM uses 'unknown' as step label when step is not provided."""
    def fake_call_llm_safe(prompt, **kwargs):
        return "output", None

    import sys
    import types

    fake_module = types.ModuleType("pipeline.infrastructure.devin_cli")
    fake_module.call_llm_safe = fake_call_llm_safe
    monkeypatch.setitem(sys.modules, "pipeline.infrastructure.devin_cli", fake_module)

    real = RealLLM()
    real("test", model="x", timeout=1, workspace=".")

    assert len(real.calls) == 1
    assert real.calls[0]["step"] == "unknown"
    assert real.calls[0]["call_id"] == "unknown#1"


# ─── RealLLM export_dir auto-generation ─────────────────────────────────────


def test_real_llm_auto_generates_export_path(monkeypatch, tmp_path):
    """RealLLM with export_dir auto-generates export_path when not provided."""
    captured = {}

    def fake_call_llm_safe(prompt, **kwargs):
        captured.update(kwargs)
        return "output", None

    import sys
    import types

    fake_module = types.ModuleType("pipeline.infrastructure.devin_cli")
    fake_module.call_llm_safe = fake_call_llm_safe
    monkeypatch.setitem(sys.modules, "pipeline.infrastructure.devin_cli", fake_module)

    export_dir = tmp_path / "exports"
    real = RealLLM(export_dir=str(export_dir))
    real("test", model="x", timeout=1, workspace=".",
         job_slug="acme-co", step="grade-jd")

    generated = captured.get("export_path")
    assert generated is not None
    assert "acme-co" in generated
    assert "grade-jd-1" in generated
    assert export_dir.exists()
    assert (export_dir / "acme-co").exists()


def test_real_llm_explicit_export_path_overrides_auto(monkeypatch, tmp_path):
    """Explicit export_path takes precedence over auto-generation."""
    captured = {}

    def fake_call_llm_safe(prompt, **kwargs):
        captured.update(kwargs)
        return "output", None

    import sys
    import types

    fake_module = types.ModuleType("pipeline.infrastructure.devin_cli")
    fake_module.call_llm_safe = fake_call_llm_safe
    monkeypatch.setitem(sys.modules, "pipeline.infrastructure.devin_cli", fake_module)

    explicit_path = str(tmp_path / "explicit.json")
    real = RealLLM(export_dir=str(tmp_path / "exports"))
    real("test", model="x", timeout=1, workspace=".",
         job_slug="acme", step="grade-jd", export_path=explicit_path)

    assert captured["export_path"] == explicit_path


def test_real_llm_no_export_dir_means_no_export_path(monkeypatch):
    """Without export_dir, export_path stays None (no auto-generation)."""
    captured = {}

    def fake_call_llm_safe(prompt, **kwargs):
        captured.update(kwargs)
        return "output", None

    import sys
    import types

    fake_module = types.ModuleType("pipeline.infrastructure.devin_cli")
    fake_module.call_llm_safe = fake_call_llm_safe
    monkeypatch.setitem(sys.modules, "pipeline.infrastructure.devin_cli", fake_module)

    real = RealLLM()  # no export_dir
    real("test", model="x", timeout=1, workspace=".",
         job_slug="acme", step="grade-jd")

    assert captured.get("export_path") is None


def test_real_llm_export_path_increments_per_step(monkeypatch, tmp_path):
    """Auto-generated export_path increments call index per step."""
    captured = []

    def fake_call_llm_safe(prompt, **kwargs):
        captured.append(kwargs.get("export_path"))
        return "output", None

    import sys
    import types

    fake_module = types.ModuleType("pipeline.infrastructure.devin_cli")
    fake_module.call_llm_safe = fake_call_llm_safe
    monkeypatch.setitem(sys.modules, "pipeline.infrastructure.devin_cli", fake_module)

    real = RealLLM(export_dir=str(tmp_path / "exports"))
    real("p1", model="x", timeout=1, workspace=".", job_slug="co", step="grade-jd")
    real("p2", model="x", timeout=1, workspace=".", job_slug="co", step="grade-jd")
    real("p3", model="x", timeout=1, workspace=".", job_slug="co", step="customize")

    assert "grade-jd-1" in captured[0]
    assert "grade-jd-2" in captured[1]
    assert "customize-1" in captured[2]


# ─── parse_llm_json (shared LLM output parser) ──────────────────────────────


def test_parse_llm_json_plain():
    """Plain JSON (no fences) parses correctly."""
    result = parse_llm_json('{"grade": 9.5, "ceiling": 10.0}')
    assert result is not None
    assert result["grade"] == 9.5


def test_parse_llm_json_markdown_fences():
    """JSON wrapped in ```json ... ``` fences parses correctly."""
    result = parse_llm_json('```json\n{"verified": true, "claims": []}\n```')
    assert result is not None
    assert result["verified"] is True


def test_parse_llm_json_bare_fences():
    """JSON wrapped in bare ``` fences (no language tag) parses correctly."""
    result = parse_llm_json('```\n{"verified": false}\n```')
    assert result is not None
    assert result["verified"] is False


def test_parse_llm_json_embedded_in_text():
    """JSON embedded in surrounding text is extracted via { ... } fallback."""
    result = parse_llm_json('Here is the grade:\n{"grade": 8.5}\nDone.')
    assert result is not None
    assert result["grade"] == 8.5


def test_parse_llm_json_none_input():
    """None input returns None."""
    assert parse_llm_json(None) is None


def test_parse_llm_json_empty_string():
    """Empty string returns None."""
    assert parse_llm_json("") is None


def test_parse_llm_json_invalid_json():
    """Non-JSON text returns None."""
    assert parse_llm_json("not json at all") is None


def test_parse_llm_json_extra_trailing_brace():
    """LLMs sometimes add an extra `}` at the end. The parser should trim
    trailing close-chars and recover the valid JSON inside."""
    result = parse_llm_json('{"per_criterion": [{"a": 1}]}}')
    assert result is not None
    assert result["per_criterion"] == [{"a": 1}]


def test_parse_llm_json_extra_trailing_bracket():
    """Same recovery for arrays with extra trailing `]`."""
    result = parse_llm_json('{"items": [1, 2, 3]}]')
    assert result is not None
    assert result["items"] == [1, 2, 3]
