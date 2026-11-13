"""영상 로고 1부(위치 후보)의 OpenAI 경로(일반 자료, 2026-11-12 사용자 결정: 검수 첫 화면의 보안 여부 선택).

보안이 필요 없는 일반 자료는 1초 프레임마다 원본 로고가 어디에 있는지만 OpenAI 비전 모델로 찾는다.
정밀 대조(2부)는 기존 RunPod 워커(kind="compare")를 그대로 쓴다. 보안 자료는 이 모듈을 쓰지 않는다.

돌려주는 data는 RunPod 후보(candidates.build_candidates)와 같은 형식이라 확정 화면·정밀 대조가 그대로 동작한다.
  - 같은 초에서 겹치는 자리(IoU 0.5 이상)는 원본이 달라도 한 자리로 묶는다
  - 3초 이내로 이어지고 위치가 가깝고 같은 원본으로 찾은 자리를 시간 묶음(group) 하나로 잇는다(candidates.py와 같은 기준,
    SigLIP2 모양 유사도 대신 '같은 원본으로 찾았는지'를 쓴다)
  - score·rank[원본] = 그 원본으로 찾은 초별 확신도 상위 3개 평균(OCR 가산점 없음), 찾지 않은 원본은 0
  - box[원본][초]·rep[원본] = 화면용 프레임(긴 변 DISPLAY_LONG) 좌표
  - refs[원본].bonus_basis = "openai", ocr = {}
한 프레임의 호출 실패는 그 초만 빼고 계속한다(failed_seconds). 모든 프레임이 실패하면 작업을 실패로 둔다.
키·모델은 backend/agent_framework/model_route.openai_settings(), 동시 요청 수는 VIDEO_LOGO_OPENAI_CONCURRENCY(기본 4).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading

from PIL import Image

from backend.app.poster.logos.openai_candidates import _area, _iou, locate, reference_parts, route_settings
from backend.app.video.logos.client import WorkerError

DISPLAY_LONG = 1280  # candidates.py와 같은 화면용 프레임 긴 변
GAP = 3              # candidates.py와 같은 시간 묶음 간격(초)
FRAME_LONG = 1280    # OpenAI에 보내는 프레임 긴 변


def _concurrency() -> int:
    try:
        return min(16, max(1, int(os.getenv("VIDEO_LOGO_OPENAI_CONCURRENCY", "4"))))
    except ValueError:
        return 4


def _near(a, b) -> bool:
    ratio = max(_area(a), _area(b)) / max(1, min(_area(a), _area(b)))
    ca, cb = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2), ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    close = _iou(a, b) >= .3 or (abs(ca[0] - cb[0]) + abs(ca[1] - cb[1])) < .5 * max(a[2] - a[0], a[3] - a[1])
    return ratio < 2.5 and close


def build_candidates(frames_dir: Path, frames: list[dict], refs: list[dict], progress=lambda p, d: None, *,
                     settings=None, transport=None) -> dict:
    """frames = [{"sec", "size"}](candidates.sample_frames), refs = [{"name", "image": PIL, "text"}] → 확정 화면용 data."""
    settings = route_settings(settings)
    parts = reference_parts(refs)
    total = max(1, len(frames))
    done, failed, errors, lock = [0], [], [], threading.Lock()
    progress(5, "OpenAI로 원본 로고 위치를 찾는 중")

    def task(frame: dict):
        sec = frame["sec"]
        try:
            with Image.open(frames_dir / "full" / f"{sec}.jpg") as image:
                found = locate(image, refs, settings=settings, subject="VIDEO FRAME", ref_parts=parts,
                               transport=transport, send_long=FRAME_LONG)
        except WorkerError as error:
            with lock:
                failed.append(sec); errors.append(error)
            found = []
        with lock:
            done[0] += 1
            progress(5 + 85 * done[0] / total, f"OpenAI 위치 탐지 {done[0]}/{total}초")
        return sec, found

    with ThreadPoolExecutor(max_workers=_concurrency()) as pool:
        results = list(pool.map(task, frames))
    if frames and len(failed) == len(frames):
        raise errors[0]

    # 같은 초 안에서 겹치는 자리는 원본이 달라도 한 자리로 묶는다.
    spots = []
    for sec, found in sorted(results, key=lambda item: item[0]):
        here = []
        for index, box, confidence in sorted(found, key=lambda item: -item[2]):
            host = next((spot for spot in here if _iou(spot["box"], box) >= .5), None)
            if host:
                host["members"].append((index, box, confidence))
            else:
                here.append({"sec": sec, "box": box, "members": [(index, box, confidence)]})
        spots += here
    # 3초 이내로 이어지고 위치가 가까우며 같은 원본으로 찾은 자리를 시간 묶음으로 잇는다.
    groups = []
    for spot in spots:
        mine = {member[0] for member in spot["members"]}
        best, best_iou = None, -1.0
        for group in groups:
            last = group[-1]
            if not 0 < spot["sec"] - last["sec"] <= GAP or not mine & {member[0] for member in last["members"]}:
                continue
            if _near(last["box"], spot["box"]) and _iou(last["box"], spot["box"]) > best_iou:
                best, best_iou = group, _iou(last["box"], spot["box"])
        (best.append(spot) if best is not None else groups.append([spot]))

    size = frames[0]["size"] if frames else [0, 0]
    scale = DISPLAY_LONG / max(size) if frames and max(size) else 1.0

    def scaled(box):
        return [round(value * scale, 1) for value in box]

    out_groups = []
    for group in groups:
        boxes, scores, reps = [], [], []
        for index in range(len(refs)):
            confidence_at, box_at = {}, {}
            for spot in group:
                hits = [(confidence, box) for ref, box, confidence in spot["members"] if ref == index]
                if hits:
                    confidence, box = max(hits, key=lambda hit: hit[0])
                    confidence_at[spot["sec"]], box_at[spot["sec"]] = confidence, box
                else:  # 다른 원본으로 찾은 자리도 같은 위치로 둔다(사람이 이 원본으로도 확정할 수 있게)
                    box_at[spot["sec"]] = spot["box"]
            top = sorted(confidence_at.values(), reverse=True)[:3]
            scores.append(round(sum(top) / len(top), 4) if top else 0.0)
            if confidence_at:
                sec = max(confidence_at, key=confidence_at.get)
            else:
                sec = max(group, key=lambda spot: max(member[2] for member in spot["members"]))["sec"]
            boxes.append({str(sec_): scaled(box) for sec_, box in box_at.items()})
            reps.append([sec, scaled(box_at[sec])])
        out_groups.append({"secs": sorted(spot["sec"] for spot in group), "box": boxes, "score": scores,
                           "rank": list(scores), "rep": reps})
    progress(95, "찾은 위치를 후보로 정리하는 중")
    return {"size": [round(size[0] * scale), round(size[1] * scale)], "seconds": len(frames), "detector": "openai",
            "failed_seconds": sorted(failed),
            "refs": [{"name": r["name"], "ocr": {}, "bonus_text": " ".join((r.get("text") or "").replace("|", " ").split()),
                      "bonus_basis": "openai"} for r in refs],
            "groups": out_groups}
