"""동영상 자막 판독 — Qwen3-VL-8B-Instruct가 프레임을 직접 보고 화면 글자를 읽는다(3·3-1·4단계 대체).

PaddleOCR 대신 비전 언어 모델을 쓰는 자막 경로다. 로고 검수(`backend.app.video.logos`, `backend.app.logos`)와는
완전히 별개이며 그쪽 코드·워커를 호출하지 않는다.

- 3단계(판독): vLLM의 OpenAI 호환 `/chat/completions`에 프레임 1장씩 보낸다. 프롬프트·이미지 크기(긴 변 1280)·
  temperature 0·max_tokens 2048은 모델 비교 평가 때와 같은 조건이다.
- 3-1단계(줄 분류): 모델이 돌려준 role을 그대로 쓴다(bbox 특징 분류기를 거치지 않음).
- 근거 상자: 모델이 글자 묶음마다 bbox_2d(0~1000 상대 좌표)를 함께 준다(2026-11-16). 원본 프레임 픽셀로 바꿔
  bbox_xyxy에 쓰고 bbox_source='model'로 남긴다. 좌표가 없거나 쓸 수 없으면(형식 오류·너무 작음·화면 거의 전체)
  예전처럼 화면 구역(top·center·bottom 등)의 대략적인 사각형을 쓰고 bbox_source='region'으로 남긴다.
- 4단계(트랙): 위치 IoU 대신 '같은 위치 구역 + 같은 문구'로 이어지는 1초 프레임을 묶는다(상자 출처와 관계없이 같다).
- 신뢰도: 모델 점수가 없어 각 판독은 1.0으로 두고, 트랙 신뢰도는 '대표 문구와 같게 읽힌 프레임 비율'로 바꾼다.
  판정 엔진 이름은 ENGINE("qwen3-vl-video")이며 기준값은 backend/app/reviews/rules.py의 ENGINE_THRESHOLDS에 있다.

환경 변수
  VIDEO_SUBTITLE_VLM_URL          OpenAI 호환 기본 주소(예: http://127.0.0.1:18000/v1). 필수
  VIDEO_SUBTITLE_VLM_API_KEY      vLLM --api-key 값(선택)
  VIDEO_SUBTITLE_VLM_MODEL        served-model-name (기본 Qwen/Qwen3-VL-8B-Instruct)
  VIDEO_SUBTITLE_VLM_CONCURRENCY  동시 요청 수 1~64 (기본 8)
  VIDEO_SUBTITLE_VLM_TIMEOUT      요청당 제한 시간(초) 10~900 (기본 180)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import base64
import io
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from typing import Callable
import unicodedata

import httpx

from backend.app.video.tracks import MISS_APPROX
from backend.app.video.tracks import MISS_EXACT
from backend.app.video.tracks import _finish
from backend.app.video.tracks import grouping_form
from backend.app.video.tracks import text_changed

ENGINE = "qwen3-vl-video"
PIPELINE = "qwen3vl_frames_v1"
DEFAULT_MODEL = "Qwen/Qwen3-VL-8B-Instruct"
MAX_SIDE = 1280
MAX_TOKENS = 2048
ATTEMPTS = 2  # 형식 오류·일시 오류는 한 번 더 읽는다
ROLES = ("subtitle", "title", "info", "logo_text", "background", "other")
REGIONS = ("top", "center", "bottom", "left", "right", "full")
# 기존 줄 분류(subtitle_roles.CLASSES)와 맞춘 역할. 촬영된 글자(background)만 트랙 후보에서 뺀다(재현율 우선).
ROLE_CLASS = {"subtitle": "subtitle", "title": "subtitle", "info": "subtitle",
              "logo_text": "other", "other": "other", "background": "background"}
ROLE_LABEL = {"subtitle": "자막", "title": "제목", "info": "정보", "logo_text": "로고 글자",
              "background": "촬영된 글자", "other": "기타"}

PROMPT = """이 이미지는 홍보 영상의 한 프레임입니다. 화면에 보이는 모든 글자를 읽어 주세요.

