"""Tests for pipeline.llm — LLM protocol, NodeDeps, get_deps, RealLLM.

FakeLLM is test infrastructure (30 lines, substring matching). Its behavior
is implicitly verified by every pipeline test that uses it successfully —
it doesn't need its own unit tests.
"""
import pytest

from pipeline.infrastructure.config import PipelineConfig
from pipeline.infrastructure.llm_interface import FakeLLM, NodeDeps, RealLLM, get_deps, parse_llm_json
from pipeline.infrastructure.paths import Paths


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
    real("Grade this JD", model="customizer-model", timeout=120, workspace=".",
         job_slug="google-engineer", step="grade_jd")

    from pipeline.infrastructure.audit_store import AuditStore
    audit = AuditStore(db)
    calls = audit.query_llm_calls("google-engineer")
    audit.close()
    assert len(calls) == 1
    call = calls[0]
    assert call["step"] == "grade_jd"
    assert call["model"] == "customizer-model"
    assert call["prompt"] == "Grade this JD"
    assert call["response"] == "GRADE: 8.5"
    assert call["job_slug"] == "google-engineer"
    assert call["duration_ms"] is not None
    assert call["duration_ms"] >= 0


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
    assert calls[0]["response"] is not None
    assert "timeout" in calls[0]["response"]


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
