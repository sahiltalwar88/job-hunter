"""Job Hunter Pipeline — LangGraph-based job application automation package.

Structure:
    - infrastructure/  — core modules: config, state, paths, graph, lifecycle,
                         LLM interface, file ops, stores, schema, delta sync,
                         feasibility checker, notifications.
    - steps/           — pipeline step nodes (step1 through step10), one per
                         workflow stage. step9_veracity handles truthfulness review.
    - helpers/         — standalone CLI tools: count_lines, fetch_jds,
                         list_feasible_jobs, md_to_pdf, pii_scrub, serve,
                         smoke_test_pipeline.
    - __main__.py      — entry point (run via: python3 -m pipeline)

See docs/adr/ for architectural decisions.
"""
