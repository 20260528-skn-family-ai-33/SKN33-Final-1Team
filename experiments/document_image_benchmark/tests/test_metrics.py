"""Run with: python -m unittest discover -s experiments/document_image_benchmark/tests."""

import copy
import json
import unittest

from experiments.document_image_benchmark.metrics import evaluate


IMAGE_SHA = "a" * 64
PACKET_SHA = "b" * 64


def fixture(verdicts, outcomes=None, human_verified=True):
    packet = {"schema": "document-image-review-v1", "cases": []}
    gold = {"schema": "document-image-gold-v1", "packet_sha256": PACKET_SHA, "labels": []}
    predictions = {
        "schema": "document-image-predictions-v1",
        "packet_sha256": PACKET_SHA,
        "results": [],
    }
    if outcomes is None:
        outcomes = verdicts
    for index, (verdict, outcome) in enumerate(zip(verdicts, outcomes)):
        case_id = f"case-{index}"
        packet["cases"].append({
            "id": case_id,
            "image": {"path": f"{case_id}.png", "sha256": IMAGE_SHA},
            "conditions": [{"id": "condition-1", "approval_status": "approved"}],
        })
        gold["labels"].append({
            "case_id": case_id,
            "condition_id": "condition-1",
            "image_sha256": IMAGE_SHA,
            "verdict": verdict,
            "human_verified": human_verified,
            "verified_by": "reviewer" if human_verified else None,
            "verified_at": "2026-10-29T00:00:00Z" if human_verified else None,
            "provenance": {"kind": "human" if human_verified else "ai_draft"},
        })
        if outcome == "missing":
            continue
        predictions["results"].append({
            "case_id": case_id,
            "condition_id": "condition-1",
            "image_sha256": IMAGE_SHA,
            "execution_status": "error" if outcome == "error" else "success",
            "verdict": None if outcome == "error" else outcome,
            "reason": "fixture",
            "evidence": [],
        })
    return packet, gold, predictions


