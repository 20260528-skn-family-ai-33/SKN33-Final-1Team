"""Conservative, evidence-linked checks against a user's frozen criteria.

Only OCR records supplied by the caller are used. A missing OCR match is not
proof that the image omits text. Visual checks require human review except for
explicitly agreed text-only proxies, which preserve their limited scope.
"""
from __future__ import annotations

import math
import re
import unicodedata

from .extract_event import extract_event


MATCH_CONFIDENCE = 0.60
CONFLICT_CONFIDENCE = 0.85
# 모델별 점수는 같은 확률이 아니다. 아래 Paddle 값은 실증 정확도가 아닌 초기 운영 기준이다.
PADDLE_MATCH_CONFIDENCE = 0.90
PADDLE_CONFLICT_CONFIDENCE = 0.97
ENGINE_THRESHOLDS = {
    "easyocr": (MATCH_CONFIDENCE, CONFLICT_CONFIDENCE),
    "paddleocr": (PADDLE_MATCH_CONFIDENCE, PADDLE_CONFLICT_CONFIDENCE),
}
VISUAL_HINT = re.compile(r"로고|색상|색깔|컬러|잘림|잘리|여백|배치|정렬|해상도|가독성|선명|디자인|배경|상단|하단|왼쪽|오른쪽|중앙|폰트|글꼴|굵기")
NEGATIVE_HINT = re.compile(r"금지|제외|삭제|없어|없음|않|사용\s*불가|불포함")
QUOTES = re.compile(r'["“「『]([^"”」』]+)["”」』]|\'([^\']+)\'')
TITLE_LABELS = {"행사명", "축제명", "제목", "이벤트명"}
DATE_LABELS = {"기간", "행사기간", "일정", "행사일정", "날짜"}
# 원본 로고 확보 전 사용자가 허용한 단순 존재 조건만 글자로 대체한다.
LOGO_TEXT_PROXY_CRITERIA = {"김천시로고가표시되어있어야합니다": "김천시"}


def normalized(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text))


def _literal_matches(target: str, observed: str) -> bool:
    # A requested 3,000 must not pass on an observed 13,000 or 3,0000.
    prefix = r"(?<!\d)" if target[:1].isdigit() else ""
    suffix = r"(?!\d)" if target[-1:].isdigit() else ""
    return bool(target and re.search(prefix + re.escape(target) + suffix, observed))


def _evidence(records: list[dict]) -> list[dict]:
    """현재 점수와 원문 출처를 함께 남겨 보류와 기준별 판정을 설명한다."""
    return [
        {"text": item["text"], "bbox_xyxy": item["bbox_xyxy"], "ocr_id": item["id"],
         "confidence": item.get("confidence"), "recognition_engine": item.get("recognition_engine", "easyocr"),
         **({"baseline_text": item["baseline_text"], "alternatives": item.get("alternatives", []),
             "requires_review": bool(item.get("requires_review")),
             **{key: item[key] for key in ("baseline_confidence", "baseline_recognition_engine", "baseline_requires_review", "selection_reason") if key in item}}
            if "baseline_text" in item else {})}
        for item in records
    ]


def _score_sufficient(item: dict, *, conflict: bool = False) -> bool:
    """엔진별 운영 기준을 적용하고 미지원 엔진이나 비정상 점수는 확정하지 않는다."""
    thresholds = ENGINE_THRESHOLDS.get(item.get("recognition_engine", "easyocr"))
    score = item.get("confidence")
    return bool(thresholds and type(score) in (float, int) and math.isfinite(score)
                and 0 <= score <= 1 and score >= thresholds[int(conflict)])


def _baseline_record(item: dict) -> dict | None:
    """출처 메타데이터가 모두 있는 경우에만 최초 OCR 근거를 복원한다."""
    required = ("baseline_text", "baseline_confidence", "baseline_recognition_engine", "baseline_requires_review")
    if not all(key in item for key in required) or not isinstance(item["baseline_text"], str):
        return None
    if type(item["baseline_requires_review"]) is not bool:
        return None
    return {**item, "text": item["baseline_text"], "confidence": item["baseline_confidence"],
            "recognition_engine": item["baseline_recognition_engine"], "requires_review": item["baseline_requires_review"]}


