"""숏폼 검수 6단계 — 트랙 문구로 기준을 영상당 1회 판정하고, 한 글자라도 다른 문구를 '불일치'로 표시한다.

계획 문서 원칙: review_rules.py·extract_event.py·event_layout.py는 고치지 않고 앞뒤 처리만 둔다.
상태 변경은 낮추는 방향뿐이다 — 충족을 새로 만들거나 위반을 새로 만들지 않는다.
"""
from __future__ import annotations

import re
import unicodedata

from .review_rules import DATE_LABELS, QUOTES, TITLE_LABELS, normalized, review_criteria
from .video_tracks import edit_distance

# 선결 규칙(문서 6단계): 금지 조건 감지 목록(review_rules.NEGATIVE_HINT)에 '마세요' 등이 없어
# "'최고' 표현은 쓰지 마세요"가 충족으로 뒤집힌다. 공통 수정은 포스터와 함께(B 담당) 하고,
# 영상 경로에서는 충족을 확인 필요로 낮추기만 한다.
VIDEO_NEGATIVE_HINT = re.compile(r"마세요|마십시오|말\s*것|말아\s*주|하지\s*마|쓰지\s*마|넣지\s*마|금물")
SEVERITY = {"pass": "info", "review": "warn", "fail": "block"}
REVIEWER = {"pass": "auto", "fail": "auto", "review": "manager"}


def track_records(tracks: list[dict]) -> list[dict]:
    """엔진·보류 정보는 필수 — 엔진이 빠지면 EasyOCR 기준(0.60/0.85)이 적용된다(review_rules.py:60).
    트랙 신뢰도는 대표 문구와 같게 읽힌 프레임들의 중앙값이다."""
    return [{"id": t["id"], "text": t["text"], "confidence": t["confidence"], "bbox_xyxy": t["bbox_xyxy"],
             "recognition_engine": "paddleocr", "requires_review": bool(t["requires_review"])} for t in tracks]


def requested_texts(criterion: dict) -> list[str]:
    """review_rules와 같은 방식으로 기준에서 비교 문구를 꺼낸다(따옴표 우선, 없으면 '항목: 값')."""
    text = criterion["text"].strip()
    quoted = [first or second for first, second in QUOTES.findall(text)]
    if quoted:
        return quoted
    label, separator, value = text.partition(":")
    if not separator:
        label, separator, value = text.partition("：")
    if separator and value.strip() and normalized(label) not in DATE_LABELS:
        return [value.strip()]
    return []


def _windows(observed: str, words: int):
    tokens = observed.split()
    for size in {max(1, words - 1), words, words + 1}:
        for start in range(0, max(1, len(tokens) - size + 1)):
            yield " ".join(tokens[start:start + size])


def mismatch_kind(expected: str, observed: str) -> str | None:
    """기준 문구 하나와 화면 문구 조각 하나를 엄격 비교한다. 같으면 None."""
    expected, observed = unicodedata.normalize("NFC", expected).strip(), unicodedata.normalize("NFC", observed).strip()
    if expected == observed:
        return None
    flat_e, flat_o = normalized(expected), normalized(observed)
    if flat_e == flat_o:
        return "spacing"
    if flat_e.casefold() == flat_o.casefold():
        return "case"
    # 문서 ②는 '같은 단어 수'지만 OCR이 띄어쓰기를 자주 흘려 읽으므로('한정 50%'→'한정50%'),
    # 공백을 뺀 글자 기준으로 한 글자만 다르면 단어 수와 관계없이 불일치로 본다(더 엄격한 쪽).
    if len(flat_e) == len(flat_o) and edit_distance(flat_e, flat_o) == 1:
        return "one_character"
    return None


