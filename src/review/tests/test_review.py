import unittest

from src.common.models import RepairCandidate, SandboxResult, ValidationReport
from src.validator.data_quality import DataQualityResult
from src.validator.performance import compute_performance_metrics
from src.review.review_api import (
    ApprovalError,
    ReviewStatus,
    approve_candidate,
    build_review_items,
    can_approve,
    get_review_state,
    reject_candidate,
    select_candidate,
)


def make_candidate(candidate_id: str = "candidate-1") -> RepairCandidate:
    return RepairCandidate(
        id=candidate_id,
        code="def transform(row):\n    return row",
        explanation="Deterministic test candidate",
    )


def make_report(
    candidate_id: str = "candidate-1",
    *,
    schema_ok: bool = True,
    data_quality_ok: bool = True,
    confidence_score: float = 1.0,
    ranked_position: int = 1,
) -> ValidationReport:
    return ValidationReport(
        candidate_id=candidate_id,
        schema_ok=schema_ok,
        data_quality_ok=data_quality_ok,
        confidence_score=confidence_score,
        ranked_position=ranked_position,
    )


def make_sandbox(candidate_id: str = "candidate-1", *, success: bool = True):
    return SandboxResult(candidate_id, success, 2, 2 if success else 0, 2.5)


def make_quality(candidate_id: str = "candidate-1", *, passed: bool = True):
    return DataQualityResult(
        candidate_id=candidate_id,
        data_quality_ok=passed,
        row_count_ok=passed,
        required_columns_ok=passed,
        null_values_ok=passed,
        duplicate_ids_ok=passed,
        schema_ok=passed,
    )


class ReviewApiTests(unittest.TestCase):
    def make_items(
        self,
        *,
        report: ValidationReport | None = None,
        sandbox: SandboxResult | None = None,
    ):
        candidate = make_candidate()
        report = report or make_report()
        sandbox = sandbox or make_sandbox()
        performance = compute_performance_metrics(sandbox)
        return build_review_items([candidate], [report], [sandbox], [performance])

    def test_pending_candidate_starts_as_pending(self):
        items = self.make_items()

        state = get_review_state(items)

        self.assertEqual(state.items[0].status, ReviewStatus.PENDING)
        self.assertIsNone(state.approved_candidate_id)

    def test_valid_candidate_can_be_approved(self):
        state = get_review_state(self.make_items())

        approved = approve_candidate(state, "candidate-1")

        self.assertEqual(approved.approved_candidate_id, "candidate-1")
        self.assertEqual(approved.items[0].status, ReviewStatus.APPROVED)

    def test_invalid_candidate_cannot_be_approved(self):
        invalid = self.make_items(report=make_report(data_quality_ok=False))

        with self.assertRaises(ApprovalError):
            approve_candidate(get_review_state(invalid), "candidate-1")

    def test_rejected_candidate_gets_rejected_state(self):
        state = get_review_state(self.make_items())

        rejected = reject_candidate(state, "candidate-1")

        self.assertEqual(rejected.items[0].status, ReviewStatus.REJECTED)
        self.assertIsNone(rejected.approved_candidate_id)

    def test_selecting_an_alternative_candidate_works(self):
        candidates = [make_candidate("first"), make_candidate("second")]
        reports = [make_report("first", ranked_position=1), make_report("second", ranked_position=2)]
        sandboxes = [make_sandbox("first"), make_sandbox("second")]
        performances = [compute_performance_metrics(result) for result in sandboxes]
        state = get_review_state(
            build_review_items(candidates, reports, sandboxes, performances)
        )

        selected = select_candidate(state, "second")

        self.assertEqual(selected.selected_candidate_id, "second")
        self.assertEqual(selected.items[1].status, ReviewStatus.PENDING)

    def test_only_one_candidate_can_be_final_approved(self):
        candidates = [make_candidate("first"), make_candidate("second")]
        reports = [make_report("first", ranked_position=1), make_report("second", ranked_position=2)]
        sandboxes = [make_sandbox("first"), make_sandbox("second")]
        items = build_review_items(candidates, reports, sandboxes)
        state = approve_candidate(get_review_state(items), "first")

        state = approve_candidate(state, "second")
        approved = [item for item in state.items if item.status == ReviewStatus.APPROVED]

        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0].candidate_id, "second")
        self.assertEqual(state.approved_candidate_id, "second")

    def test_rejecting_all_candidates_leaves_no_approved_candidate(self):
        candidates = [make_candidate("first"), make_candidate("second")]
        reports = [make_report("first"), make_report("second", ranked_position=2)]
        sandboxes = [make_sandbox("first"), make_sandbox("second")]
        state = get_review_state(build_review_items(candidates, reports, sandboxes))

        state = reject_candidate(state, "first")
        state = reject_candidate(state, "second")

        self.assertIsNone(state.approved_candidate_id)
        self.assertTrue(all(item.status == ReviewStatus.REJECTED for item in state.items))

    def test_failed_schema_validation_prevents_approval(self):
        item = self.make_items(report=make_report(schema_ok=False))[0]

        self.assertFalse(can_approve(item))
        with self.assertRaises(ApprovalError):
            approve_candidate(get_review_state([item]), item.candidate_id)

    def test_failed_data_quality_validation_prevents_approval(self):
        item = self.make_items(report=make_report(data_quality_ok=False))[0]

        self.assertFalse(can_approve(item))
        with self.assertRaises(ApprovalError):
            approve_candidate(get_review_state([item]), item.candidate_id)

    def test_failed_sandbox_validation_prevents_approval(self):
        item = self.make_items(sandbox=make_sandbox(success=False))[0]

        self.assertFalse(can_approve(item))
        with self.assertRaises(ApprovalError):
            approve_candidate(get_review_state([item]), item.candidate_id)

    def test_candidate_id_is_preserved(self):
        candidate = make_candidate("preserved")
        report = make_report("preserved")
        sandbox = make_sandbox("preserved")
        item = build_review_items(
            [candidate], [report], [sandbox], [compute_performance_metrics(sandbox)]
        )[0]

        self.assertEqual(item.candidate_id, "preserved")
        self.assertEqual(item.report.candidate_id, "preserved")
        self.assertEqual(item.sandbox_result.candidate_id, "preserved")

    def test_ranking_and_confidence_are_preserved(self):
        item = self.make_items(report=make_report(confidence_score=0.75, ranked_position=4))[0]

        self.assertEqual(item.report.confidence_score, 0.75)
        self.assertEqual(item.report.ranked_position, 4)

    def test_existing_validation_objects_are_not_mutated(self):
        candidate = make_candidate()
        report = make_report(ranked_position=7)
        sandbox = make_sandbox()
        state = get_review_state(
            build_review_items([candidate], [report], [sandbox])
        )

        approved = approve_candidate(state, candidate.id)

        self.assertIsNot(approved.items[0], state.items[0])
        self.assertEqual(report.ranked_position, 7)
        self.assertEqual(sandbox.row_count_after, 2)
        self.assertFalse(candidate.passed_sandbox)

    def test_empty_candidate_list_is_handled(self):
        self.assertEqual(build_review_items([], []), ())
        self.assertEqual(get_review_state([]).items, ())

    def test_selection_of_unknown_candidate_fails(self):
        with self.assertRaises(KeyError):
            select_candidate(get_review_state(self.make_items()), "unknown")


if __name__ == "__main__":
    unittest.main()