def _reusable_baseline(item: dict, target: str | None) -> bool:
    """성공한 재인식에서 변하지 않은 기준 문구만 기존 점수로 판정한다."""
    if (item.get("selection_reason") != "new_model_candidate_unverified"
            or item.get("recognition_engine") not in {"paddleocr", "qwen3-vl"}):
        return False
    baseline = _baseline_record(item)
    if not baseline or baseline["requires_review"] or not _score_sufficient(baseline):
        return False
    return target is None or all(_literal_matches(target, normalized(text)) for text in (baseline["text"], item["text"]))


def _strong(records: list[dict], *, conflict: bool = False, baseline_agrees: bool = False,
            baseline_target: str | None = None) -> bool:
    """명시적 보류를 유지하되 기준별로 보존된 원문 근거가 있으면 기존 충족을 유지한다."""
    return bool(records) and all(
        (_score_sufficient(item, conflict=conflict) if not item.get("requires_review")
         else not conflict and baseline_agrees and _reusable_baseline(item, baseline_target))
        for item in records)


def _review_reason(records: list[dict], subject: str = "문구") -> str:
    """낮은 점수와 모델 간 변경·미검증·실패를 다른 보류 사유로 안내한다."""
    pending = [item for item in records if item.get("requires_review")]
    if any(item.get("selection_reason") == "refinement_incomplete" for item in pending):
        return "재인식이 일부 실패하거나 중단되어 자동 확정하지 않았습니다. 표시된 근거와 원본을 확인해 주세요."
    if any(item.get("selection_reason") in {"conflicting_candidates_keep_original", "stronger_candidate_unverified"}
           or ("baseline_text" in item and normalized(item["baseline_text"]) != normalized(item["text"])) for item in pending):
        return "기존 OCR과 재인식 결과에 변경된 문구가 있어 자동 확정하지 않았습니다. 두 문구와 원본을 확인해 주세요."
    if pending:
        return "새로 읽거나 재인식한 후보를 자동 확정할 근거가 충분하지 않습니다. 표시된 근거와 원본을 확인해 주세요."
    if any(item.get("recognition_engine", "easyocr") not in ENGINE_THRESHOLDS for item in records):
        return "해당 OCR 모델의 자동 판정 기준이 없어 확인 대상으로 남겼습니다. 표시된 근거를 확인해 주세요."
    return f"{subject} OCR 점수가 해당 모델의 자동 판정 기준에 못 미쳐 확정하지 않았습니다. 표시된 근거를 확인해 주세요."


def _baseline_fields(records: list[dict], extractor) -> dict:
    """제목·날짜의 기존 전체 후보를 다시 해석해 유일한 값이 유지되는지 확인한다."""
    try:
        return extractor([_baseline_record(item) or item for item in records])["fields"]
    except ValueError:
        # 손상되거나 구버전인 출처는 원문 근거 재사용 자격을 얻지 못한다.
        return {}


def _structured_dates(text: str) -> tuple[str, str] | None:
    item = {"id": "criterion", "text": text, "confidence": None, "bbox_xyxy": [0, 0, 1, 1]}
    fields = extract_event([item])["fields"]
    start, end = fields["start_date"]["value"], fields["end_date"]["value"]
    return (start, end) if start and end else None


