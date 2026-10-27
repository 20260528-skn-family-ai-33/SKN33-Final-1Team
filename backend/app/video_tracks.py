"""숏폼 검수 4·5단계 — 자막 트랙, 구간 분할, 대표 프레임.

계획 문서 4단계: 같은 자리(IoU ≥ 0.5)에서 같은 문구로 읽힌 자막 줄을 트랙으로 묶는다.
오인식 '할인→할언'과 실제 변경 '50%→70%'의 유사도가 같으므로, 숫자·% 토큰은 정확히 비교하고
나머지만 자모 편집거리로 본다. 작은 차이는 묶을 때만 허용하고, 다르게 읽힌 글자는 variants로 모두 남긴다.
틀렸는지는 6단계(video_judge)가 판단한다.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import statistics
import unicodedata

import numpy as np

from .video_frames import BASE_MS

TRACK_IOU = 0.5
STRONG_CONFIDENCE = 0.90   # 다른 문구가 이 이상이면 그 자리의 기존 트랙을 바로 닫는다(50→70→50 교체가 숨지 않게)
MISS_EXACT, MISS_APPROX = 2, 1  # 누락 허용 프레임: 정확 일치만 있던 트랙 2 / 근사 일치가 섞인 트랙 1
NUMBER = re.compile(r"\d+(?:[.,:]\d+)*%?|%")


# ---------------------------------------------------------------------------
# 문구 비교
# ---------------------------------------------------------------------------
def grouping_form(text: str) -> str:
    """묶기 비교용: NFC + 공백 제거. 대소문자·기호는 그대로 둔다(편집 1회로 센다)."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text or ""))


def jamo(text: str) -> list[str]:
    """한글 음절은 초·중·종성으로 풀고, 나머지 글자는 그대로 둔다."""
    out = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out += [chr(0x1100 + code // 588), chr(0x1161 + code % 588 // 28)]
            if code % 28:
                out.append(chr(0x11A7 + code % 28))
        else:
            out.append(ch)
    return out


def edit_distance(a, b) -> int:
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def allowed_edits(length: int) -> int:
    """문서의 허용 편집 수: min(3, max(1, round(0.15×자모 길이))), 4자모 미만은 0."""
    return 0 if length < 4 else min(3, max(1, round(0.15 * length)))


def text_changed(a: str, b: str) -> bool:
    """숫자·% 토큰이 하나라도 다르면 바뀐 문구, 나머지는 자모 편집거리가 허용치를 넘으면 바뀐 문구."""
    ga, gb = grouping_form(a), grouping_form(b)
    if ga == gb:
        return False
    if NUMBER.findall(ga) != NUMBER.findall(gb):
        return True
    ja, jb = jamo(NUMBER.sub("", ga)), jamo(NUMBER.sub("", gb))
    return edit_distance(ja, jb) > allowed_edits(max(len(ja), len(jb)))


def agreement(a: str, b: str) -> float:
    ja, jb = jamo(grouping_form(a)), jamo(grouping_form(b))
    longest = max(len(ja), len(jb))
    return 1.0 if not longest else 1 - edit_distance(ja, jb) / longest


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# 4단계 — 트랙
# ---------------------------------------------------------------------------
def build_tracks(frames: list[dict], lines: list[dict], duration_ms: int) -> list[dict]:
    """1초 프레임의 '남긴' 줄만 쓴다. OCR이 실패한 프레임은 누락으로 세지 않는다."""
    by_frame: dict[str, list[dict]] = {}
    for line in lines:
        if line["kept"]:
            by_frame.setdefault(line["frame_id"], []).append(line)
    base = sorted((f for f in frames if f["sampling"] == "base"), key=lambda f: f["time_ms"])
    tracks, active = [], []
    for frame in base:
        if frame.get("ocr_status") == "failed":
            continue
        matched, closing = set(), set()
        for line in sorted(by_frame.get(frame["frame_id"], []), key=lambda item: -item["confidence"]):
            best, best_iou = None, 0.0
            for index, track in enumerate(active):
                if index in matched or index in closing:
                    continue
                overlap = iou(track["readings"][-1]["bbox_xyxy"], line["bbox_xyxy"])
                if overlap < TRACK_IOU:
                    continue
                if not text_changed(track["readings"][-1]["text"], line["text"]):
                    if overlap > best_iou:
                        best, best_iou = index, overlap
                elif line["confidence"] >= STRONG_CONFIDENCE:
                    closing.add(index)
            reading = {key: line[key] for key in ("frame_id", "time_ms", "text", "confidence", "bbox_xyxy", "role")}
            if best is None:
                active.append({"readings": [reading], "misses": 0, "exact": True})
                matched.add(len(active) - 1)
            else:
                track = active[best]
                track["exact"] = track["exact"] and grouping_form(track["readings"][-1]["text"]) == grouping_form(line["text"])
                track["readings"].append(reading)
                track["misses"] = 0
                matched.add(best)
        survivors = []
        for index, track in enumerate(active):
            if index not in matched:
                track["misses"] += 1
            if index in closing or track["misses"] > (MISS_EXACT if track["exact"] else MISS_APPROX):
                tracks.append(track)
            else:
                survivors.append(track)
        active = survivors
    tracks.extend(active)
    tracks.sort(key=lambda t: (t["readings"][0]["time_ms"], t["readings"][0]["bbox_xyxy"][1]))
    return [_finish(i, t["readings"], duration_ms) for i, t in enumerate(tracks, 1)]


def _finish(number: int, readings: list[dict], duration_ms: int) -> dict:
    """문구 투표로 대표 문구를 정하고, 과반이 없으면 requires_review를 붙인다."""
    votes = Counter(r["text"].strip() for r in readings)
    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], -statistics.fmean(
        r["confidence"] for r in readings if r["text"].strip() == kv[0])))
    text, count = ranked[0]
    agreeing = [r for r in readings if r["text"].strip() == text]
    best = max(agreeing, key=lambda r: r["confidence"])
    roles = Counter(r.get("role") for r in readings)
    return {
        "id": f"T{number:03d}", "text": text, "start_ms": readings[0]["time_ms"],
        "end_ms": min(duration_ms, readings[-1]["time_ms"] + BASE_MS),
        "confidence": round(statistics.median(r["confidence"] for r in agreeing), 4),
        "bbox_xyxy": best["bbox_xyxy"], "rep_reading_frame": best["frame_id"],
        "requires_review": count * 2 <= len(readings), "vote": {"count": count, "total": len(readings)},
        "role": roles.most_common(1)[0][0],
        "readings": [{key: r[key] for key in ("frame_id", "time_ms", "text", "confidence", "bbox_xyxy")} for r in readings],
        "variants": [{"text": r["text"], "frame_id": r["frame_id"], "time_ms": r["time_ms"]}
                     for r in readings if r["text"].strip() != text],
    }


