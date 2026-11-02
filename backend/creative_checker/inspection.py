"""A06: conservative evidence validation and required-condition aggregation."""

from __future__ import annotations

import copy
import datetime as dt
import json
import math
import re
import unicodedata
from decimal import Decimal, InvalidOperation

VERDICTS = {"pass", "fail", "review"}
KINDS = {"exact_text", "date", "amount", "quantity", "logo", "layout", "color", "unsupported"}
MAX_CRITERIA = 128
ABSENCE = re.compile(r"not\s+(?:found|visible|detected|present)|\b(?:absent|missing|unreadable)\b|(?:보이지|찾을 수|확인할 수|존재하지)\s*않|미검출|미인식|판독\s*(?:불가|불가능)|(?:텍스트|문구|로고)\s*(?:없음|부재)", re.I)


class InspectionError(ValueError):
    pass


def _canonical(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def _same_json(left, right) -> bool:
    try:
        return json.dumps(left, sort_keys=True, ensure_ascii=False, allow_nan=False) == json.dumps(right, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return False


def _expected_text(value) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value) if not isinstance(value, float) or math.isfinite(value) else None
    if isinstance(value, dict) and set(value) <= {"value", "unit"} and "value" in value:
        text = _expected_text(value["value"])
        unit = value.get("unit", "")
        return text + unit if text is not None and isinstance(unit, str) else None
    return None


def _date(value: str):
    value = _canonical(value)
    patterns = (
        r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일",
        r"(\d{4})([-./])\s*(\d{1,2})\2\s*(\d{1,2})\.?",
    )
    match = re.fullmatch(patterns[0], value)
    values = match.groups() if match else None
    if values is None:
        match = re.fullmatch(patterns[1], value)
        if match:
            values = (match.group(1), match.group(3), match.group(4))
    if values is None:
        return None
    try:
        return dt.date(*(int(part) for part in values))
    except ValueError:
        return None


def _numeric(value: str):
    # Full-token parse. '100' can never match '1100', '100원' or '100.5'.
    value = _canonical(value)
    match = re.fullmatch(r"([₩$€£]?)\s*([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*([A-Za-z가-힣%‰㎡²³/]+)?", value)
    if not match:
        return None
    try:
        return (Decimal(match.group(2).replace(",", "")), match.group(1), match.group(3) or "")
    except InvalidOperation:
        return None


def _comparison(kind: str, expected, observed: str) -> str:
    wanted = _expected_text(expected)
    if wanted is None or not wanted.strip() or not observed.strip():
        return "unknown"
    if kind == "exact_text":
        return "equal" if _canonical(wanted) == _canonical(observed) else "different"
    if kind == "date":
        left, right = _date(wanted), _date(observed)
    elif kind in {"amount", "quantity"}:
        left, right = _numeric(wanted), _numeric(observed)
        # A different unit/currency is not a comparable quantity without an explicit conversion rule.
        if left is not None and right is not None and left[1:] != right[1:]:
            return "unknown"
    else:
        return "unknown"
    if left is None or right is None:
        return "unknown"
    return "equal" if left == right else "different"


def _review(result: dict, limitation: str):
    result["verdict"] = "review"
    if limitation not in result["limitations"]:
        result["limitations"].append(limitation)


def _valid_box(value) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    if any(isinstance(number, bool) or not isinstance(number, (int, float)) or not 0 <= number <= 1 for number in value):
        return False
    x, y, width, height = value
    return x >= 0 and y >= 0 and width > 0 and height > 0 and x + width <= 1.000001 and y + height <= 1.000001


def _criteria_map(criteria: list) -> dict:
    if not isinstance(criteria, list) or len(criteria) > MAX_CRITERIA:
        raise InspectionError("criteria must be a list of at most 128 items")
    mapping = {}
    for criterion in criteria:
        if not isinstance(criterion, dict) or not isinstance(criterion.get("id"), str) or not criterion["id"]:
            raise InspectionError("each criterion requires a nonempty string id")
        if criterion["id"] in mapping:
            raise InspectionError("duplicate criterion id")
        mapping[criterion["id"]] = criterion
    return mapping


def _missing(criterion: dict, message="검사 결과가 없습니다.") -> dict:
    return {
        "criterion_id": criterion["id"], "criterion_text": criterion.get("text", ""),
        "execution_status": "failed", "verdict": "review", "observed": "",
        "expected": copy.deepcopy(criterion.get("expected")), "reason": message,
        "evidence": [], "limitations": ["missing_or_invalid_result"],
    }


def _validate_one(criterion: dict, raw: dict, evidence_ids: set | None) -> dict:
    result = copy.deepcopy(raw)
    result["criterion_id"] = criterion["id"]
    result["criterion_text"] = criterion.get("text", "")
    result["required"] = criterion.get("required") is True
    limitations = result.get("limitations")
    result["limitations"] = list(limitations) if isinstance(limitations, list) and all(isinstance(item, str) for item in limitations) else []
    if not isinstance(result.get("verdict"), str) or result["verdict"] not in VERDICTS:
        _review(result, "invalid_verdict")
    if not isinstance(result.get("execution_status"), str) or result["execution_status"] not in {"completed", "failed", "unsupported"}:
        result["execution_status"] = "failed"
        _review(result, "invalid_execution_status")
    if result["execution_status"] != "completed":
        _review(result, "execution_not_completed")
    for field in ("observed", "reason"):
        if not isinstance(result.get(field), str):
            result[f"reported_{field}"] = result.get(field)
            result[field] = ""
            _review(result, f"invalid_{field}")
    if not result["reason"].strip():
        _review(result, "missing_reason")
    if not _same_json(result.get("expected"), criterion.get("expected")):
        result["reported_expected"] = copy.deepcopy(result.get("expected"))
        _review(result, "expected_value_changed_by_model")
    result["expected"] = copy.deepcopy(criterion.get("expected"))
    if (
        not isinstance(criterion.get("text"), str) or not criterion["text"].strip()
        or not isinstance(criterion.get("required"), bool)
        or not isinstance(criterion.get("kind"), str) or criterion["kind"] not in KINDS
        or not isinstance(criterion.get("target", "all"), str) or criterion.get("target", "all") not in {"all", "poster", "video"}
    ):
        _review(result, "invalid_criterion_schema")
    if criterion.get("supported") is not True or criterion.get("kind") == "unsupported":
        _review(result, "criterion_unsupported")
        result["execution_status"] = "unsupported"
    if criterion.get("category") != "creative":
        _review(result, "noncreative_criterion_requires_human_inspection")
        result["execution_status"] = "unsupported"
    if criterion.get("condition") or criterion.get("exception"):
        _review(result, "unresolved_condition_or_exception")

    raw_evidence = result.get("evidence")
    if not isinstance(raw_evidence, list) or len(raw_evidence) > 64:
        raw_evidence = []
        _review(result, "invalid_evidence_schema")
    evidence = []
    for item in raw_evidence:
        if not isinstance(item, dict):
            _review(result, "invalid_evidence_schema")
            continue
        file_id, timestamp = item.get("file_id"), item.get("frame_ms")
        if (
            not isinstance(file_id, str) or not file_id
            or (evidence_ids is not None and file_id not in evidence_ids)
            or not _valid_box(item.get("bbox"))
            or (timestamp is not None and (isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0))
            or not isinstance(item.get("text"), str)
        ):
            _review(result, "unverified_evidence")
            continue
        evidence.append(item)
    result["evidence"] = evidence
    reference_ids = result.get("logo_reference_ids", [])
    if not isinstance(reference_ids, list) or not all(isinstance(item, str) for item in reference_ids):
        reference_ids = []
        _review(result, "invalid_logo_reference_ids")
    asset_evidence = [item for item in evidence if item["file_id"] not in reference_ids]
    if result["verdict"] in {"pass", "fail"} and not asset_evidence:
        _review(result, "no_located_asset_evidence")

    kind = criterion.get("kind") if isinstance(criterion.get("kind"), str) else "unsupported"
    if kind in {"exact_text", "date", "amount", "quantity"} and result["verdict"] in {"pass", "fail"}:
        texts = [item["text"] for item in asset_evidence if item["text"].strip()]
        if not texts or not result["observed"].strip():
            _review(result, "no_observed_text_absence_cannot_be_inferred_from_ocr")
        elif any(_canonical(text) not in _canonical(result["observed"]) for text in texts):
            _review(result, "observed_text_not_supported_by_evidence")
        else:
            comparisons = [_comparison(kind, criterion.get("expected"), text) for text in texts]
            # Whole observed content must agree as well, preventing a selected substring from proving pass.
            full_comparison = _comparison(kind, criterion.get("expected"), result["observed"])
            if result["verdict"] == "pass" and ("equal" not in comparisons or full_comparison != "equal"):
                _review(result, "exact_or_numeric_equality_not_established")
            elif result["verdict"] == "fail" and ("different" not in comparisons or full_comparison != "different"):
                _review(result, "definite_text_mismatch_not_established")
    if result["verdict"] == "fail" and ABSENCE.search(result["observed"] + " " + result["reason"]):
        _review(result, "absence_not_proven_by_ocr_or_sampled_images")
    if kind == "logo" and result["verdict"] in {"pass", "fail"}:
        if not reference_ids or not any(item["file_id"] in reference_ids for item in evidence):
            _review(result, "approved_logo_reference_evidence_missing")
        # Matching itself is a model observation, never a deterministic proof of logo fidelity.
        if result["verdict"] == "pass":
            _review(result, "logo_similarity_cannot_establish_exact_compliance")
        elif not result["observed"].strip() or re.search(r"similarity|유사도|\b[01]?\.\d+\b", result["reason"], re.I):
            _review(result, "logo_difference_requires_specific_visible_evidence")

    scope = result.get("scope") if isinstance(result.get("scope"), dict) else {}
    video = criterion.get("target") == "video" or scope.get("asset_kind") == "video" or any(item["frame_ms"] is not None for item in asset_evidence)
    if result["verdict"] == "pass" and video and not (scope.get("complete") is True and scope.get("all_frames_checked") is True):
        _review(result, "sampled_video_cannot_prove_full_scope_pass")
    if isinstance(criterion.get("target"), str) and criterion["target"] in {"poster", "video"} and scope.get("asset_kind") and scope["asset_kind"] != criterion["target"]:
        _review(result, "criterion_asset_kind_mismatch")
    if result["verdict"] == "pass" and scope.get("asset_kind") == "poster" and scope.get("complete") is not True:
        _review(result, "poster_full_view_scope_not_inspected")
    expected_assets = criterion.get("asset_ids", [])
    if not isinstance(expected_assets, list) or not all(isinstance(item, str) for item in expected_assets):
        _review(result, "invalid_criterion_asset_scope")
    elif expected_assets:
        actual_assets = scope.get("asset_ids", [])
        if not isinstance(actual_assets, list) or not all(isinstance(item, str) for item in actual_assets) or not set(actual_assets).intersection(expected_assets):
            _review(result, "criterion_asset_scope_not_established")
    if isinstance(raw.get("verdict"), str) and raw["verdict"] in VERDICTS and raw["verdict"] != result["verdict"]:
        result["reported_verdict"] = raw["verdict"]
    return result


def validate_results(criteria: list, results: list, evidence_ids: set | None = None) -> list:
    """Retain source/observed/expected text; downgrade unsupported claims, never invent a pass.

    Pass evidence_ids when the caller has a registered file set. With None, this function
    validates evidence shape/text but cannot independently establish file existence.
    """
    mapping = _criteria_map(criteria)
    if not isinstance(results, list) or len(results) > MAX_CRITERIA * 12:
        raise InspectionError("results must be a bounded list")
    if evidence_ids is not None and (not isinstance(evidence_ids, set) or not all(isinstance(item, str) for item in evidence_ids)):
        raise InspectionError("evidence_ids must be a set of file IDs")
    buckets: dict[str, list] = {key: [] for key in mapping}
    invalid: list[dict] = []
    for raw in results:
        if not isinstance(raw, dict) or not isinstance(raw.get("criterion_id"), str) or raw["criterion_id"] not in mapping:
            invalid.append({
                "criterion_id": raw.get("criterion_id", "") if isinstance(raw, dict) else "",
                "execution_status": "failed", "verdict": "review", "observed": "", "expected": None,
                "reason": "등록되지 않은 조건 또는 잘못된 결과 형식입니다.", "evidence": [],
                "limitations": ["unknown_criterion_or_invalid_result"],
            })
            continue
        key = raw["criterion_id"]
        observations = raw.get("observations")
        if isinstance(observations, list) and observations and len(observations) <= 12:
            if all(isinstance(item, dict) and item.get("criterion_id") == key and not item.get("observations") for item in observations):
                # Idempotent revalidation of a merged multi-frame result: compare each
                # original observation, not concatenated text from different frames.
                buckets[key].extend(_validate_one(mapping[key], item, evidence_ids) for item in observations)
                continue
        buckets[key].append(_validate_one(mapping[key], raw, evidence_ids))
    validated = []
    for key, criterion in mapping.items():
        observations = buckets[key]
        if not observations:
            validated.append(_missing(criterion))
            continue
        if len(observations) == 1:
            validated.append(observations[0])
            continue
        # Distinct frame violations outrank other frames' uncertainty. Conflicting claims
        # about the very same region/time are not a confirmed violation.
        signatures: dict[tuple, set] = {}
        for item in observations:
            signature = tuple(sorted((ev["file_id"], -1 if ev["frame_ms"] is None else ev["frame_ms"], tuple(ev["bbox"])) for ev in item["evidence"]))
            signatures.setdefault(signature, set()).add(item.get("reported_verdict", item["verdict"]))
        conflict = any("pass" in values and "fail" in values for values in signatures.values())
        verdict = "review" if conflict else ("fail" if any(item["verdict"] == "fail" for item in observations) else "review" if any(item["verdict"] == "review" for item in observations) else "pass")
        merged = copy.deepcopy(next((item for item in observations if item["verdict"] == verdict), observations[0]))
        merged["verdict"] = verdict
        merged["observations"] = observations
        merged["observed"] = "\n".join(dict.fromkeys(item["observed"] for item in observations if item["observed"]))
        merged["evidence"] = [ev for item in observations for ev in item["evidence"]]
        merged["limitations"] = list(dict.fromkeys(limitation for item in observations for limitation in item["limitations"]))
        if conflict:
            _review(merged, "conflicting_observations_of_same_evidence")
        validated.append(merged)
    return validated + invalid


def _combine(verdicts: list[str], operator="all") -> str:
    if not verdicts:
        return "review"
    if operator == "any":
        return "pass" if "pass" in verdicts else "review" if "review" in verdicts else "fail"
    return "fail" if "fail" in verdicts else "review" if "review" in verdicts else "pass"


def aggregate_verdict(criteria: list, results: list, groups=None) -> str:
    """Required AND at root; explicit OR inside groups; unresolved branches stay review."""
    mapping = _criteria_map(criteria)
    checked = validate_results(criteria, results)
    by_id = {item["criterion_id"]: item for item in checked if item["criterion_id"] in mapping}
    required = {key for key, item in mapping.items() if item.get("required") is True}
    if not required:
        return "review"
    group_list = groups if isinstance(groups, list) else []
    decisions = []
    if groups is not None and not isinstance(groups, list):
        decisions.append("review")
    if any(item["criterion_id"] not in mapping for item in checked) or any(not isinstance(item.get("required"), bool) for item in criteria):
        decisions.append("review")
    membership: dict[str, int] = {}
    for group in group_list:
        if isinstance(group, dict) and isinstance(group.get("criterion_ids"), list):
            for key in group["criterion_ids"]:
                if isinstance(key, str):
                    membership[key] = membership.get(key, 0) + 1
    covered = set()
    seen_ids = set()
    for group in group_list:
        if not isinstance(group, dict):
            decisions.append("review")
            continue
        keys = group.get("criterion_ids")
        if not isinstance(keys, list) or not keys or not all(isinstance(key, str) for key in keys):
            decisions.append("review")
            continue
        applicable = [key for key in keys if key in required]
        covered.update(applicable)
        if not applicable:
            # Recommended-only groups remain visible in results, excluded from verdict.
            if any(key not in mapping for key in keys):
                decisions.append("review")
            continue
        group_id = group.get("id")
        invalid = (
            not isinstance(group_id, str) or not group_id or group_id in seen_ids
            or not isinstance(group.get("operator"), str) or group["operator"] not in {"all", "any"}
            or len(set(keys)) != len(keys) or any(key not in mapping or membership.get(key, 0) > 1 for key in keys)
        )
        if isinstance(group_id, str):
            seen_ids.add(group_id)
        if invalid or group.get("condition") or group.get("exception"):
            decisions.append("review")
        else:
            decisions.append(_combine([by_id[key]["verdict"] for key in applicable], group["operator"]))
    decisions.extend(by_id[key]["verdict"] for key in required - covered)
    return _combine(decisions)