def _review_logo_text_proxy(text: str, records: list[dict]) -> dict | None:
    """합의된 로고 조건만 문구로 확인하고 추후 이미지 대조와 구분할 근거를 남긴다."""
    target = LOGO_TEXT_PROXY_CRITERIA.get(normalized(text).removesuffix("."))
    if target is None:
        return None
    # 글자 사이 공백은 허용하되 김천시장·김천시청 등 다른 단어는 일치로 보지 않는다.
    pattern = re.compile(r"(?<!\w)" + r"\s*".join(map(re.escape, target)) + r"(?!\w)")
    matches = [item for item in records if pattern.search(unicodedata.normalize("NFC", item["text"]))]
    # 존재 조건이므로 충분한 근거 하나면 통과하며 낮은 점수의 중복 인식은 방해하지 않는다.
    supported = [item for item in matches if _strong([item])]
    evidence = supported or matches
    scope = "로고 원본 대조 전 임시 문구 기준이며, 심벌의 형태는 검증하지 않았습니다."
    if supported:
        reason = f'OCR에서 “{target}” 문구를 확인하여 충족으로 처리했습니다. {scope}'
    elif matches:
        reason = f"{_review_reason(matches)} {scope}"
    else:
        reason = f'“{target}” 문구를 OCR에서 확인하지 못했습니다. 인식 누락일 수 있어 확인 필요로 남겼습니다. {scope}'
    return {"status": "pass" if supported else "review", "check_method": "logo_text_proxy",
            "observed_text": " / ".join(item["text"] for item in evidence),
            "reason": reason, "evidence": _evidence(evidence)}