규칙:
- 같은 위치에 함께 놓인 글자 묶음 하나를 항목 하나로 적습니다. 묶음 안의 줄바꿈은 \\n으로 남깁니다.
- 보이는 그대로 적습니다. 띄어쓰기·문장부호·숫자·영문 대소문자를 고치거나 추측해서 채우지 않습니다.
- 읽을 수 없는 글자는 적지 않습니다. 글자가 하나도 없으면 빈 목록을 냅니다.
- role은 subtitle(대사·설명 자막), title(제목·큰 문구), info(일시·장소·주최·웹주소 등 정보), logo_text(로고 안 글자), background(간판·현수막·화면 속 화면 등 촬영된 글자), other(워터마크 등) 중 하나입니다.
- region은 top, center, bottom, left, right, full 중 하나입니다.
- bbox_2d는 그 글자 묶음만 꼭 맞게 감싸는 사각형 [x1, y1, x2, y2]입니다. 이미지 왼쪽 위를 (0, 0), 오른쪽 아래를 (1000, 1000)으로 둔 0~1000 상대 좌표로 적습니다. 글자 둘레의 배경·띠까지 넓히지 않습니다. 위치를 알 수 없으면 bbox_2d를 빼고 region만 적습니다.

JSON만 출력합니다:
{"texts": [{"text": "...", "role": "subtitle", "region": "bottom", "bbox_2d": [120, 812, 880, 905]}]}"""


class VlmConfigError(ValueError):
    """자막 판독 모델 연결 설정이 없거나 잘못됨."""


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise VlmConfigError(f"{name} must be an integer") from None
    if not low <= value <= high:
        raise VlmConfigError(f"{name} must be between {low} and {high}")
    return value


def config(provider: str = "qwen") -> dict:
    """자막 판독 연결 설정. 기본(qwen)은 Qwen3-VL(vLLM)이다. 자막 판독은 보안 여부와 관계없이 Qwen3-VL이 먼저 읽는다.
    provider=openai는 일반 자료에서만 쓰는 보조 판독(read_frames 참고)이며 같은 프롬프트를 OpenAI API로 보낸다.
    키·모델은 model_route.openai_settings(), 동시 요청 수·제한 시간은 VIDEO_SUBTITLE_VLM_* 값을 함께 쓴다."""
    if provider == "openai":
        from backend.agent_framework.model_route import OPENAI_BASE_URL, openai_settings
        from backend.agent_framework.ports import AdapterFailure
        try:
            settings = openai_settings()
        except AdapterFailure:
            raise VlmConfigError("일반 자료 보조 판독에 쓸 OpenAI API 키가 설정되지 않았습니다.") from None
        return {"base": OPENAI_BASE_URL, "key": settings.api_key, "model": settings.model, "openai": True,
                "effort": settings.reasoning_effort,
                "concurrency": _int_env("VIDEO_SUBTITLE_VLM_CONCURRENCY", 8, 1, 64),
                "timeout": _int_env("VIDEO_SUBTITLE_VLM_TIMEOUT", 180, 10, 900)}
    base = os.getenv("VIDEO_SUBTITLE_VLM_URL", "").strip().rstrip("/")
    if not base:
        raise VlmConfigError("VIDEO_SUBTITLE_VLM_URL이 설정되지 않았습니다.")
    if not base.startswith(("http://", "https://")):
        raise VlmConfigError("VIDEO_SUBTITLE_VLM_URL은 http(s) 주소여야 합니다.")
    return {"base": base, "key": os.getenv("VIDEO_SUBTITLE_VLM_API_KEY", "").strip(),
            "model": os.getenv("VIDEO_SUBTITLE_VLM_MODEL", "").strip() or DEFAULT_MODEL, "openai": False,
            "concurrency": _int_env("VIDEO_SUBTITLE_VLM_CONCURRENCY", 8, 1, 64),
            "timeout": _int_env("VIDEO_SUBTITLE_VLM_TIMEOUT", 180, 10, 900)}


OPENAI_MAX_TOKENS = 8192  # 추론 모델은 추론 토큰도 출력 한도에 포함하므로 vLLM 경로보다 넉넉히 둔다


def _payload(cfg: dict, data: str) -> dict:
    """프레임 1장 요청 본문. OpenAI(보조 판독)는 vLLM 전용 값(chat_template_kwargs)과 temperature를 빼고
    max_completion_tokens·reasoning_effort를 쓴다. 프롬프트·이미지 크기는 같다."""
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}},
        {"type": "text", "text": PROMPT}]}]
    if cfg.get("openai"):
        return {"model": cfg["model"], "max_completion_tokens": OPENAI_MAX_TOKENS,
                "reasoning_effort": cfg["effort"], "messages": messages}
    return {"model": cfg["model"], "temperature": 0, "max_tokens": MAX_TOKENS,
            "chat_template_kwargs": {"enable_thinking": False}, "messages": messages}


def _headers(cfg: dict) -> dict:
    return {"Authorization": f"Bearer {cfg['key']}"} if cfg["key"] else {}


def readiness() -> tuple[bool, str]:
    """서버가 응답하고 설정한 모델을 올려 두었는지 확인한다(추론 없음). 키·주소는 메시지에 넣지 않는다."""
    try:
        cfg = config()
    except VlmConfigError as exc:
        return False, f"자막 판독 모델(Qwen3-VL) 연결 설정을 확인해 주세요: {exc}"
    try:
        response = httpx.get(f"{cfg['base']}/models", headers=_headers(cfg), timeout=10)
        response.raise_for_status()
        served = {item.get("id") for item in response.json().get("data", [])}
    except (httpx.HTTPError, ValueError, AttributeError):
        return False, "자막 판독 모델(Qwen3-VL) 서버에 연결하지 못했습니다. 터널·서버 상태를 확인해 주세요."
    if cfg["model"] not in served:
        return False, f"자막 판독 서버에 {cfg['model']} 모델이 올라와 있지 않습니다."
    return True, f"자막 판독 모델 준비됨: {cfg['model']}"


def encode(path: Path, max_side: int = MAX_SIDE) -> tuple[str, int, int]:
    """프레임을 긴 변 max_side 이하 JPEG로 줄여 base64로. 반환: (data, 원본 너비, 원본 높이)."""
    from PIL import Image
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    width, height = image.size
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return base64.b64encode(buffer.getvalue()).decode(), width, height


def parse(raw: str) -> list[dict]:
    """모델 응답에서 {"texts": [...]} 또는 목록을 꺼낸다. JSON이 아니면 ValueError."""
    body = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", body, re.S)
    if fence:
        body = fence.group(1)
    start = min((i for i in (body.find("{"), body.find("[")) if i >= 0), default=0)
    data, _ = json.JSONDecoder().raw_decode(body[start:])
    items = data.get("texts", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("texts must be a list")
    texts = []
    for item in items:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue
        text = unicodedata.normalize("NFC", str(item.get("text", ""))).strip()
        if not text:
            continue
        role = str(item.get("role", "other")).strip().lower()
        region = str(item.get("region", "")).strip().lower()
        box = item.get("bbox_2d", item.get("bbox"))   # 모델이 준 그대로 둔다. 좌표 변환·검사는 model_box가 한다.
        texts.append({"text": text, "role": role if role in ROLES else "other",
                      "region": region if region in REGIONS else "full", "bbox": box if isinstance(box, list) else None})
    return texts


def region_box(region: str, width: int, height: int) -> list[float]:
    """화면 구역의 대략적인 사각형(모델이 글자 위치를 주지 않았을 때의 근거 위치). 정확한 글자 위치가 아니다."""
    w, h = float(width), float(height)
    return {"top": [0.0, 0.0, w, h / 3], "center": [0.0, h / 3, w, 2 * h / 3], "bottom": [0.0, 2 * h / 3, w, h],
            "left": [0.0, 0.0, w / 2, h], "right": [w / 2, 0.0, w, h]}.get(region, [0.0, 0.0, w, h])


def sent_size(width: int, height: int, max_side: int = MAX_SIDE) -> tuple[int, int]:
    """encode가 모델에 보낸 이미지 크기(긴 변 max_side 이하로 비율을 유지해 줄인 크기)."""
    scale = min(1.0, max_side / max(width, height)) if width > 0 and height > 0 else 1.0
    return max(1, round(width * scale)), max(1, round(height * scale))


def model_box(value, width: int, height: int, sent: tuple[int, int] | None = None) -> list[float] | None:
    """모델이 준 글자 묶음 사각형 [x1, y1, x2, y2]를 원본 프레임 픽셀 좌표로 바꾼다. 쓸 수 없으면 None.

    약속한 좌표는 0~1000 상대 좌표다. 값이 1000을 넘으면 보낸 이미지(sent)의 픽셀 좌표로 본다(이전 Qwen-VL 계열의
    습관). 둘 다 아니거나, 넓이가 거의 없거나, 화면 거의 전체를 덮으면 글자 위치로 믿지 않는다(구역 사각형으로 대체)."""
    if not isinstance(value, (list, tuple)) or len(value) != 4 or width <= 0 or height <= 0:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x1, y1, x2, y2)) or min(x1, y1, x2, y2) < -10:
        return None
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    if right > 1000 or bottom > 1000:
        sent_w, sent_h = sent or sent_size(width, height)
        if right > sent_w * 1.02 or bottom > sent_h * 1.02:
            return None
        sx, sy = width / sent_w, height / sent_h
    else:
        sx, sy = width / 1000.0, height / 1000.0
    box = [max(0.0, left * sx), max(0.0, top * sy), min(float(width), right * sx), min(float(height), bottom * sy)]
    box_w, box_h = box[2] - box[0], box[3] - box[1]
    if box_w < 4 or box_h < 4 or box_w * box_h > 0.9 * width * height:
        return None
    return [round(v, 1) for v in box]


def _read_one(client: httpx.Client, cfg: dict, path: Path) -> dict:
    data, width, height = encode(path)
    payload = _payload(cfg, data)
    raw, error, texts, seconds = "", None, [], 0.0
    for _ in range(ATTEMPTS):
        began = time.perf_counter()
        try:
            response = client.post(f"{cfg['base']}/chat/completions", json=payload)
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"] or ""
            texts, error = parse(raw), None
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            error = type(exc).__name__  # 응답 원문은 raw에 남기고 오류는 종류만 기록한다
        seconds = round(time.perf_counter() - began, 3)
        if error is None:
            break
    if error is not None:
        return {"status": "failed", "records": [], "raw": raw[:4000], "error": error, "seconds": seconds}
    sent = sent_size(width, height)
    records = []
    for index, item in enumerate(texts):
        box = model_box(item.get("bbox"), width, height, sent)
        records.append({"id": f"vlm_{index:03d}", "text": item["text"], "role": item["role"], "region": item["region"],
                        "bbox_xyxy": box or region_box(item["region"], width, height),
                        "bbox_source": "model" if box else "region", "confidence": 1.0})
    return {"status": "ok", "records": records, "raw": raw[:4000], "error": None, "seconds": seconds}


def _served(cfg: dict) -> bool:
    """Qwen3-VL 서버가 응답하고 설정한 모델을 올려 두었는가(추론 없음, 10초 안)."""
    try:
        response = httpx.get(f"{cfg['base']}/models", headers=_headers(cfg), timeout=10)
        response.raise_for_status()
        return cfg["model"] in {item.get("id") for item in response.json().get("data", [])}
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


def _read_all(cfg: dict, frames: list[Path], tick: Callable[[], None] | None = None) -> list[dict]:
    limits = httpx.Limits(max_connections=cfg["concurrency"], max_keepalive_connections=cfg["concurrency"])

    def task(path: Path) -> dict:
        result = _read_one(client, cfg, path)
        result["provider"] = "openai" if cfg["openai"] else "vllm"
        if tick:
            tick()
        return result

    with httpx.Client(headers=_headers(cfg), timeout=cfg["timeout"], limits=limits) as client:
        with ThreadPoolExecutor(max_workers=cfg["concurrency"]) as pool:
            return list(pool.map(task, frames))


def read_frames(frames: list[Path], directory: Path, progress: Callable[[int], None], route: str = "default") -> dict:
    """Pipeline의 recognize 자리에 들어가는 함수. 프레임 하나의 실패는 그 프레임만 'failed'로 남긴다.

    자막은 보안 여부와 관계없이 Qwen3-VL(vLLM)이 먼저 읽는다(2026-11-16 사용자 결정).
    route=openai(일반 자료)일 때만 OpenAI API를 보조로 쓴다:
      · Qwen3-VL 연결 설정이 없거나 서버가 응답하지 않으면 모든 프레임을 OpenAI로 읽는다.
      · Qwen3-VL이 읽지 못한(failed) 프레임만 OpenAI로 다시 읽는다.
    보안 자료(runpod)·경로를 받지 않은 작업(default)은 OpenAI를 부르지 않는다. 응답 형식·후처리는 같다."""
    began = time.perf_counter()
    primary = backup = None
    try:
        primary = config("qwen")
    except VlmConfigError:
        if route != "openai":
            raise
    if route == "openai":
        try:
            backup = config("openai")
        except VlmConfigError:
            if primary is None:
                raise
        if primary is not None and backup is not None and not _served(primary):
            primary = None   # Qwen3-VL이 내려가 있으면 프레임마다 기다리지 않고 바로 보조 판독으로 읽는다
    done, lock = [0], threading.Lock()

    def tick() -> None:
        with lock:
            done[0] += 1
            progress(done[0])

    results = _read_all(primary or backup, frames, tick)
    retried = [index for index, row in enumerate(results) if row["status"] == "failed"] if primary and backup else []
    if retried:
        for index, row in zip(retried, _read_all(backup, [frames[i] for i in retried])):
            results[index] = {**row, "primary_error": results[index]["error"]}
    used = primary or backup
    fallback = sum(1 for row in results if row["provider"] == "openai") if primary else len(results)
    with (directory / "vlm.raw.jsonl").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "header", "engine": ENGINE, "model": used["model"], "max_side": MAX_SIDE,
                                 "provider": "openai" if used["openai"] else "vllm", "route": route,
                                 "backup_model": backup["model"] if backup and primary else None,
                                 "openai_frames": fallback if backup else 0,
                                 "max_tokens": OPENAI_MAX_TOKENS if used["openai"] else MAX_TOKENS,
                                 "temperature": None if used["openai"] else 0, "prompt": PROMPT}, ensure_ascii=False) + "\n")
        for index, (path, row) in enumerate(zip(frames, results)):
            handle.write(json.dumps({"index": index, "frame": path.name, "provider": row["provider"], "status": row["status"],
                                     "error": row["error"], "primary_error": row.get("primary_error"),
                                     "seconds": row["seconds"], "raw": row["raw"]}, ensure_ascii=False) + "\n")
    device = "openai" if not primary else "vllm+openai" if fallback else "vllm"
    return {"engine": ENGINE, "model": used["model"], "device": device, "load_seconds": None,
            "fallback_frames": fallback if backup else 0,
            "wall_seconds": round(time.perf_counter() - began, 2),
            "inference_seconds": [row["seconds"] for row in results],
            "frames": [{"status": row["status"], "records": row["records"], "unread_boxes": 0} for row in results]}


def line_role(record: dict) -> dict:
    """3-1단계 대체: 모델이 준 역할로 줄 분류 결과(role·kept·reason·probs)를 만든다."""
    model_role = record.get("role", "other")
    role = ROLE_CLASS.get(model_role, "other")
    kept = role != "background"
    reason = f"모델 판독 역할: {ROLE_LABEL.get(model_role, model_role)}" + ("" if kept else " — 자막 후보에서 제외")
    return {"role": role, "kept": kept, "reason": reason, "probs": None, "vlm_role": model_role,
            "region": record.get("region", "full")}


def build_tracks(frames: list[dict], lines: list[dict], duration_ms: int) -> list[dict]:
    """4단계 대체: 1초 프레임의 '남긴' 줄을 '같은 구역 + 같은 문구(작은 차이 허용)'로 이어 트랙을 만든다.

    위치 IoU를 쓰지 않으므로 한 구역에 서로 다른 문구가 함께 있어도 서로의 트랙을 끊지 않는다.
    문구가 바뀌면(숫자·% 토큰이 다르거나 편집거리 초과) 새 트랙이다. 누락 허용은 기존 트랙 규칙과 같다.
    """
    by_frame: dict[str, list[dict]] = {}
    for line in lines:
        if line["kept"]:
            by_frame.setdefault(line["frame_id"], []).append(line)
    base = sorted((f for f in frames if f["sampling"] == "base"), key=lambda f: f["time_ms"])
    tracks, active = [], []
    for frame in base:
        if frame.get("ocr_status") == "failed":
            continue
        matched = set()
        for line in by_frame.get(frame["frame_id"], []):
            region = line.get("region", "full")
            best = None
            for index, track in enumerate(active):
                last = track["readings"][-1]
                if index in matched or track["region"] != region:
                    continue
                if not text_changed(last["text"], line["text"]):
                    exact = grouping_form(last["text"]) == grouping_form(line["text"])
                    if best is None or (exact and not best[1]):
                        best = (index, exact)
            reading = {key: line[key] for key in ("frame_id", "time_ms", "text", "confidence", "bbox_xyxy", "role")}
            if best is None:
                active.append({"readings": [reading], "misses": 0, "exact": True, "region": region})
                matched.add(len(active) - 1)
            else:
                track = active[best[0]]
                track["exact"] = track["exact"] and best[1]
                track["readings"].append(reading)
                track["misses"] = 0
                matched.add(best[0])
        survivors = []
        for index, track in enumerate(active):
            if index not in matched:
                track["misses"] += 1
            if track["misses"] > (MISS_EXACT if track["exact"] else MISS_APPROX):
                tracks.append(track)
            else:
                survivors.append(track)
        active = survivors
    tracks.extend(active)
    tracks.sort(key=lambda t: (t["readings"][0]["time_ms"], t["readings"][0]["bbox_xyxy"][1]))
    finished = []
    for number, track in enumerate(tracks, 1):
        item = _finish(number, track["readings"], duration_ms)
        # 모델 점수가 없어 '대표 문구와 같게 읽힌 프레임 비율'을 트랙 신뢰도로 쓴다(판정 기준은 rules.ENGINE_THRESHOLDS)
        item["confidence"] = round(item["vote"]["count"] / item["vote"]["total"], 4)
        item["recognition_engine"] = ENGINE
        item["region"] = track["region"]
        finished.append(item)
    return finished
