"""Conservative, evidence-linked checks against a user's frozen criteria.

Only OCR records supplied by the caller are used. A missing OCR match is not
proof that the image omits text. Visual checks always require human review.
"""
from __future__ import annotations

import re
import unicodedata

from .extract_event import extract_event


MATCH_CONFIDENCE = 0.60
CONFLICT_CONFIDENCE = 0.85
VISUAL_HINT = re.compile(r"로고|색상|색깔|컬러|잘림|잘리|여백|배치|정렬|해상도|가독성|선명|디자인|배경|상단|하단|왼쪽|오른쪽|중앙|폰트|글꼴|굵기")
NEGATIVE_HINT = re.compile(r"금지|제외|삭제|없어|없음|않|사용\s*불가|불포함")
QUOTES = re.compile(r'["“「『]([^"”」』]+)["”」』]|\'([^\']+)\'')
TITLE_LABELS = {"행사명", "축제명", "제목", "이벤트명"}
DATE_LABELS = {"기간", "행사기간", "일정", "행사일정", "날짜"}


def normalized(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text))


def _literal_matches(target: str, observed: str) -> bool:
    # A requested 3,000 must not pass on an observed 13,000 or 3,0000.
    prefix = r"(?<!\d)" if target[:1].isdigit() else ""
    suffix = r"(?!\d)" if target[-1:].isdigit() else ""
    return bool(target and re.search(prefix + re.escape(target) + suffix, observed))


def _evidence(records: list[dict]) -> list[dict]:
    return [
        {"text": item["text"], "bbox_xyxy": item["bbox_xyxy"], "ocr_id": item["id"]}
        for item in records
    ]


def _strong(records: list[dict], threshold: float = MATCH_CONFIDENCE) -> bool:
    return bool(records) and all(
        item.get("confidence") is not None and item["confidence"] >= threshold
        for item in records
    )


def _structured_dates(text: str) -> tuple[str, str] | None:
    item = {"id": "criterion", "text": text, "confidence": None, "bbox_xyxy": [0, 0, 1, 1]}
    fields = extract_event([item])["fields"]
    start, end = fields["start_date"]["value"], fields["end_date"]["value"]
    return (start, end) if start and end else None


def review_criteria(criteria: list[dict], records: list[dict]) -> list[dict]:
    """Return a result for every selected criterion, preserving its ID and order."""
    fields = extract_event(records)["fields"]
    by_id = {item["id"]: item for item in records}
    results = []
    for criterion in criteria:
        result = {
            "criterion_id": criterion["id"], "criterion": criterion["text"],
            "type": criterion["type"], "status": "review", "observed_text": "",
            "reason": "", "evidence": [],
        }
        text = criterion["text"].strip()
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
            source = [by_id[item_id] for item_id in fields["start_date"]["source_ocr_ids"]]
            result["evidence"] = _evidence(source)
            result["observed_text"] = " / ".join(item["text"] for item in source)
            if expected_dates is None:
                result["reason"] = "기준의 기간을 해석할 수 없습니다. YYYY.MM.DD ~ MM.DD 형식과 실제 날짜를 확인해 주세요."
            elif not all(actual_dates):
                result["reason"] = "OCR에서 유일하고 유효한 기간을 확인하지 못했습니다. 이미지의 날짜를 직접 확인해 주세요."
            elif expected_dates == actual_dates and _strong(source):
                result.update(status="pass", reason="OCR에서 추출한 시작일·종료일이 기준과 일치합니다. 날짜 구분 기호와 공백은 정규화했습니다.")
            elif expected_dates != actual_dates and _strong(source, CONFLICT_CONFIDENCE):
                result.update(status="fail", reason=f"OCR의 기간({actual_dates[0]} ~ {actual_dates[1]})이 기준의 기간과 다릅니다. 원본 이미지를 함께 확인해 주세요.")
            else:
                result["reason"] = "날짜 OCR의 신뢰도가 충분하지 않아 자동 확정하지 않았습니다. 표시된 근거를 확인해 주세요."
            results.append(result)
            continue

        # A title mismatch is only asserted for a single parseable festival name.
        if label in TITLE_LABELS and separator and re.search(r"(?:축제|페스티벌)$", value):
            source = [by_id[item_id] for item_id in fields["event_name"]["source_ocr_ids"]]
            observed = fields["event_name"]["value"]
            expected = re.sub(r"^\d{4}(?!\d)\s*(?:년\s*)?", "", value)
            result["evidence"] = _evidence(source)
            result["observed_text"] = " / ".join(item["text"] for item in source)
            if observed and normalized(observed) == normalized(expected) and _strong(source):
                result.update(status="pass", reason="OCR에서 추출한 행사명이 기준과 일치합니다. 선행 연도와 공백은 정규화했습니다.")
            elif observed and normalized(observed) != normalized(expected) and _strong(source, CONFLICT_CONFIDENCE):
                result.update(status="fail", reason="OCR에서 추출한 하나의 행사명이 기준과 다릅니다. 표시된 문구를 원본과 대조해 주세요.")
            else:
                result["reason"] = "행사명의 유일한 OCR 근거 또는 신뢰도가 충분하지 않습니다. 누락으로 단정하지 않고 확인 대상으로 남겼습니다."
            results.append(result)
            continue

        found = []
        all_found = True
        for requested_text in requested:
            target = normalized(requested_text)
            matches = [item for item in records if _literal_matches(target, normalized(item["text"]))]
            if not matches:
                all_found = False
            for item in matches:
                if item not in found:
                    found.append(item)
        result["evidence"] = _evidence(found)
        result["observed_text"] = " / ".join(item["text"] for item in found)
        if all_found and _strong(found):
            result.update(status="pass", reason="기준에 지정된 문구가 OCR 근거에서 확인됐습니다. 공백과 유니코드 표현만 정규화했습니다.")
        elif all_found:
            result["reason"] = "문구 후보가 있으나 OCR 신뢰도가 낮습니다. 표시된 근거를 확인해 주세요."
        else:
            result["reason"] = "지정 문구 전체를 OCR에서 확인하지 못했습니다. OCR 인식 누락일 수 있으므로 이미지에 없다고 판정하지 않았습니다."
        results.append(result)
    return results
