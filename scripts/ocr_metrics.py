"""정답을 추론에 전달하지 않는 OCR 사후 평가. 초안 지표와 승인 지표를 구분한다."""
from __future__ import annotations

import math
import re
import unicodedata

from backend.app.extract_event import extract_event
from backend.app.review_rules import review_criteria

FIELDS = ("event_name", "start_date", "end_date", "location_raw")


def compact(text):
    """유니코드와 공백만 통일하고 오탈자·문장부호는 그대로 비교한다."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text or ""))


def edit_distance(expected, actual):
    """삽입·삭제·치환 횟수로 문자 오류율의 분자를 구한다."""
    row = list(range(len(actual) + 1))
    for i, char in enumerate(expected, 1):
        new = [i]
        for j, other in enumerate(actual, 1):
            new.append(min(new[-1] + 1, row[j] + 1, row[j - 1] + (char != other)))
        row = new
    return row[-1]


def valid_box(box, dimensions):
    """박스의 유한성, 면적, 정규화 원본 경계를 확인한다."""
    width, height = dimensions
    return (isinstance(box, list) and len(box) == 4
            and all(type(v) in (int, float) and math.isfinite(v) for v in box)
            and 0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)


def validate_label(label, source_hash, dimensions):
    """다른 이미지·좌표계·불완전한 승인 정보가 평가에 섞이지 않게 한다."""
    if label.get("source_sha256") != source_hash:
        raise ValueError("Label source hash mismatch")
    if label.get("coordinate_space") != "normalized_original_pixels" or label.get("dimensions") != list(dimensions):
        raise ValueError("Label coordinate space/dimensions mismatch")
    if type(label.get("human_verified")) is not bool:
        raise ValueError("Label must declare human_verified")
    if label["human_verified"] and not (label.get("review_status") == "approved" and label.get("reviewed_by") and label.get("reviewed_at")):
        raise ValueError("Approved labels require reviewer and review time")
    ids = set()
    for region in label["regions"]:
        if region["id"] in ids or not valid_box(region["bbox_xyxy"], dimensions) or not compact(region["text"]):
            raise ValueError("Invalid label region")
        ids.add(region["id"])
    for name, field in label["fields"].items():
        if name not in FIELDS or field["status"] not in {"visible", "unresolved", "absent"}:
            raise ValueError("Invalid reference field")
        if field["status"] == "visible" and (not isinstance(field["value"], str) or not field["value"] or not field.get("region_ids")):
            raise ValueError("Visible fields require text and evidence")
        if field["status"] != "visible" and field.get("value") is not None:
            raise ValueError("Absent/unresolved fields must be null")
        if not set(field.get("region_ids", [])).issubset(ids):
            raise ValueError("Unknown field evidence region")
    criterion_ids = set()
    for criterion in label.get("criteria", []):
        if criterion["id"] in criterion_ids or criterion["type"] not in {"information", "visual"} or criterion["expected_status"] not in {"pass", "fail", "review"}:
            raise ValueError("Invalid reference criterion")
        if not isinstance(criterion.get("text"), str) or not criterion["text"].strip():
            raise ValueError("Criterion text required")
        if not set(criterion.get("region_ids", [])).issubset(ids):
            raise ValueError("Unknown criterion evidence region")
        if criterion["expected_status"] in {"pass", "fail"} and not criterion.get("region_ids"):
            raise ValueError("Decisive reference verdict requires evidence")
        criterion_ids.add(criterion["id"])


def evaluate(records, label, *, multiline=False):
    """OCR 추출을 먼저 수행한 뒤 분리된 라벨과 검수 기준으로 결과를 채점한다."""
    from backend.app.event_layout import extract_event_multiline
    event = extract_event_multiline(records) if multiline else extract_event(records)
    regions = label["regions"]
    assigned = {r["id"]: [] for r in regions}
    for record in records:
        x1, y1, x2, y2 = record["bbox_xyxy"]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        matches = [r for r in regions if r["bbox_xyxy"][0] <= cx < r["bbox_xyxy"][2]
                   and r["bbox_xyxy"][1] <= cy < r["bbox_xyxy"][3]]
        if matches:
            # 겹치는 초안 박스에서는 가장 작은 영역에 한 번만 배정한다.
            region = min(matches, key=lambda r: (r["bbox_xyxy"][2] - r["bbox_xyxy"][0]) * (r["bbox_xyxy"][3] - r["bbox_xyxy"][1]))
            assigned[region["id"]].append(record)
    comparisons = []
    for region in regions:
        matched = sorted(assigned[region["id"]], key=lambda r: (r["bbox_xyxy"][1], r["bbox_xyxy"][0]))
        actual = " ".join(r["text"] for r in matched)
        expected = compact(region["text"])
        comparisons.append({"region_id": region["id"], "expected": region["text"], "actual": actual,
                            "ocr_ids": [r["id"] for r in matched], "characters": len(expected),
                            "edits": edit_distance(expected, compact(actual))})
    fields = []
    for name, field in label["fields"].items():
        # 판독 불가와 실제 부재를 구분하며, 둘 다 가시 필드 정확도의 분모에 넣지 않는다.
        actual = event["fields"][name]["value"]
        visible = field["status"] == "visible"
        fields.append({"field": name, "reference_status": field["status"], "expected": field["value"],
                       "actual": actual, "scored": visible,
                       "exact": visible and actual is not None and compact(actual) == compact(field["value"]),
                       "missing": visible and actual is None,
                       "hallucinated_on_absent": field["status"] == "absent" and actual is not None})
    criteria = [{k: c[k] for k in ("id", "text", "type")} for c in label.get("criteria", [])]
    verdicts = review_criteria(criteria, records, multiline=multiline)
    for result, reference in zip(verdicts, label.get("criteria", [])):
        result["expected_status"] = reference["expected_status"]
    edits = sum(c["edits"] for c in comparisons)
    chars = sum(c["characters"] for c in comparisons)
    return {"reference_status": "human_approved" if label["human_verified"] else "draft_diagnostic",
            "event": event, "regions": comparisons, "fields": fields, "verdicts": verdicts,
            "counts": {"edits": edits, "characters": chars,
                       "exact_fields": sum(f["exact"] for f in fields),
                       "visible_fields": sum(f["scored"] for f in fields),
                       "missing_fields": sum(f["missing"] for f in fields),
                       "hallucinated_absent_fields": sum(f["hallucinated_on_absent"] for f in fields),
                       "criteria": len(verdicts), "review": sum(v["status"] == "review" for v in verdicts),
                       "expected_pass": sum(v["expected_status"] == "pass" for v in verdicts),
                       "correct_pass": sum(v["expected_status"] == v["status"] == "pass" for v in verdicts),
                       "expected_fail": sum(v["expected_status"] == "fail" for v in verdicts),
                       "false_pass": sum(v["expected_status"] == "fail" and v["status"] == "pass" for v in verdicts),
                       "false_fail": sum(v["expected_status"] == "pass" and v["status"] == "fail" for v in verdicts),
                       "unsafe_review_pass": sum(v["expected_status"] == "review" and v["status"] == "pass" for v in verdicts)},
            "core_cer": edits / chars if chars else None}