def find_mismatch(criteria: list[dict], results: list[dict], tracks: list[dict]) -> list[dict]:
    """① 근거 트랙에 variants가 있음 ② 같은 단어 수에서 한 글자·숫자만 다름 ③ 대소문자만 다름 ④ 띄어쓰기만 다름."""
    by_id = {t["id"]: t for t in tracks}
    found = []
    for criterion, result in zip(criteria, results):
        # 근거가 없는 기준도 건너뛰지 않는다 — '70%' 기준에 화면이 '50%'면 근거는 비지만 불일치는 있다.
        for track_id in {e["ocr_id"] for e in result["evidence"]}:
            track = by_id.get(track_id)
            if track and track["variants"]:
                found.append({"criterion_id": criterion["id"], "track_id": track_id, "kind": "variants",
                              "expected": None, "observed": track["text"],
                              "variants": sorted({v["text"] for v in track["variants"]})})
        for expected in requested_texts(criterion):
            if len(normalized(expected)) < 2:
                continue
            for track in tracks:
                exact = normalized(expected) in normalized(track["text"]) and expected in track["text"]
                for window in _windows(track["text"], len(expected.split())):
                    kind = mismatch_kind(expected, window)
                    if kind and not exact:
                        found.append({"criterion_id": criterion["id"], "track_id": track["id"], "kind": kind,
                                      "expected": expected, "observed": window})
                        break
    unique, seen = [], set()
    for item in found:
        key = (item["criterion_id"], item["track_id"], item["kind"], item["observed"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _label(criterion: dict) -> str:
    label, separator, _ = criterion["text"].partition(":")
    return normalized(label) if separator else ""


def judge(criteria: list[dict], tracks: list[dict], segments: list[dict], frames: list[dict]) -> dict:
    """review_criteria를 영상당 정확히 한 번 부른다(multiline=False — 500레코드 상한·시각이 다른 트랙 결합 문제)."""
    if not criteria:
        return {"results": [], "mismatch": [], "calls": 0}
    results = review_criteria(criteria, track_records(tracks), multiline=False)
    mismatch = find_mismatch(criteria, results, tracks)
    failed_frames = [f["frame_id"] for f in frames if f.get("ocr_status") == "failed"]
    by_track = {t["id"]: t for t in tracks}
    frame_meta = {f["frame_id"]: f for f in frames}
    for criterion, result in zip(criteria, results):
        notes = []
        if result["status"] == "pass" and any(m["criterion_id"] == criterion["id"] for m in mismatch):
            result["status"] = "review"
            notes.append("기준과 한 글자라도 다르게 읽힌 화면 문구가 있어 '불일치'로 사람 확인이 필요합니다.")
        if result["status"] == "fail" and failed_frames:
            result["status"] = "review"
            notes.append(f"OCR이 실패한 프레임 {len(failed_frames)}장이 있어 위반으로 확정하지 않았습니다.")
        if result["status"] == "fail" and _label(criterion) in TITLE_LABELS:
            result["status"] = "review"
            notes.append("영상에서는 실제 행사명이 읽히지 않고 다른 '…축제' 문구만 남을 수 있어 확인 필요로 낮췄습니다.")
        if result["status"] == "pass" and VIDEO_NEGATIVE_HINT.search(criterion["text"]):
            result["status"] = "review"
            notes.append("금지(부정) 조건은 문구가 보인다는 사실만으로 충족을 정할 수 없습니다.")
        if result["status"] == "pass":
            notes.append("(검사한 시점 기준 충족 — 1초 간격 표본)")
        if notes:
            result["reason"] = " ".join([result["reason"], *notes]).strip()
        result["mismatch"] = [m for m in mismatch if m["criterion_id"] == criterion["id"]]
        # 불일치 트랙도 근거 위치로 펼쳐 '00:12~00:15 위치 보기'로 바로 확인하게 한다
        located = result["evidence"] + [{"ocr_id": m["track_id"]} for m in result["mismatch"]
                                        if m["track_id"] not in {e["ocr_id"] for e in result["evidence"]}]
        result["evidence_segments"] = _expand(located, by_track, segments, frame_meta)
        result["whole_video"] = result["status"] == "review" and not result["evidence_segments"]
        result.update(severity=SEVERITY[result["status"]], reviewer=REVIEWER[result["status"]])
    return {"results": results, "mismatch": mismatch, "calls": 1}


def _expand(evidence: list[dict], tracks: dict, segments: list[dict], frames: dict) -> list[dict]:
    """근거의 트랙 ID를 구간마다 근거 하나(구간·프레임·time_ms·프레임 크기·bbox)로 펼친다."""
    out = []
    for item in evidence:
        track = tracks.get(item["ocr_id"])
        if not track:
            continue
        for segment in segments:
            if track["id"] not in segment["track_ids"]:
                continue
            inside = [r for r in track["readings"] if segment["start_ms"] <= r["time_ms"] < segment["end_ms"]]
            if not inside:
                continue
            reading = next((r for r in inside if r["frame_id"] == segment.get("rep_frame_id")), inside[0])
            frame = frames[reading["frame_id"]]
            out.append({"track_id": track["id"], "segment_id": segment["id"], "start_ms": segment["start_ms"],
                        "end_ms": segment["end_ms"], "frame_id": reading["frame_id"], "time_ms": reading["time_ms"],
                        "width": frame["width"], "height": frame["height"], "bbox_xyxy": reading["bbox_xyxy"],
                        "text": reading["text"]})
    return out
