"""OCR 좌표로 인접 제목·날짜를 결합한다. 기대 문구와 라벨은 사용하지 않는다."""
from __future__ import annotations

from datetime import date
import re
import unicodedata

from .extract_event import extract_event, _validate_records, WEEKDAYS

TITLE_END = re.compile(r"(?:축제|페스티벌)$")
TITLE_TOKEN = re.compile(r"[가-힣\s·]+$")
DATE_TOKEN = re.compile(r"[0-9\s./~∼〜～–—\-()（）월화수목금토일년]+$")
WEEKDAY = r"\s*(?:[（(]?\s*([월화수목금토일])(?:요일)?\s*[）)]?)?"
RANGE = re.compile(
    r"(?<!\d)(\d{4})\s*[./년-]\s*(\d{1,2})\s*[./월-]\s*(\d{1,2})(?!\d)(?:\s*\.|일)?" + WEEKDAY
    + r"\s*[~∼〜～–—-]\s*(?:(\d{4})\s*[./년-]\s*)?"
    + r"(?:(\d{1,2})\s*[./월-]\s*)?(\d{1,2})(?!\d)(?:\s*\.|일)?" + WEEKDAY
)


def text_of(record):
    """문자를 보정하지 않고 유니코드·앞뒤 공백만 정규화한다."""
    return unicodedata.normalize("NFC", record["text"]).strip()


def follows(first, second):
    """글자 높이·겹침·거리로 읽기 순서의 이웃을 찾는다. 화면 절대 위치는 쓰지 않는다."""
    a, b = first["bbox_xyxy"], second["bbox_xyxy"]
    ah, bh = a[3] - a[1], b[3] - b[1]
    if min(ah, bh) / max(ah, bh) < .55:
        return False
    horizontal = (b[0] >= a[2] and b[0] - a[2] <= min(ah, bh) * 1.2
                  and min(a[3], b[3]) - max(a[1], b[1]) >= .6 * min(ah, bh))
    overlap = min(a[2], b[2]) - max(a[0], b[0])
    vertical = ((b[1] + b[3] - a[1] - a[3]) / 2 > .5 * min(ah, bh)
                and -.35 * min(ah, bh) <= b[1] - a[3] <= .85 * max(ah, bh)
                and overlap >= .55 * min(a[2] - a[0], b[2] - b[0]))
    return horizontal or vertical


def group_evidence(kind, members):
    """결합 후보에도 구성 OCR ID와 개별 박스·합집합 박스를 남긴다."""
    boxes = [r["bbox_xyxy"] for r in members]
    return {"kind": kind, "source_ocr_ids": [r["id"] for r in members],
            "text": " ".join(text_of(r) for r in members), "source_boxes": boxes,
            "bbox_xyxy": [min(b[0] for b in boxes), min(b[1] for b in boxes),
                          max(b[2] for b in boxes), max(b[3] for b in boxes)]}


def clear_link(first, second, records):
    """사이에 다른 OCR 줄이 있으면 건너뛰어 제목·날짜를 합치지 않는다."""
    if not follows(first, second):
        return False
    a, b = first["bbox_xyxy"], second["bbox_xyxy"]
    ay, by = (a[1] + a[3]) / 2, (b[1] + b[3]) / 2
    if by - ay <= .5 * min(a[3] - a[1], b[3] - b[1]):
        return True
    left, right = max(a[0], b[0]), min(a[2], b[2])
    return not any(r["id"] not in {first["id"], second["id"]}
                   and ay < (r["bbox_xyxy"][1] + r["bbox_xyxy"][3]) / 2 < by
                   and min(right, r["bbox_xyxy"][2]) > max(left, r["bbox_xyxy"][0]) for r in records)


def title_candidates(records):
    """축제명 끝말 앞의 인접한 제목 조각을 최대 네 줄까지 역순으로 찾는다."""
    candidates = []
    tokens = [r for r in records if TITLE_TOKEN.fullmatch(re.sub(r"^\d{4}(?!\d)\s*(?:년\s*)?", "", text_of(r)))]
    for tail in tokens:
        if not TITLE_END.search(text_of(tail)):
            continue
        chain, ambiguous = [tail], False
        while len(chain) < 4:
            previous = [r for r in tokens if r not in chain and not TITLE_END.search(text_of(r)) and clear_link(r, chain[0], records)]
            if len(previous) > 1:
                ambiguous = True
                break
            if not previous:
                break
            chain.insert(0, previous[0])
        if len(chain) == 4 and any(r not in chain and follows(r, chain[0]) for r in tokens):
            ambiguous = True
        candidate = group_evidence("title", chain)
        value = re.sub(r"\s+", "", re.sub(r"^\d{4}(?!\d)\s*(?:년\s*)?", "", candidate["text"]))
        candidate.update(value=value, ambiguous=ambiguous or value in {"축제", "페스티벌"})
        candidates.append(candidate)
    return candidates