def text_groups(tracks: list[dict]) -> list[dict]:
    """반복 문구(엔딩 로고 등)는 같은 문구 키로 묶는다. 유사도 군집은 숫자 차이를 합쳐 버려 쓰지 않는다."""
    groups: dict[str, list[str]] = {}
    for track in tracks:
        groups.setdefault(grouping_form(track["text"]), []).append(track["id"])
    return [{"key": key, "track_ids": ids} for key, ids in groups.items() if len(ids) > 1]


# ---------------------------------------------------------------------------
# 4단계 — 구간
# ---------------------------------------------------------------------------
def build_segments(tracks: list[dict], frames: list[dict], duration_ms: int, scene_grid: list[int]) -> list[dict]:
    """구간 경계 = 장면 컷 ∪ 보이는 트랙 집합이 바뀌는 시각. 구간들은 [0, 영상 길이)를 빈틈없이 덮는다."""
    edges = {0, duration_ms, *scene_grid}
    for track in tracks:
        edges.update((track["start_ms"], track["end_ms"]))
    edges = sorted(e for e in edges if 0 <= e <= duration_ms)
    base = [f for f in frames if f["sampling"] == "base"]
    segments = []
    for start, end in zip(edges, edges[1:]):
        if end <= start:
            continue
        visible = [t["id"] for t in tracks if t["start_ms"] <= start < t["end_ms"]]
        if segments and segments[-1]["track_ids"] == visible and start not in scene_grid:
            segments[-1]["end_ms"] = end  # 같은 트랙 집합이 이어지면 합친다
            segments[-1]["frame_ids"] += [f["frame_id"] for f in base if start <= f["time_ms"] < end]
            continue
        segments.append({"start_ms": start, "end_ms": end, "track_ids": visible,
                         "frame_ids": [f["frame_id"] for f in base if start <= f["time_ms"] < end]})
    for number, segment in enumerate(segments, 1):
        segment["id"] = f"S{number:02d}"
    return segments


# ---------------------------------------------------------------------------
# 5단계 — 대표 프레임
# ---------------------------------------------------------------------------
def _stroke_sharpness(path: Path, boxes: list) -> float:
    """화면 전체가 아니라 자막 글자 영역의 라플라시안 분산(배경 선명도에 끌려가지 않게)."""
    from PIL import Image
    with Image.open(path) as image:
        gray = np.asarray(image.convert("L"), dtype=np.float64)
    values = []
    for x1, y1, x2, y2 in boxes:
        crop = gray[max(0, int(y1)):int(y2) + 1, max(0, int(x1)):int(x2) + 1]
        if crop.shape[0] >= 3 and crop.shape[1] >= 3:
            lap = 4 * crop[1:-1, 1:-1] - crop[:-2, 1:-1] - crop[2:, 1:-1] - crop[1:-1, :-2] - crop[1:-1, 2:]
            values.append(float(lap.var()))
    return statistics.fmean(values) if values else 0.0


def choose_representatives(segments: list[dict], tracks: list[dict], frames: list[dict], frame_dir: Path) -> None:
    """후보는 구간 양 끝과 OCR 실패 프레임을 뺀 프레임. 점수 = 획 선명도 + 평균 인식 점수 + 투표 문구 일치도."""
    by_id = {t["id"]: t for t in tracks}
    status = {f["frame_id"]: f.get("ocr_status") for f in frames}
    for segment in segments:
        ids = [fid for fid in segment["frame_ids"] if status.get(fid) != "failed"]
        candidates = ids[1:-1] or ids
        scored = []
        for fid in candidates:
            readings = [(by_id[tid], r) for tid in segment["track_ids"] for r in by_id[tid]["readings"] if r["frame_id"] == fid]
            confidence = statistics.fmean(r["confidence"] for _, r in readings) if readings else 0.0
            agree = statistics.fmean(agreement(r["text"], t["text"]) for t, r in readings) if readings else 0.0
            sharp = _stroke_sharpness(frame_dir / f"{fid}.jpg", [r["bbox_xyxy"] for _, r in readings]) if readings else 0.0
            scored.append([fid, sharp, confidence, agree])
        top = max((s[1] for s in scored), default=0.0) or 1.0
        ranked = sorted(scored, key=lambda s: -(s[1] / top + s[2] + s[3]))
        if not any(s[2] for s in scored):  # 글자가 없는 구간은 가운데 프레임을 대표로
            ranked = sorted(scored, key=lambda s: abs(candidates.index(s[0]) - len(candidates) // 2))
        segment["rep_order"] = [s[0] for s in ranked]
        segment["rep_frame_id"] = ranked[0][0] if ranked else None
