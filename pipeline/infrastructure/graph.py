"""LangGraph builder — the per-job pipeline graph.

Wires all nodes (step3_ingest through step10 + terminal nodes) with
linear and conditional edges. The optimize loop (step6 -> step7 -> step8
-> back to step6 or forward to step9) is a real graph cycle.

Graph topology:

    START -> step3_ingest -> step4_grade_jd
                                |
                                +--[clearance] -> trash -> END
                                +--[graded] -> step5_triage
                                                |
                                                +--[trash] -> trash -> END
                                                +--[rejected_job_fit] -> rejected_job_fit -> END
                                                +--[drafts] -> step6_customize
                                                                v
                                                          step7_grade_resume
                                                                v
                                                          step8_optimize
                                                                |
                                                                +--[continue] -> step6_customize (cycle)
                                                                +--[done] -> step9_veracity
                                                                              |
                                                                              +--[verified] -> step10_ready -> END
                                                                              +--[unverified] -> rejected_resume -> END

The checkpointer (SqliteSaver) provides per-job resumability — an
interrupted hourly run resumes from the last checkpoint, keyed by
thread_id = slug.
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from pipeline.infrastructure.state import JobState, TriageDestination
from pipeline.steps.step10_finalize import (
    rejected_job_fit_node,
    rejected_resume_node,
    step10_ready_node,
    trash_node,
)
from pipeline.steps.step3_ingest import step3_ingest_node
from pipeline.steps.step4_grade_jd import step4_grade_jd_node
from pipeline.steps.step5_triage import step5_triage_node
from pipeline.steps.step6_customize import step6_customize_node
from pipeline.steps.step7_grade_resume import step7_grade_resume_node
from pipeline.steps.step8_optimize import step8_optimize_node
from pipeline.steps.step9_veracity import step9_veracity_node


# ─── Routing functions ──────────────────────────────────────────────────────


def route_after_jd_grade(state: JobState) -> str:
    """Route after JD grading: clearance -> trash, else -> triage."""
    if state.jd_grade and state.jd_grade.is_clearance:
        return "clearance"
    return "graded"


def route_triage(state: JobState) -> str:
    """Route after triage: read state.triage (set by step5_triage_node)."""
    if state.triage is None:
        return "trash"
    return state.triage.value


def route_optimize(state: JobState) -> str:
    """Route after optimize decision: continue (cycle) or done (forward).

    Reads state.optimize_can_improve (set by step8_optimize_node).
    True  -> continue (back to step6_customize)
    False -> done (forward to step9_veracity)
    """
    if state.optimize_can_improve is True:
        return "continue"
    return "done"


def route_after_truthfulness(state: JobState) -> str:
    """Route after truthfulness review: verified -> ready, else -> rejected.

    Reads state.verification (set by step9_veracity_node).
    """
    if state.verification and state.verification.verified:
        return "verified"
    return "unverified"


# ─── Graph builder ──────────────────────────────────────────────────────────


def build_job_graph(checkpointer: Any = None):
    """Build and compile the per-job LangGraph pipeline.

    Args:
        checkpointer: A LangGraph checkpointer (e.g. SqliteSaver) for
            per-job resumability. Pass None for tests (no checkpointing).

    Returns:
        A compiled StateGraph ready to invoke with:
            graph.invoke(initial_state, config={"configurable": {...},
                                                "thread_id": slug})
    """
    graph = StateGraph(JobState)

    # ── Add nodes ──
    graph.add_node("step3_ingest", step3_ingest_node)
    graph.add_node("step4_grade_jd", step4_grade_jd_node)
    graph.add_node("step5_triage", step5_triage_node)
    graph.add_node("step6_customize", step6_customize_node)
    graph.add_node("step7_grade_resume", step7_grade_resume_node)
    graph.add_node("step8_optimize", step8_optimize_node)
    graph.add_node("step9_veracity", step9_veracity_node)
    graph.add_node("step10_ready", step10_ready_node)
    graph.add_node("trash", trash_node)
    graph.add_node("rejected_job_fit", rejected_job_fit_node)
    graph.add_node("rejected_resume", rejected_resume_node)

    # ── Linear edges ──
    graph.add_edge(START, "step3_ingest")
    graph.add_edge("step3_ingest", "step4_grade_jd")
    graph.add_edge("step6_customize", "step7_grade_resume")
    graph.add_edge("step7_grade_resume", "step8_optimize")
    graph.add_edge("step10_ready", END)
    graph.add_edge("trash", END)
    graph.add_edge("rejected_job_fit", END)
    graph.add_edge("rejected_resume", END)

    # ── Conditional edges ──
    graph.add_conditional_edges(
        "step4_grade_jd",
        route_after_jd_grade,
        {
            "clearance": "trash",
            "graded": "step5_triage",
        },
    )
    graph.add_conditional_edges(
        "step5_triage",
        route_triage,
        {
            "trash": "trash",
            "rejected_job_fit": "rejected_job_fit",
            "drafts": "step6_customize",
        },
    )
    graph.add_conditional_edges(
        "step8_optimize",
        route_optimize,
        {
            "continue": "step6_customize",
            "done": "step9_veracity",
        },
    )
    graph.add_conditional_edges(
        "step9_veracity",
        route_after_truthfulness,
        {
            "verified": "step10_ready",
            "unverified": "rejected_resume",
        },
    )

    return graph.compile(checkpointer=checkpointer)
