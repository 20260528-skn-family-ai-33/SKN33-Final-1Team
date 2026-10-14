"""EasyOCR 없이 Paddle 탐지·한국어 인식을 실행하는 로컬 OCR 어댑터."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from scripts.prepare_paddle import DETECTION_MODEL, MODEL, verify_model
from .ocr_runtime import OCRRuntimeError, PROJECT_ROOT

ENGINE = "paddleocr"
PIPELINE = "paddle_full_v1"
WORKER_TIMEOUT = 120


def paddle_paths():
    """EasyOCR 환경과 분리된 Python 및 탐지·인식 모델 경로를 반환한다."""
    executable = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    base = PROJECT_ROOT / ".runtime/paddle-models"
    return PROJECT_ROOT / ".runtime/paddle-env" / executable, base / DETECTION_MODEL, base / MODEL


def worker_environment():
    """모델 자동 조회를 끄고 작업자 캐시를 프로젝트 내부에 고정한다."""
    return {**os.environ, "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True", "HF_HUB_OFFLINE": "1",
            "PADDLE_PDX_CACHE_HOME": str(PROJECT_ROOT / ".runtime/paddlex"),
            "XDG_CACHE_HOME": str(PROJECT_ROOT / ".runtime/model-cache")}


def paddle_readiness(*, check_import=False):
    """설치·다운로드·추론 없이 로컬 실행 환경과 두 모델의 해시를 확인한다."""
    if os.getenv("RUN_TARGET", "local").strip().lower() != "local":
        return False, "Paddle 단독 경로는 RUN_TARGET=local에서만 지원합니다."
    python, detection, recognition = paddle_paths()
    if not python.is_file():
        return False, "Paddle 전용 .runtime/paddle-env 환경이 없습니다."
    for directory, model in ((detection, DETECTION_MODEL), (recognition, MODEL)):
        try:
            verify_model(directory, model)
        except (OSError, ValueError, TypeError, KeyError):
            return False, f"{model} 모델 파일이 없거나 무결성 검증에 실패했습니다."
    if check_import:
        try:
            subprocess.run([str(python), "-c", "import paddle; from paddleocr import PaddleOCR"],
                           cwd=PROJECT_ROOT, env=worker_environment(), timeout=20, check=True, capture_output=True)
        except (OSError, subprocess.SubprocessError):
            return False, "Paddle 패키지 불러오기에 실패했습니다. requirements-paddle.txt 설치를 확인하세요."
    return True, "Paddle 탐지·한국어 인식 모델 무결성 확인 완료; 추론은 실행 시 확인합니다."


def normalize_records(raw, width, height):
    """필터 후 rec_polys와 문구·점수를 함께 검증하고 입력 픽셀 좌표를 유지한다."""
    if not isinstance(raw, dict):
        raise ValueError("Invalid Paddle result object")
    value = raw.get("res", raw)
    if not isinstance(value, dict):
        raise ValueError("Invalid Paddle result payload")
    texts, scores, polygons = (value.get(name) for name in ("rec_texts", "rec_scores", "rec_polys"))
    if (any(not isinstance(items, list) for items in (texts, scores, polygons))
            or not len(texts) == len(scores) == len(polygons) or len(texts) > 1000):
        raise ValueError("Paddle text/score/polygon count mismatch")
    records = []
    for index, (text, score, polygon) in enumerate(zip(texts, scores, polygons)):
        if not isinstance(text, str) or len(text) > 2000:
            raise ValueError("Invalid Paddle text")
        if type(score) not in (float, int) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Invalid Paddle confidence")
        if not isinstance(polygon, list) or len(polygon) != 4:
            raise ValueError("Invalid Paddle polygon")
        for point in polygon:
            if (not isinstance(point, list) or len(point) != 2
                    or any(type(v) not in (float, int) or not math.isfinite(v) for v in point)
                    or not 0 <= point[0] <= width or not 0 <= point[1] <= height):
                raise ValueError("Paddle polygon outside input image")
        xs, ys = [point[0] for point in polygon], [point[1] for point in polygon]
        box = [min(xs), min(ys), max(xs), max(ys)]
        area = abs(sum(polygon[i][0]*polygon[(i+1)%4][1]-polygon[(i+1)%4][0]*polygon[i][1] for i in range(4)))
        if box[0] >= box[2] or box[1] >= box[3] or area == 0:
            raise ValueError("Degenerate Paddle polygon")
        if text.strip():
            records.append({"id": f"paddle_{index:03d}", "text": text.strip(), "confidence": float(score),
                            "bbox_xyxy": box, "polygon": polygon, "recognition_engine": ENGINE})
    return records


def recognize_paddle(image_path, directory, *, timeout=WORKER_TIMEOUT):
    """EasyOCR 호출 없이 제한 시간 내 전체 탐지·인식 결과와 원시 응답을 저장한다."""
    from PIL import Image
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 240:
        raise ValueError("Invalid Paddle timeout")
    ready, detail = paddle_readiness()
    if not ready:
        raise OCRRuntimeError(detail, code="model_not_ready")
    image_path, directory = Path(image_path).resolve(), Path(directory).resolve()
    with Image.open(image_path) as opened:
        width, height = opened.size
    directory.mkdir(parents=True, exist_ok=False)
    python, detection, recognition = paddle_paths()
    output = directory / "paddle.full.raw.json"
    started = time.perf_counter()
    try:
        with (directory / "paddle.full.log").open("w", encoding="utf-8") as log:
            subprocess.run([str(python), "-m", "backend.app.paddle_full_worker", "--input", str(image_path),
                            "--output", str(output), "--detection-dir", str(detection),
                            "--recognition-dir", str(recognition)], cwd=PROJECT_ROOT, env=worker_environment(),
                           timeout=min(timeout, WORKER_TIMEOUT), check=True, stdout=log, stderr=subprocess.STDOUT)
        if output.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Paddle result too large")
        payload = json.loads(output.read_text(encoding="utf-8"))
        if payload.get("input_dimensions") != [width, height] or payload.get("device") != "cpu":
            raise ValueError("Paddle input dimensions or device mismatch")
        records = normalize_records(payload["raw"], width, height)
        identity = payload["model_identity"]
        if (identity["detection"] != verify_model(detection, DETECTION_MODEL)
                or identity["recognition"] != verify_model(recognition, MODEL)):
            raise ValueError("Paddle output model identity mismatch")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as exc:
        attempt = {"device": "cpu", "status": "failed", "error_type": type(exc).__name__}
        code = "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "paddle_failed"
        raise OCRRuntimeError("Paddle 탐지·인식 실행에 실패했습니다. 저장된 실행 로그를 확인하세요.",
                              attempts=[attempt], code=code) from exc
    result = {"engine": ENGINE, "pipeline": PIPELINE, "execution_target": "local", "status": "success",
              "device": "cpu", "input_dimensions": [width, height], "records": records,
              "attempts": [{"device": "cpu", "status": "success"}], "model_identity": identity,
              "raw": payload["raw"], "profile": {"wall_seconds": time.perf_counter()-started,
                  "load_seconds": payload.get("load_seconds"), "inference_seconds": payload.get("inference_seconds"),
                  "peak_rss_bytes": payload.get("peak_rss_bytes")}}
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return result