def review_criteria(criteria: list[dict], records: list[dict], *, multiline=False) -> list[dict]:
    """Return a result for every selected criterion, preserving its ID and order."""
    # 개선 경로만 좌표 기반 결합을 사용하여 기존 비교 기준선을 재현할 수 있게 한다.
    from .event_layout import extract_event_multiline
    extractor = extract_event_multiline if multiline else extract_event
    fields = extractor(records)["fields"]
    baseline_fields = _baseline_fields(records, extractor)
    by_id = {item["id"]: item for item in records}
    results = []
    for criterion in criteria:
        result = {
            "criterion_id": criterion["id"], "criterion": criterion["text"],
            "type": criterion["type"], "status": "review", "observed_text": "",
            "reason": "", "evidence": [],
        }
        text = criterion["text"].strip()
        # 위치·색상·잘림 등의 복합 조건은 대체 정책에 포함하지 않는다.
        logo_text_result = _review_logo_text_proxy(text, records)
        if logo_text_result is not None:
            result.update(logo_text_result)
            results.append(result)
            continue
        if criterion["type"] == "visual" or VISUAL_HINT.search(text):
            result["reason"] = "로고·색상·배치·잘림 등 시각 조건은 OCR로 판정할 수 없습니다. 이미지와 기준을 담당자가 확인해야 합니다."
            results.append(result)
            continue
        if NEGATIVE_HINT.search(text):
            result["reason"] = "금지·제외·부재 조건은 문구 포함 여부만으로 확정할 수 없습니다. 담당자의 확인이 필요합니다."
            results.append(result)
            continue

        label, separator, value = text.partition(":")
        if not separator:
            label, separator, value = text.partition("：")
        label, value = normalized(label), value.strip()
        quoted = [first or second for first, second in QUOTES.findall(text)]
        requested = quoted or ([value] if separator and value else [])
        if not requested:
            result["reason"] = '비교할 문구가 명확하지 않습니다. “필수 문구” 또는 항목명: 값 형식으로 기준을 작성해 주세요.'
            results.append(result)
            continue

        # Compare parsed dates rather than punctuation or optional weekdays.
        if label in DATE_LABELS and separator:
            expected_dates = _structured_dates(value)
            actual_dates = (fields["start_date"]["value"], fields["end_date"]["value"])
            baseline_dates = tuple(baseline_fields.get(name, {}).get("value") for name in ("start_date", "end_date"))
            source = [by_id[item_id] for item_id in fields["start_date"]["source_ocr_ids"]]
            result["evidence"] = _evidence(source)
            result["observed_text"] = " / ".join(item["text"] for item in source)
            if expected_dates is None:
                result["reason"] = "기준의 기간을 해석할 수 없습니다. YYYY.MM.DD ~ MM.DD 형식과 실제 날짜를 확인해 주세요."
            elif not all(actual_dates):
                result["reason"] = "OCR에서 유일하고 유효한 기간을 확인하지 못했습니다. 이미지의 날짜를 직접 확인해 주세요."
            elif expected_dates == actual_dates and _strong(source, baseline_agrees=baseline_dates == actual_dates):
                result.update(status="pass", reason="OCR에서 추출한 시작일·종료일이 기준과 일치합니다. 날짜 구분 기호와 공백은 정규화했습니다.")
            elif expected_dates != actual_dates and _strong(source, conflict=True):
                result.update(status="fail", reason=f"OCR의 기간({actual_dates[0]} ~ {actual_dates[1]})이 기준의 기간과 다릅니다. 원본 이미지를 함께 확인해 주세요.")
            else:
                result["reason"] = _review_reason(source, "날짜")
            results.append(result)
            continue

        # A title mismatch is only asserted for a single parseable festival name.
        if label in TITLE_LABELS and separator and re.search(r"(?:축제|페스티벌)$", value):
            source = [by_id[item_id] for item_id in fields["event_name"]["source_ocr_ids"]]
            observed = fields["event_name"]["value"]
            baseline_title = baseline_fields.get("event_name", {}).get("value")
            expected = re.sub(r"^\d{4}(?!\d)\s*(?:년\s*)?", "", value)
            result["evidence"] = _evidence(source)
            result["observed_text"] = " / ".join(item["text"] for item in source)
            if observed and normalized(observed) == normalized(expected) and _strong(source, baseline_agrees=baseline_title == observed):
                result.update(status="pass", reason="OCR에서 추출한 행사명이 기준과 일치합니다. 선행 연도와 공백은 정규화했습니다.")
            elif observed and normalized(observed) != normalized(expected) and _strong(source, conflict=True):
                result.update(status="fail", reason="OCR에서 추출한 하나의 행사명이 기준과 다릅니다. 표시된 문구를 원본과 대조해 주세요.")
            else:
                result["reason"] = (_review_reason(source, "행사명") if observed else
                                    "행사명의 유일한 OCR 근거를 확인하지 못했습니다. 누락으로 단정하지 않고 확인 대상으로 남겼습니다.")
            results.append(result)
            continue

        found = []
        all_found = True
        all_supported = True
        has_candidate_only_match = False
        has_missing_request = False
        for requested_text in requested:
            target = normalized(requested_text)
            matches = [item for item in records if _literal_matches(target, normalized(item["text"]))]
            all_supported = all_supported and _strong(matches, baseline_agrees=True, baseline_target=target)
            if not matches:
                all_found = False
                # 원문에 없고 재인식 후보에만 있는 문구도 확인할 근거로 보여 준다.
                # 후보 일치만으로 all_found나 자동 통과를 만들지는 않는다.
                matches = [item for item in records if any(
                    _literal_matches(target, normalized(candidate["text"]))
                    for candidate in item.get("alternatives", []))]
                has_candidate_only_match = has_candidate_only_match or bool(matches)
                has_missing_request = has_missing_request or not matches
            for item in matches:
                if item not in found:
                    found.append(item)
        result["evidence"] = _evidence(found)
        result["observed_text"] = " / ".join(item["text"] for item in found)
        if all_found and all_supported:
            result.update(status="pass", reason="기준에 지정된 문구가 OCR 근거에서 확인됐습니다. 공백과 유니코드 표현만 정규화했습니다.")
        elif all_found:
            result["reason"] = _review_reason(found)
        else:
            result["reason"] = ("일부 요청 문구는 재인식 후보에만 있고 현재 OCR과 다릅니다. 두 문구와 원본을 확인해 주세요."
                                if has_candidate_only_match and not has_missing_request
                                else "지정 문구 전체를 OCR에서 확인하지 못했습니다. OCR 인식 누락일 수 있으므로 이미지에 없다고 판정하지 않았습니다.")
        results.append(result)
    return results
