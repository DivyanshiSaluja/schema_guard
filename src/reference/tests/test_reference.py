import unittest

from src.reference.attribution import (
    ACTIVATE_REFERENCE,
    ALERT,
    CANDIDATE_INDUCED,
    HALT_FIELD,
    QUARANTINE,
    UPSTREAM_INDUCED,
    attribute_batch,
    incumbent_health,
)
from src.deploy.activation import activate_module
from src.reference.artifact import render_reference_module
from src.reference.attribution import candidate_share_upper_bound, in_sample
from src.reference.drift import (
    AMBIGUOUS,
    DROP,
    MERGE,
    RENAME,
    RETYPE,
    SPLIT,
    normalize_drift,
    required_probe_size,
    wilson_lower_bound,
)
from src.reference.evidence import build_evidence, check_freshness
from src.reference.invariants import NON_EMPTY, NOT_NULL, UNIQUE, Invariant
from src.reference.verify import (
    DIVERGENT,
    EQUIVALENT,
    UNVERIFIABLE,
    verify_candidate,
)


INCUMBENT = (
    "def transform(row):\n"
    "    return {'id': row['id'], 'name': row['full_name'], 'email': row['email']}\n"
)
PRIOR = {"id": "integer", "full_name": "text", "email": "text"}
RENAMED = {"id": "integer", "name": "text", "email": "text"}


def pre_rows(count=40):
    return [
        {"id": n, "full_name": f"Customer {n}", "email": f"c{n}@example.com"}
        for n in range(1, count + 1)
    ]


def renamed(rows):
    return [
        {"id": r["id"], "name": r["full_name"], "email": r["email"]} for r in rows
    ]


def candidate(name_expr, email_expr="row['email']", id_expr="row['id']"):
    return (
        "def transform(row):\n"
        f"    return {{'id': {id_expr}, 'name': {name_expr}, 'email': {email_expr}}}\n"
    )