class EvaluateTests(unittest.TestCase):
    def test_seven_case_fixture_preserves_denominator_and_false_negatives(self):
        result = evaluate(*fixture(
            ["fail", "fail", "fail", "fail", "fail", "pass", "review"],
            ["pass", "review", "error", "missing", "fail", "pass", "review"],
        ))
        metric = result["human_verified"]
        self.assertEqual(metric["n"], 7)
        self.assertEqual(metric["correct"], 3)
        for name, expected in {
            "accuracy": 3 / 7,
            "macro_f1": 5 / 9,
            "auto_coverage": 3 / 7,
            "review_rate": 2 / 7,
            "error_rate": 1 / 7,
            "missing_rate": 1 / 7,
            "fail_recall": 1 / 5,
            "false_pass_rate": 1 / 5,
            "missed_violation_rate": 4 / 5,
        }.items():
            with self.subTest(metric=name):
                self.assertAlmostEqual(metric[name], expected)
        self.assertEqual(metric["per_class"]["fail"]["false_negative"], 4)
        self.assertEqual(metric["confusion_matrix"]["fail"]["execution_error"], 1)
        self.assertEqual(metric["confusion_matrix"]["fail"]["missing"], 1)
        self.assertAlmostEqual(sum(metric[key] for key in (
            "auto_coverage", "review_rate", "error_rate", "missing_rate"
        )), 1.0)

    def test_approved_ai_draft_is_never_promoted_to_human_verified(self):
        packet, gold, predictions = fixture(["pass"], human_verified=False)
        gold["labels"][0]["verified_by"] = "draft-author"
        gold["labels"][0]["verified_at"] = "2026-10-29T00:00:00Z"
        result = evaluate(packet, gold, predictions)
        self.assertEqual(result["draft_agreement"]["accuracy"], 1.0)
        self.assertEqual(result["human_verified"]["n"], 0)
        self.assertIsNone(result["human_verified"]["accuracy"])
        self.assertIsNone(result["human_verified"]["macro_f1"])
        self.assertEqual(result["overall"]["human_verified_coverage"], 0.0)

    def test_unlabeled_count_includes_absent_and_null_gold(self):
        packet, gold, predictions = fixture(["pass", None, "fail"], ["pass", "review", "error"])
        gold["labels"].pop(2)
        result = evaluate(packet, gold, predictions)
        self.assertEqual(result["overall"]["expected_count"], 3)
        self.assertEqual(result["overall"]["missing_gold_count"], 1)
        self.assertEqual(result["overall"]["null_gold_count"], 1)
        self.assertEqual(result["overall"]["label_coverage"], 1 / 3)
        self.assertEqual(result["unlabeled"]["n"], 2)
        self.assertIsNone(result["unlabeled"]["accuracy"])
        self.assertEqual(result["unlabeled"]["error_rate"], 0.5)
        self.assertEqual(result["human_verified"]["n"], 1)

    def test_empty_packet_and_no_fail_gold_are_json_safe(self):
        result = evaluate(*fixture([]))
        self.assertIsNone(result["overall"]["label_coverage"])
        self.assertIsNone(result["human_verified"]["accuracy"])
        self.assertIsNone(result["human_verified"]["fail_recall"])
        json.dumps(result, allow_nan=False)
        metric = evaluate(*fixture(["pass"]))["human_verified"]
        self.assertIsNone(metric["false_pass_rate"])
        self.assertIsNone(metric["missed_violation_rate"])
        self.assertEqual(metric["per_class"]["fail"]["f1"], 0.0)
        self.assertAlmostEqual(metric["macro_f1"], 1 / 3)
        json.dumps(metric, allow_nan=False)

    def test_duplicate_unknown_and_wrong_hash_records_are_rejected(self):
        for collection_index, collection_name in ((1, "labels"), (2, "results")):
            for mutation in ("duplicate", "unknown_case", "unknown_condition", "wrong_hash"):
                with self.subTest(collection=collection_name, mutation=mutation):
                    inputs = fixture(["pass"])
                    records = inputs[collection_index][collection_name]
                    if mutation == "duplicate":
                        records.append(copy.deepcopy(records[0]))
                    elif mutation == "unknown_case":
                        records[0]["case_id"] = "unknown"
                    elif mutation == "unknown_condition":
                        records[0]["condition_id"] = "unknown"
                    else:
                        records[0]["image_sha256"] = "c" * 64
                    with self.assertRaises(ValueError):
                        evaluate(*inputs)

    def test_duplicate_packet_case_or_condition_is_rejected(self):
        for field in ("case", "condition"):
            with self.subTest(field=field):
                inputs = fixture(["pass"])
                case = inputs[0]["cases"][0]
                if field == "case":
                    inputs[0]["cases"].append(copy.deepcopy(case))
                else:
                    case["conditions"].append(copy.deepcopy(case["conditions"][0]))
                with self.assertRaises(ValueError):
                    evaluate(*inputs)

    def test_packet_hash_mismatch_is_rejected(self):
        inputs = fixture(["pass"])
        inputs[2]["packet_sha256"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "packet_sha256 mismatch"):
            evaluate(*inputs)

    def test_missing_verdict_field_is_rejected_instead_of_treated_as_null(self):
        for index, collection in ((1, "labels"), (2, "results")):
            with self.subTest(collection=collection):
                inputs = fixture(["pass"])
                del inputs[index][collection][0]["verdict"]
                with self.assertRaisesRegex(ValueError, "verdict is required"):
                    evaluate(*inputs)

    def test_human_verified_requires_explicit_bool_and_audit_fields(self):
        for field, value in (("human_verified", "true"), ("human_verified", 1),
                             ("verified_by", None), ("verified_at", " ")):
            with self.subTest(field=field, value=value):
                inputs = fixture(["pass"])
                inputs[1]["labels"][0][field] = value
                with self.assertRaises(ValueError):
                    evaluate(*inputs)

    def test_execution_and_verdict_are_consistent(self):
        for status, verdict in (("error", "pass"), ("success", None),
                                ("pending", None), ("success", "unknown")):
            with self.subTest(status=status, verdict=verdict):
                inputs = fixture(["pass"])
                inputs[2]["results"][0].update(execution_status=status, verdict=verdict)
                with self.assertRaises(ValueError):
                    evaluate(*inputs)

    def test_same_condition_id_in_different_cases_and_multiple_conditions(self):
        packet, gold, predictions = fixture(["pass", "fail"])
        packet["cases"][0]["conditions"].append({"id": "condition-2"})
        label = copy.deepcopy(gold["labels"][0])
        label["condition_id"] = "condition-2"
        gold["labels"].append(label)
        result = evaluate(packet, gold, predictions)
        self.assertEqual(result["overall"]["expected_count"], 3)
        self.assertEqual(result["human_verified"]["accuracy"], 2 / 3)
        self.assertEqual(result["human_verified"]["missing_rate"], 1 / 3)

    def test_evaluate_does_not_mutate_inputs(self):
        inputs = fixture(["pass", "fail"], ["error", "missing"])
        before = copy.deepcopy(inputs)
        evaluate(*inputs)
        self.assertEqual(inputs, before)


if __name__ == "__main__":
    unittest.main()
