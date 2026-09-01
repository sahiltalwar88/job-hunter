"""Tests for LangGraph + JobState integration.

Verifies that StateGraph(JobState) compiles, the Annotated[list, add]
reducer accumulates resume versions correctly, and result dicts
reconstruct to JobState.
"""
from pathlib import Path

from langgraph.graph import START, END, StateGraph

from pipeline.infrastructure.state import JobState, ResumeVersion


def test_graph_accumulates_resume_versions():
    """Two nodes each appending a version should result in 2 versions."""
    def add_version(state: JobState) -> dict:
        v = ResumeVersion(
            version=len(state.resume_versions) + 1,
            path=Path(f"drafts/{state.slug}/resume-v{len(state.resume_versions)+1}.md"),
        )
        return {"resume_versions": [v]}

    graph = StateGraph(JobState)
    graph.add_node("add_v1", add_version)
    graph.add_node("add_v2", add_version)
    graph.add_edge(START, "add_v1")
    graph.add_edge("add_v1", "add_v2")
    graph.add_edge("add_v2", END)
    compiled = graph.compile()

    result = compiled.invoke(JobState(slug="test-co"))
    state = JobState(**result)
    assert len(state.resume_versions) == 2
    assert state.resume_versions[0].version == 1
    assert state.resume_versions[1].version == 2
    assert state.latest_resume.version == 2


def test_graph_single_node():
    """A single node appending a version should work."""
    def add_version(state: JobState) -> dict:
        v = ResumeVersion(version=1, path=Path("drafts/test/resume-v1.md"))
        return {"resume_versions": [v]}

    graph = StateGraph(JobState)
    graph.add_node("add", add_version)
    graph.add_edge(START, "add")
    graph.add_edge("add", END)
    compiled = graph.compile()

    result = compiled.invoke(JobState(slug="test"))
    state = JobState(**result)
    assert len(state.resume_versions) == 1
    assert state.latest_resume.version == 1


def test_graph_preserves_other_fields():
    """Fields set in initial state should survive graph execution."""
    def noop(state: JobState) -> dict:
        return {}

    graph = StateGraph(JobState)
    graph.add_node("noop", noop)
    graph.add_edge(START, "noop")
    graph.add_edge("noop", END)
    compiled = graph.compile()

    initial = JobState(slug="test", url="https://example.com", company="TestCo")
    result = compiled.invoke(initial)
    state = JobState(**result)
    assert state.slug == "test"
    assert state.url == "https://example.com"
    assert state.company == "TestCo"