class DriftNormalizationTests(unittest.TestCase):
    def test_rename_confirmed_on_probe_rows(self):
        events = normalize_drift(
            "customers", PRIOR, RENAMED,
            probe_rows=pre_rows(), live_rows=renamed(pre_rows()),
        )
        self.assertEqual([e.drift_class for e in events], [RENAME])
        self.assertEqual((events[0].old_column, events[0].new_column), ("full_name", "name"))

    def test_rename_without_matching_values_is_drop_plus_add(self):
        live = [dict(r, name="someone else") for r in renamed(pre_rows())]
        events = normalize_drift(
            "customers", PRIOR, RENAMED, probe_rows=pre_rows(), live_rows=live,
        )
        self.assertEqual(sorted(e.drift_class for e in events), ["ADD", DROP])

    def test_two_matching_columns_are_ambiguous(self):
        live = [dict(r, display_name=r["name"]) for r in renamed(pre_rows())]
        events = normalize_drift(
            "customers", PRIOR, dict(RENAMED, display_name="text"),
            probe_rows=pre_rows(), live_rows=live,
        )
        self.assertEqual(events[0].drift_class, AMBIGUOUS)
        self.assertEqual(events[0].candidates, ("display_name", "name"))

    def test_retype_detected(self):
        events = normalize_drift("customers", PRIOR, dict(PRIOR, id="text"))
        self.assertEqual(events[0].drift_class, RETYPE)


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.domain = renamed(pre_rows())
        self.events = normalize_drift(
            "customers", PRIOR, RENAMED, probe_rows=pre_rows(), live_rows=self.domain,
        )

    def test_correct_repair_is_equivalent(self):
        result = verify_candidate("c1", candidate("row['name']"), INCUMBENT, self.events, self.domain)
        self.assertEqual(result.verdict, EQUIVALENT)
        self.assertTrue(result.deployable)

    def test_wrong_column_is_divergent_with_one_row_witness(self):
        result = verify_candidate("c2", candidate("row['email']"), INCUMBENT, self.events, self.domain)
        self.assertEqual(result.verdict, DIVERGENT)
        self.assertEqual(len(result.witness.rows), 1)
        self.assertEqual(result.witness.read_difference["reference_only"], frozenset({"name"}))

    def test_stale_repair_is_divergent(self):
        result = verify_candidate("c0", candidate("row['full_name']"), INCUMBENT, self.events, self.domain)
        self.assertEqual(result.verdict, DIVERGENT)
        self.assertEqual(result.divergences[0].kind, "error")

    def test_dropped_input_makes_field_unverifiable(self):
        domain = [{"id": r["id"], "full_name": r["full_name"]} for r in pre_rows()]
        events = normalize_drift("customers", PRIOR, {"id": "integer", "full_name": "text"})
        result = verify_candidate(
            "d2", candidate("row['full_name']", email_expr="'unknown@invalid'"),
            INCUMBENT, events, domain,
        )
        self.assertEqual(result.verdict, UNVERIFIABLE)
        self.assertEqual(result.unverifiable_fields, frozenset({"email"}))
        self.assertTrue(result.needs_waiver)

    def test_lossy_retype_value_is_unverifiable_not_divergent(self):
        domain = [dict(r, id=str(r["id"])) for r in pre_rows(3)] + [
            {"id": "007", "full_name": "Bond", "email": "b@example.com"}
        ]
        events = normalize_drift("customers", PRIOR, dict(PRIOR, id="text"))
        result = verify_candidate(
            "r1", candidate("row['full_name']", id_expr="int(row['id'])"),
            INCUMBENT, events, domain,
        )
        self.assertEqual(result.verdict, UNVERIFIABLE)
        self.assertIn("id", result.unverifiable_fields)

    def test_input_data_violation_is_not_charged_to_candidate(self):
        domain = self.domain + [{"id": 99, "name": "", "email": "x@example.com"}]
        result = verify_candidate(
            "c1", candidate("row['name']"), INCUMBENT, self.events, domain,
            [Invariant(NON_EMPTY, "name")],
        )
        self.assertTrue(result.deployable)
        self.assertEqual(result.upstream_violations, {"non_empty(name)": (99,)})

    def test_uniqueness_witness_keeps_two_rows(self):
        result = verify_candidate(
            "dup", candidate("row['name']", id_expr="1"), INCUMBENT, self.events,
            self.domain, [Invariant(UNIQUE, "id")],
        )
        self.assertEqual(result.verdict, DIVERGENT)
        self.assertIn("unique(id)", result.invariant_violations)


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.batch = renamed(pre_rows())
        self.events = normalize_drift(
            "customers", PRIOR, RENAMED, probe_rows=pre_rows(), live_rows=self.batch,
        )
        self.invariants = [Invariant(NON_EMPTY, "name"), Invariant(NOT_NULL, "email")]

    def test_upstream_fault_is_quarantined(self):
        self.batch[2]["name"] = None
        report = attribute_batch(self.batch, candidate("row['name']"), INCUMBENT, self.events, self.invariants)
        self.assertEqual(report.action, QUARANTINE)
        self.assertEqual(report.quarantined_keys, (3,))
        self.assertEqual(report.per_invariant[0].attribution, UPSTREAM_INDUCED)

    def test_candidate_fault_activates_reference(self):
        report = attribute_batch(
            self.batch, candidate("row['name'] if row['id'] != 5 else ''"),
            INCUMBENT, self.events, self.invariants,
        )
        self.assertEqual(report.action, ACTIVATE_REFERENCE)
        self.assertEqual(report.per_invariant[0].attribution, CANDIDATE_INDUCED)

    def test_reference_failing_alone_only_alerts(self):
        buggy = (
            "def transform(row):\n"
            "    return {'id': row['id'], 'name': '' if row['id'] == 4 else row['full_name'], 'email': row['email']}\n"
        )
        report = attribute_batch(self.batch, candidate("row['name']"), buggy, self.events, self.invariants)
        self.assertEqual(report.action, ALERT)

    def test_untrusted_incumbent_halts_instead_of_attributing(self):
        buggy = (
            "def transform(row):\n"
            "    return {'id': row['id'], 'name': None if row['id'] % 10 == 0 else row['full_name'], 'email': row['email']}\n"
        )
        untrusted = incumbent_health(buggy, pre_rows(), self.invariants)
        self.assertEqual(untrusted, frozenset({"non_empty(name)"}))
        self.batch[2]["name"] = None
        report = attribute_batch(
            self.batch, candidate("row['name']"), buggy, self.events,
            self.invariants, untrusted_invariants=untrusted,
        )
        self.assertEqual(report.action, HALT_FIELD)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.code = candidate("row['name']")
        self.record = build_evidence(
            candidate_code=self.code, incumbent_code=INCUMBENT, events=(),
            prior_schema=PRIOR, changed_schema=RENAMED, invariant_names=["x"],
            domain=[], verdict=EQUIVALENT, approval={"reviewer": "r"},
        )

    def test_reformatted_code_is_still_fresh(self):
        ok, _ = check_freshness(self.record, self.code.replace(", ", ",  "), RENAMED)
        self.assertTrue(ok)

    def test_changed_code_or_schema_voids_approval(self):
        self.assertFalse(check_freshness(self.record, candidate("row['email']"), RENAMED)[0])
        self.assertFalse(check_freshness(self.record, self.code, dict(RENAMED, phone="text"))[0])


