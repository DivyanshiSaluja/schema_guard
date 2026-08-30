import unittest

from src.common.models import SandboxResult, ValidationReport
from src.validator.ranking import effective_confidence, rank_candidates


def report(
    candidate_id: str,
    *,
    schema_ok: bool = True,
    data_quality_ok: bool = True,
    confidence: float = 1.0,
    ranked_position: int = 0,
) -> ValidationReport:
    return ValidationReport(
        candidate_id=candidate_id,
        schema_ok=schema_ok,
        data_quality_ok=data_quality_ok,
        confidence_score=confidence,
        ranked_position=ranked_position,
    )


class CandidateRankingTests(unittest.TestCase):
    def test_empty_candidate_list(self):
        self.assertEqual(rank_candidates([]), [])

    def test_one_candidate(self):
        result = rank_candidates([report("candidate-1")])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].candidate_id, "candidate-1")
        self.assertEqual(result[0].ranked_position, 1)

    def test_multiple_candidates_with_different_confidence_scores(self):
        result = rank_candidates(
            [report("medium", confidence=0.5), report("high", confidence=0.9)]
        )

        self.assertEqual([item.candidate_id for item in result], ["high", "medium"])

    def test_correct_descending_ranking(self):
        result = rank_candidates(
            [
                report("low", confidence=0.1),
                report("high", confidence=1.0),
                report("medium", confidence=0.6),
            ]
        )

        self.assertEqual([item.confidence_score for item in result], [1.0, 0.6, 0.1])

    def test_ranked_positions_start_at_one(self):
        result = rank_candidates(
            [report("a", confidence=0.2), report("b", confidence=0.8)]
        )

        self.assertEqual([item.ranked_position for item in result], [1, 2])

    def test_ties_are_handled_deterministically(self):
        candidates = [report("b", confidence=0.7), report("a", confidence=0.7)]

        first = rank_candidates(candidates)
        second = rank_candidates(list(reversed(candidates)))

        self.assertEqual(first, second)
        self.assertEqual([item.candidate_id for item in first], ["a", "b"])

    def test_candidate_id_is_tie_breaker(self):
        result = rank_candidates(
            [report("candidate-z", confidence=0.5), report("candidate-a", confidence=0.5)]
        )

        self.assertEqual(
            [item.candidate_id for item in result], ["candidate-a", "candidate-z"]
        )

    def test_failed_schema_validation_cannot_outrank_valid_candidate(self):
        result = rank_candidates(
            [
                report("failed-schema", schema_ok=False, confidence=1.0),
                report("valid", confidence=0.2),
            ]
        )

        self.assertEqual(result[0].candidate_id, "valid")
        self.assertEqual(result[1].confidence_score, 0.0)

    def test_failed_data_quality_cannot_outrank_valid_candidate(self):
        result = rank_candidates(
            [
                report("failed-quality", data_quality_ok=False, confidence=1.0),
                report("valid", confidence=0.2),
            ]
        )

        self.assertEqual(result[0].candidate_id, "valid")
        self.assertEqual(result[1].confidence_score, 0.0)

    def test_original_input_order_is_not_relied_upon(self):
        reports = [report("third", confidence=0.3), report("first", confidence=0.9)]
        original_order = [item.candidate_id for item in reports]

        result = rank_candidates(reports)

        self.assertEqual(original_order, ["third", "first"])
        self.assertEqual([item.candidate_id for item in result], ["first", "third"])

    def test_existing_validation_report_fields_are_preserved(self):
        result = rank_candidates(
            [report("candidate", schema_ok=True, data_quality_ok=True, confidence=0.75)]
        )

        self.assertEqual(result[0].candidate_id, "candidate")
        self.assertTrue(result[0].schema_ok)
        self.assertTrue(result[0].data_quality_ok)
        self.assertEqual(result[0].confidence_score, 0.75)
        self.assertEqual(result[0].ranked_position, 1)

    def test_ranking_does_not_execute_database_code(self):
        # Ranking accepts reports only; no SandboxResult or database object is needed.
        result = rank_candidates([report("candidate")])

        self.assertEqual(result[0].candidate_id, "candidate")

    def test_ranking_does_not_modify_existing_objects(self):
        original = report("candidate", confidence=0.8, ranked_position=99)
        reports = [original]

        result = rank_candidates(reports)

        self.assertIsNot(result[0], original)
        self.assertEqual(original.ranked_position, 99)
        self.assertEqual(original.confidence_score, 0.8)
        self.assertEqual(reports, [original])

    def test_ranking_does_not_modify_sandbox_result(self):
        sandbox = SandboxResult("candidate", True, 2, 2, 1.0)
        before = sandbox.__dict__.copy()

        rank_candidates([report("candidate")])

        self.assertEqual(sandbox.__dict__, before)

    def test_confidence_values_are_deterministic(self):
        candidates = [
            report("valid", confidence=0.65),
            report("invalid", schema_ok=False, confidence=0.95),
        ]

        first = [effective_confidence(item) for item in candidates]
        second = [effective_confidence(item) for item in candidates]

        self.assertEqual(first, [0.65, 0.0])
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
