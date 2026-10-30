"""Dependency-free evaluation of condition verdicts against versioned labels.

The packet is the denominator: absent predictions and execution errors remain
in every labeled partition. Approval of a criterion never verifies a gold label.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


VERDICTS = ("pass", "fail", "review")
OUTCOMES = (*VERDICTS, "execution_error", "missing")
MACRO_F1_NOTE = (
    "Unweighted mean over fixed labels pass/fail/review; undefined class "
    "precision/recall/F1 use zero_division=0. Missing predictions and execution "
    "errors count as false negatives for their gold class. Empty partitions "
    "have accuracy=null and macro_f1=null."
)


def _object(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _sha(value: Any, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    return value.lower()


def _records(document: dict, schema: str, name: str) -> list:
    _object(document, schema)
    if document.get("schema") != schema:
        raise ValueError(f"schema must be {schema}")
    records = document.get(name)
    if not isinstance(records, list):
        raise ValueError(f"{name} must be an array")
    return records


def _expected(packet: dict) -> dict[tuple[str, str], str]:
    expected = {}
    case_ids = set()
    for case in _records(packet, "document-image-review-v1", "cases"):
        _object(case, "case")
        case_id = _text(case.get("id"), "case.id")
        if case_id in case_ids:
            raise ValueError(f"duplicate case ID: {case_id}")
        case_ids.add(case_id)
        image = _object(case.get("image"), "case.image")
        _text(image.get("path"), "image.path")
        digest = _sha(image.get("sha256"), "image.sha256")
        conditions = case.get("conditions")
        if not isinstance(conditions, list):
            raise ValueError("case.conditions must be an array")
        for condition in conditions:
            _object(condition, "condition")
            condition_id = _text(condition.get("id"), "condition.id")
            key = (case_id, condition_id)
            if key in expected:
                raise ValueError(f"duplicate condition ID: {key}")
            expected[key] = digest
    return expected


def _index(records: list, expected: dict, kind: str) -> dict:
    indexed = {}
    for record in records:
        _object(record, kind)
        key = (
            _text(record.get("case_id"), f"{kind}.case_id"),
            _text(record.get("condition_id"), f"{kind}.condition_id"),
        )
        if key not in expected:
            raise ValueError(f"unknown {kind} case/condition ID: {key}")
        if key in indexed:
            raise ValueError(f"duplicate {kind} case/condition ID: {key}")
        if _sha(record.get("image_sha256"), f"{kind}.image_sha256") != expected[key]:
            raise ValueError(f"{kind} image_sha256 mismatch: {key}")
        if "verdict" not in record:
            raise ValueError(f"{kind}.verdict is required (null is allowed): {key}")
        verdict = record["verdict"]
        if verdict is not None and verdict not in VERDICTS:
            raise ValueError(f"invalid {kind} verdict: {key}")
        if kind == "gold":
            if type(record.get("human_verified")) is not bool:
                raise ValueError(f"gold.human_verified must be a bool: {key}")
            if record["human_verified"]:
                _text(record.get("verified_by"), "gold.verified_by")
                _text(record.get("verified_at"), "gold.verified_at")
            if "provenance" in record:
                _object(record["provenance"], "gold.provenance")
        else:
            status = record.get("execution_status")
            if status not in ("success", "error"):
                raise ValueError(f"invalid prediction execution_status: {key}")
            if status == "success" and verdict is None:
                raise ValueError(f"successful prediction requires a verdict: {key}")
            if status == "error" and verdict is not None:
                raise ValueError(f"error prediction requires verdict=null: {key}")
            if "evidence" in record and not isinstance(record["evidence"], list):
                raise ValueError(f"prediction.evidence must be an array: {key}")
        indexed[key] = record
    return indexed


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _outcome(prediction: dict | None) -> str:
    if prediction is None:
        return "missing"
    if prediction["execution_status"] == "error":
        return "execution_error"
    return prediction["verdict"]


def _execution_metrics(keys: list, predictions: dict) -> dict:
    counts = Counter(_outcome(predictions.get(key)) for key in keys)
    n = len(keys)
    return {
        "prediction_counts": {outcome: counts[outcome] for outcome in OUTCOMES},
        "auto_coverage": _ratio(counts["pass"] + counts["fail"], n),
        "review_rate": _ratio(counts["review"], n),
        "error_rate": _ratio(counts["execution_error"], n),
        "missing_rate": _ratio(counts["missing"], n),
    }


def _partition(keys: list, labels: dict, predictions: dict) -> dict:
    matrix = {label: {outcome: 0 for outcome in OUTCOMES} for label in VERDICTS}
    for key in keys:
        matrix[labels[key]["verdict"]][_outcome(predictions.get(key))] += 1
    n = len(keys)
    correct = sum(matrix[label][label] for label in VERDICTS)
    per_class = {}
    for label in VERDICTS:
        tp = matrix[label][label]
        support = sum(matrix[label].values())
        predicted = sum(matrix[actual][label] for actual in VERDICTS)
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        denominator = precision + recall
        per_class[label] = {
            "support": support,
            "predicted": predicted,
            "true_positive": tp,
            "false_positive": predicted - tp,
            "false_negative": support - tp,
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / denominator if denominator else 0.0,
        }
    fail_count = per_class["fail"]["support"]
    return {
        "n": n,
        "correct": correct,
        "accuracy": _ratio(correct, n),
        "macro_f1": sum(row["f1"] for row in per_class.values()) / 3 if n else None,
        "macro_f1_labels": list(VERDICTS),
        "zero_division": 0,
        "metric_note": MACRO_F1_NOTE,
        "per_class": per_class,
        "confusion_matrix": matrix,
        **_execution_metrics(keys, predictions),
        "fail_recall": _ratio(matrix["fail"]["fail"], fail_count),
        "false_pass_rate": _ratio(matrix["fail"]["pass"], fail_count),
        "missed_violation_rate": _ratio(fail_count - matrix["fail"]["fail"], fail_count),
    }


def evaluate(packet: dict, gold: dict, predictions: dict) -> dict:
    """Validate and score condition cases without reading or modifying files.

    The caller checks packet_sha256 against the actual packet bytes. This
    function additionally rejects disagreement when both inputs carry hashes.
    No labels or results are inferred, promoted, or silently deduplicated.
    """
    expected = _expected(packet)
    labels = _index(_records(gold, "document-image-gold-v1", "labels"), expected, "gold")
    results = _index(
        _records(predictions, "document-image-predictions-v1", "results"),
        expected,
        "prediction",
    )
    hashes = []
    for name, document in (("gold", gold), ("predictions", predictions)):
        if "packet_sha256" in document:
            hashes.append(_sha(document["packet_sha256"], f"{name}.packet_sha256"))
    if len(hashes) == 2 and hashes[0] != hashes[1]:
        raise ValueError("gold and predictions packet_sha256 mismatch")

    human_keys, draft_keys, unlabeled_keys = [], [], []
    for key in expected:
        label = labels.get(key)
        if label is None or label["verdict"] is None:
            unlabeled_keys.append(key)
        elif label["human_verified"]:
            human_keys.append(key)
        else:
            draft_keys.append(key)
    n = len(expected)
    known = len(human_keys) + len(draft_keys)
    missing_gold = sum(key not in labels for key in expected)
    null_gold = sum(label["verdict"] is None for label in labels.values())
    return {
        "schema": "document-image-metrics-v1",
        "overall": {
            "expected_count": n,
            "provided_gold_count": len(labels),
            "labeled_count": known,
            "unlabeled_count": len(unlabeled_keys),
            "missing_gold_count": missing_gold,
            "null_gold_count": null_gold,
            "human_verified_count": len(human_keys),
            "draft_count": len(draft_keys),
            "label_coverage": _ratio(known, n),
            "human_verified_coverage": _ratio(len(human_keys), n),
            "prediction_count": len(results),
            **_execution_metrics(list(expected), results),
        },
        "human_verified": _partition(human_keys, labels, results),
        "draft_agreement": _partition(draft_keys, labels, results),
        "unlabeled": {
            "n": len(unlabeled_keys),
            "missing_gold_count": missing_gold,
            "null_gold_count": null_gold,
            "accuracy": None,
            "macro_f1": None,
            "metric_note": "No known gold verdict; correctness cannot be scored.",
            **_execution_metrics(unlabeled_keys, results),
        },
    }