class SplitMergeTests(unittest.TestCase):
    def test_split_detected_and_reversed(self):
        live = [
            {"id": r["id"], "first": r["full_name"].split(" ")[0],
             "last": r["full_name"].split(" ")[1], "email": r["email"]}
            for r in pre_rows()
        ]
        events = normalize_drift(
            "customers", PRIOR, {"id": "integer", "first": "text", "last": "text", "email": "text"},
            probe_rows=pre_rows(), live_rows=live,
        )
        self.assertEqual(events[0].drift_class, SPLIT)
        self.assertEqual((events[0].parts, events[0].separator), (("first", "last"), " "))
        good = candidate("row['first'] + ' ' + row['last']")
        self.assertEqual(verify_candidate("s1", good, INCUMBENT, events, live).verdict, EQUIVALENT)
        self.assertEqual(verify_candidate("s2", candidate("row['first']"), INCUMBENT, events, live).verdict, DIVERGENT)

    def test_merge_is_unverifiable_only_where_separator_repeats(self):
        live = [{"id": r["id"], "contact": f"{r['full_name']}|{r['email']}"} for r in pre_rows()]
        events = normalize_drift(
            "customers", PRIOR, {"id": "integer", "contact": "text"},
            probe_rows=pre_rows(), live_rows=live,
        )
        self.assertEqual(events[0].drift_class, MERGE)
        code = candidate("row['contact'].split('|')[0]", email_expr="row['contact'].split('|')[1]")
        self.assertEqual(verify_candidate("m1", code, INCUMBENT, events, live).verdict, EQUIVALENT)
        odd = live + [{"id": 999, "contact": "Pipe|Name|p@example.com"}]
        result = verify_candidate("m1", code, INCUMBENT, events, odd)
        self.assertEqual(result.verdict, UNVERIFIABLE)
        self.assertEqual(result.unverifiable_fields, frozenset({"name", "email"}))


class StatisticsTests(unittest.TestCase):
    def test_probe_size_needed_for_theta(self):
        n = required_probe_size(0.9)
        self.assertGreaterEqual(wilson_lower_bound(n, n), 0.9)
        self.assertLess(wilson_lower_bound(n - 1, n - 1), 0.9)

    def test_upper_bound_shrinks_with_more_shared_violations(self):
        self.assertGreater(candidate_share_upper_bound(2, 0.05), 0.25)
        self.assertLessEqual(candidate_share_upper_bound(11, 0.05), 0.25)


class SampledAttributionTests(unittest.TestCase):
    def setUp(self):
        self.batch = renamed(pre_rows(200))
        self.events = normalize_drift(
            "customers", PRIOR, RENAMED, probe_rows=pre_rows(200), live_rows=self.batch,
        )
        self.invariants = [Invariant(NON_EMPTY, "name")]

    def test_sampled_candidate_fault_is_proven_by_one_record(self):
        sampled = [r["id"] for r in self.batch if in_sample(r["id"], 0.3)]
        bad = sampled[0]
        code = candidate(f"row['name'] if row['id'] != {bad} else ''")
        report = attribute_batch(self.batch, code, INCUMBENT, self.events, self.invariants, sample_fraction=0.3)
        self.assertEqual(report.action, ACTIVATE_REFERENCE)

    def test_too_few_shared_violations_halts(self):
        sampled = [r["id"] for r in self.batch if in_sample(r["id"], 0.3)]
        self.batch[sampled[0] - 1]["name"] = ""
        report = attribute_batch(self.batch, candidate("row['name']"), INCUMBENT, self.events, self.invariants, sample_fraction=0.3)
        self.assertEqual(report.action, HALT_FIELD)

    def test_many_shared_violations_quarantine(self):
        for row in self.batch:
            if row["id"] % 4 == 0:
                row["name"] = ""
        report = attribute_batch(self.batch, candidate("row['name']"), INCUMBENT, self.events, self.invariants, sample_fraction=0.3)
        self.assertEqual(report.action, QUARANTINE)
        self.assertEqual(len(report.quarantined_keys), 50)


class ArtifactTests(unittest.TestCase):
    def test_rendered_reference_runs_and_activates_atomically(self):
        import importlib.util
        import tempfile
        from pathlib import Path
        events = normalize_drift(
            "customers", PRIOR, RENAMED, probe_rows=pre_rows(), live_rows=renamed(pre_rows()),
        )
        source = render_reference_module(INCUMBENT, events)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "transformations.py"
            target.write_text(INCUMBENT)
            snapshot = activate_module(source, target)
            self.assertIsNotNone(snapshot)
            spec = importlib.util.spec_from_file_location("ref", target)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            row = renamed(pre_rows(1))[0]
            self.assertEqual(module.transform(row), {"id": 1, "name": "Customer 1", "email": "c1@example.com"})
            with self.assertRaises(SyntaxError):
                activate_module("def broken(:", target)
            self.assertEqual(target.read_text(), source)


if __name__ == "__main__":
    unittest.main()