def date_text(members):
    """독립 연도 줄은 점으로 연결하고, 인쇄된 날짜 구분 기호는 그대로 유지한다."""
    parts = [re.sub(r"\s*\d+\s*일간\s*$", "", text_of(r)) for r in members]
    if len(parts) > 1 and re.fullmatch(r"\d{4}\s*년?", parts[0]):
        parts[0] = re.sub(r"\s*년$", "", parts[0]) + "."
    return " ".join(parts)


def parse_range(text):
    """월 생략을 같은 달로 해석하되 실제 날짜·순서·요일을 모두 검증한다."""
    matches = list(RANGE.finditer(text))
    pairs = []
    for match in matches:
        year, month, day, weekday, end_year, end_month, end_day, end_weekday = match.groups()
        start = date(int(year), int(month), int(day))
        end = date(int(end_year or year), int(end_month or month), int(end_day))
        if end < start:
            raise ValueError("종료일이 시작일보다 빠르며 연도·월을 추측하지 않습니다.")
        for value, observed in ((start, weekday), (end, end_weekday)):
            if observed and WEEKDAYS[value.weekday()] != observed:
                raise ValueError("인쇄된 요일과 실제 날짜가 다릅니다.")
        pairs.append((start.isoformat(), end.isoformat()))
    return pairs


def date_candidates(records):
    """인접한 날짜 조각만 최대 네 줄·256개 후보로 결합하고 충돌을 남긴다."""
    tokens = [r for r in records if DATE_TOKEN.fullmatch(date_text([r]))]
    candidates, invalid_ids, visited = [], set(), 0
    for start in tokens:
        paths = [[start]]
        while paths and len(candidates) < 256:
            visited += 1
            if visited > 1024:
                return candidates, {r["id"] for r in tokens}
            chain = paths.pop()
            text = date_text(chain)
            try:
                pairs = parse_range(text)
            except ValueError:
                invalid_ids.update(r["id"] for r in chain)
                pairs = []
            if pairs:
                candidate = group_evidence("date", chain)
                candidate.update(text=text, values=pairs)
                candidates.append(candidate)
                continue
            if len(chain) < 4:
                paths.extend(chain + [r] for r in tokens if r not in chain and clear_link(chain[-1], r, records))
                # 후보 수는 인접 잡음이 많아도 일정하게 제한한다.
                if len(paths) > 256:
                    invalid_ids.update(r["id"] for r in tokens)
                    paths = []
    return candidates, invalid_ids


def field(value, ids, reason=None):
    """결합 결과를 기존 필드 계약으로 반환하고 후보임을 표시한다."""
    return {"value": value, "source_ocr_ids": list(dict.fromkeys(ids)),
            "status": "extracted" if value is not None else "needs_review",
            "normalization": ["Geometry-linked OCR lines; no character correction"], "reason": reason}


def extract_event_multiline(records):
    """기존 장소·선택 필드는 유지하고 제목·날짜에 근거 기반 결합을 적용한다."""
    _validate_records(records)
    result = extract_event(records)
    result.update(method="geometry_line_rules_v2", groups=[])
    if len(records) > 500:
        for name in ("event_name", "start_date", "end_date"):
            result["fields"][name] = field(None, [], "OCR 영역 수 제한을 초과했습니다.")
        return result
    titles = title_candidates(records)
    values = {c["value"] for c in titles if not c["ambiguous"]}
    title_ids = [i for c in titles for i in c["source_ocr_ids"]]
    value = next(iter(values)) if len(values) == 1 and not any(c["ambiguous"] for c in titles) else None
    result["fields"]["event_name"] = field(value, title_ids, None if value else "제목 조각 또는 복수 후보를 확정하지 않았습니다.")
    dates, invalid_ids = date_candidates(records)
    pairs = {tuple(pair) for c in dates for pair in c["values"]}
    date_ids = [i for c in dates for i in c["source_ocr_ids"]]
    # 해석 실패한 완전한 연도 표기가 다른 곳에 있으면 기간 하나로 확정하지 않는다.
    unresolved = [r["id"] for r in records if re.search(r"\d{4}\s*[./년-]\s*\d", text_of(r)) and r["id"] not in date_ids]
    pair = next(iter(pairs)) if len(pairs) == 1 and not invalid_ids and not unresolved else (None, None)
    for name, value in zip(("start_date", "end_date"), pair):
        result["fields"][name] = field(value, date_ids + list(invalid_ids) + unresolved,
                                      None if value else "날짜 범위·요일 또는 후보 충돌을 확인해야 합니다.")
    result["groups"] = titles + dates
    # v1의 제목·날짜 미추출 경고는 v2 결과에 맞게 교체한다.
    result["warnings"] = [w for w in result["warnings"] if w["code"] not in {"event_name_needs_review", "date_range_needs_review"}]
    for name in ("event_name", "start_date"):
        if result["fields"][name]["value"] is None:
            result["warnings"].append({"code": name + "_needs_review", "message": result["fields"][name]["reason"],
                                       "source_ocr_ids": result["fields"][name]["source_ocr_ids"]})
    return result
