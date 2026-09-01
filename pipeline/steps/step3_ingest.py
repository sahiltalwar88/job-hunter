"""Step 3 (per-job): Ingest — create listing folder + JD file.

This is a LangGraph node. It creates listings/<slug>/[TBD] job-description.md
with the JD text and URL metadata comment. No clearance check here (ADR-0007:
clearance is detected by the LLM at step4_grade_jd).
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from pipeline.infrastructure.llm_interface import get_deps
from pipeline.infrastructure.state import JobState
from pipeline.infrastructure.file_ops import company_role_slug, create_jd_file


def step3_ingest_node(state: JobState, config: RunnableConfig) -> dict:
    """Create the listing folder and JD file for this job.

    Returns partial state with jd_path set.
    """
    deps = get_deps(config)
    slug = state.slug

    # If slug is empty, derive from company + title
    if not slug and (state.company or state.title):
        slug = company_role_slug(state.company, state.title)

    if not slug:
        deps.logger.error("Cannot ingest: no slug and no company/title to derive one")
        return {"error": "No slug or company/title provided"}

    jd_text = state.jd_text
    if not jd_text and state.jd_path and state.jd_path.exists():
        jd_text = state.jd_path.read_text(encoding="utf-8")

    jd_path = create_jd_file(
        slug=slug,
        jd_text=jd_text,
        url=state.url,
        dest_dir=deps.paths.listings,
        dry_run=deps.config.dry_run,
    )

    deps.logger.info(f"  {slug}: ingested → listings/")
    return {"slug": slug, "jd_path": jd_path, "jd_text": jd_text}
