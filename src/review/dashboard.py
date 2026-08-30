"""Minimal Streamlit presentation for human review decisions."""

from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path

from src.integration.pipeline import run_integration

from src.review.review_api import (
    ApprovalError,
    ReviewItem,
    ReviewState,
    approve_candidate,
    can_approve,
    get_review_state,
    reject_candidate,
    select_candidate,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CANDIDATES_PATH = PROJECT_ROOT / "src" / "integration" / "tests" / "mock_candidates.json"
CANDIDATES_PATH_ENV = "SCHEMAGUARD_CANDIDATES_PATH"


def resolve_candidates_path(candidates_path: str | Path | None = None) -> Path:
    """Resolve an explicit, environment-configured, or demo candidates path."""

    if candidates_path is not None:
        return Path(candidates_path)
    return Path(os.environ.get(CANDIDATES_PATH_ENV, DEFAULT_CANDIDATES_PATH))


def load_dashboard_items(candidates_path: str | Path | None = None) -> tuple[ReviewItem, ...]:
    """Load and validate dashboard items through the existing integration layer."""

    path = resolve_candidates_path(candidates_path)
    result = run_integration(path)
    if result.error:
        raise ValueError(result.error)
    if result.review_state is None:
        return ()
    return result.review_state.items


def render_dashboard(
    items: Iterable[ReviewItem],
    *,
    state_key: str = "schemaguard_review_state",
) -> None:
    """Render review controls for precomputed review items."""

    import streamlit as st

    item_tuple = tuple(items)
    if state_key not in st.session_state:
        st.session_state[state_key] = get_review_state(item_tuple)
    state: ReviewState = st.session_state[state_key]

    st.title("SchemaGuard — Candidate Review")
    if not state.items:
        st.info("No review candidates loaded.")
        return

    for item in state.items:
        report = item.report
        sandbox = item.sandbox_result
        metrics = item.performance_metrics
        st.subheader(f"Candidate {item.candidate_id} — {item.status.value}")
        st.write(f"Rank: {report.ranked_position}")
        st.write(f"Confidence score: {report.confidence_score:.3f}")
        st.write(f"Schema validation: {'PASS' if report.schema_ok else 'FAIL'}")
        st.write(f"Data quality: {'PASS' if report.data_quality_ok else 'FAIL'}")
        st.write(
            "Sandbox: "
            + ("PASS" if sandbox is not None and sandbox.ran_successfully else "FAIL")
        )
        if metrics is not None:
            st.write(f"Execution time: {metrics.execution_time_ms:.3f} ms")
            st.write(
                f"Rows: {metrics.input_row_count} → {metrics.output_row_count}"
            )
        elif sandbox is not None:
            st.write(f"Execution time: {sandbox.execution_time_ms:.3f} ms")
            st.write(f"Rows: {sandbox.row_count_before} → {sandbox.row_count_after}")
        st.write(f"Explanation: {item.candidate.explanation}")
        st.code(item.candidate.code, language="python")

        select_key = f"select-{item.candidate_id}"
        reject_key = f"reject-{item.candidate_id}"
        approve_key = f"approve-{item.candidate_id}"
        if st.button(
            "Select Alternative", key=select_key, disabled=item.status.value == "REJECTED"
        ):
            st.session_state[state_key] = select_candidate(
                state, item.candidate_id
            )
            st.rerun()
        if st.button("Reject", key=reject_key):
            st.session_state[state_key] = reject_candidate(
                state, item.candidate_id
            )
            st.rerun()
        if st.button("Approve", key=approve_key, disabled=not can_approve(item)):
            try:
                st.session_state[state_key] = approve_candidate(
                    state, item.candidate_id
                )
            except ApprovalError as error:
                st.error(str(error))
            else:
                st.rerun()

    if state.approved_candidate_id is None:
        st.info("No candidate approved. Existing pipeline remains unchanged.")
    else:
        st.success(f"Approved candidate: {state.approved_candidate_id}")


def main() -> None:
    """Standalone dashboard entry point using the deterministic MVP fixture."""

    import streamlit as st

    st.set_page_config(page_title="SchemaGuard Candidate Review")
    configured_path = resolve_candidates_path()
    candidates_path = st.sidebar.text_input(
        "Candidates JSON path",
        value=str(configured_path),
        help=f"Defaults to {DEFAULT_CANDIDATES_PATH}",
    )
    try:
        items = load_dashboard_items(candidates_path)
    except ValueError as error:
        st.title("SchemaGuard — Candidate Review")
        st.error(f"Could not load candidates: {error}")
        return

    state_key = f"schemaguard_review_state:{Path(candidates_path).resolve()}"
    render_dashboard(items, state_key=state_key)


if __name__ == "__main__":
    main()
