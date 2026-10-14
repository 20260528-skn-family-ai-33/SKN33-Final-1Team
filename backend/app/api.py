"""Local review API: upload -> frozen criteria -> real OCR -> evidence.

Run with ``uvicorn backend.app.api:app --host 127.0.0.1 --port 8000``. Uploaded bytes,
normalised images, immutable criteria and job results stay under .runtime.
The configured OCR runtime alone controls any explicitly enabled remote call.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import threading
from typing import Callable, Literal
from uuid import UUID, uuid4
import warnings

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .review_rules import review_criteria
from .sample import sample_image_path
from .spa import ReviewStaticFiles
from .local_auth import local_auth_router


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# The OCR runtime accepts 40 MP. A fixed 2x scale multiplies pixels by four.
MAX_IMAGE_PIXELS = 10_000_000
MAX_IMAGE_SIDE = 8192
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}
CRITERIA_MAX_JSON = 250_000
PIPELINE_NAMES = {"baseline", "multiline", "adaptive", "paddle", "paddle-only", "qwen"}
MULTILINE_PIPELINES = {"multiline_v2", "adaptive_v1", "paddle_regions_v1", "paddle_full_v1", "qwen_regions_v1"}
LOCAL_PIPELINES = {"adaptive", "paddle", "paddle-only", "qwen"}
RECOGNITION_ENGINES = {"easyocr", "paddleocr", "qwen3-vl"}
SELECTION_REASONS = {"new_model_candidate_unverified", "model_abstained_keep_original",
                     "conflicting_candidates_keep_original", "stronger_candidate_unverified",
                     "new_detection_unverified", "vlm_location_unverified",
                     "same_text_without_confidence_boost", "refinement_incomplete"}


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=500)
    type: Literal["information", "visual"]
    source: str = Field(default="user", min_length=1, max_length=500)


class CriteriaSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    criteria: list[Criterion] = Field(min_length=1, max_length=100)
    criteria_version: int = Field(ge=1, le=1_000_000)
    project_name: str = Field(min_length=1, max_length=120)

    @field_validator("criteria")
    @classmethod
    def unique_ids(cls, values: list[Criterion]) -> list[Criterion]:
        if len({item.id for item in values}) != len(values):
            raise ValueError("criteria IDs must be unique")
        return values


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(f".{uuid4().hex}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalise_image(data: bytes) -> tuple[Image.Image, dict]:
    """Validate decoded raster contents, ignoring an untrusted file extension."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as decoded:
                actual_format = decoded.format
                if actual_format not in ALLOWED_FORMATS:
                    raise HTTPException(415, "JPEG, PNG, WEBP 래스터 이미지만 지원합니다. PDF·SVG는 이미지로 변환해 주세요.")
                width, height = decoded.size
                if width * height > MAX_IMAGE_PIXELS or max(width, height) > MAX_IMAGE_SIDE:
                    raise HTTPException(413, "이미지는 1,000만 픽셀 이하, 한 변 8,192픽셀 이하로 줄여 주세요.")
                if getattr(decoded, "n_frames", 1) != 1:
                    raise HTTPException(415, "애니메이션은 지원하지 않습니다. 단일 이미지로 저장해 주세요.")
                decoded.load()
                oriented = ImageOps.exif_transpose(decoded).convert("RGBA")
                rgb = Image.alpha_composite(Image.new("RGBA", oriented.size, "white"), oriented).convert("RGB")
                metadata = {
                    "actual_format": actual_format, "original_dimensions": [width, height],
                    "original_sha256": hashlib.sha256(data).hexdigest(), "original_bytes": len(data),
                    "oriented_dimensions": list(rgb.size), "ocr_scale": 2,
                    "normalization": "EXIF transpose; alpha composited onto white; RGB; 2x Lanczos for OCR",
                }
                return rgb, metadata
    except HTTPException:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(413, "이미지의 픽셀 수가 허용 범위를 초과합니다.") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise HTTPException(415, "이미지를 읽을 수 없습니다. 손상되지 않은 JPEG, PNG, WEBP 파일을 선택해 주세요.") from None


def _default_recognizer(path: Path) -> dict:
    # 미검증 모델 채택과 구분하여 개선 경로를 명시적으로 선택할 수 있게 한다.
    pipeline = os.getenv("OCR_PIPELINE", "baseline").strip().lower()
    if pipeline == "paddle-only":
        # 탐지부터 인식까지 Paddle로 처리하여 EasyOCR 패키지·가중치를 요구하지 않는다.
        from .paddle_ocr import recognize_paddle
        return recognize_paddle(path, path.parent / "paddle-only")
    if pipeline in {"paddle", "qwen"}:
        from .model_ocr import recognize_models
        return recognize_models(path, path.parent / pipeline, pipeline)
    if pipeline == "adaptive":
        from .adaptive_ocr import recognize_adaptive
        return recognize_adaptive(path, path.parent / "adaptive")
    if pipeline not in {"baseline", "multiline"}:
        raise ValueError("OCR_PIPELINE must be baseline, multiline, adaptive, paddle, paddle-only or qwen")
    from .ocr_runtime import recognize_image
    payload = recognize_image(path)
    if pipeline == "multiline":
        payload["pipeline"] = "multiline_v2"
    return payload


def _runtime_identity() -> dict:
    """실패 전에도 선택한 엔진과 실행 경로를 민감 정보 없이 표시한다."""
    pipeline = os.getenv("OCR_PIPELINE", "baseline").strip().lower()
    engine = {"paddle-only": "paddleocr", "paddle": "easyocr+paddleocr", "qwen": "easyocr+qwen3-vl"}.get(pipeline, "easyocr")
    metadata = {"engine": engine, "execution_target": os.getenv("RUN_TARGET", "local").strip().lower()}
    name = {"multiline": "multiline_v2", "adaptive": "adaptive_v1", "paddle": "paddle_regions_v1",
            "paddle-only": "paddle_full_v1", "qwen": "qwen_regions_v1"}.get(pipeline)
    if name:
        metadata["pipeline"] = name
    return metadata


def _default_runtime_status() -> dict:
    """선택한 경로만 점검하여 Paddle 단독 건강 검사에서 EasyOCR를 불러오지 않는다."""
    pipeline = os.getenv("OCR_PIPELINE", "baseline").strip().lower()
    target = os.getenv("RUN_TARGET", "local").strip().lower()
    identity = _runtime_identity()
    if pipeline not in PIPELINE_NAMES or target not in {"local", "runpod"}:
        return {**identity, "ready": False, "status": "not_ready", "detail": "Invalid OCR configuration"}
    if pipeline in LOCAL_PIPELINES and target != "local":
        return {**identity, "ready": False, "status": "not_ready", "detail": "Selected pipeline requires RUN_TARGET=local"}
    if pipeline == "paddle-only":
        from .paddle_ocr import paddle_readiness
        ready, detail = paddle_readiness()
        return {**identity, "device": "cpu", "ready": ready, "status": "ready" if ready else "not_ready", "detail": detail}
    from .ocr_runtime import get_runtime_status
    status = get_runtime_status()
    if pipeline in {"paddle", "qwen"}:
        from .model_ocr import model_readiness, ENGINES, PIPELINES
        ready, detail = model_readiness(pipeline)
        status.update(engine=ENGINES[pipeline], pipeline=PIPELINES[pipeline],
                      ready=status["ready"] and ready, detail=detail, device="unknown")
    return status


def _safe_runtime(payload: dict) -> dict:
    """Return public execution metadata, never URLs, credentials or tracebacks."""
    allowed_values = {
        "engine": {"easyocr", "paddleocr", "easyocr+paddleocr", "easyocr+qwen3-vl"}, "execution_target": {"local", "runpod"},
        "device": {"cuda", "mps", "cpu", "gpu", "runpod", "remote-auto", "unknown"},
        "status": {"ok", "ready", "completed", "failed", "unavailable", "not_ready", "configured", "success", "skipped", "fallback"},
    }
    result = {}
    if isinstance(payload.get("ready"), bool):
        result["ready"] = payload["ready"]
    for key, allowed in allowed_values.items():
        value = payload.get(key)
        if isinstance(value, str) and value.lower() in allowed:
            result[key] = value.lower()
    if payload.get("pipeline") in MULTILINE_PIPELINES:
        result["pipeline"] = payload["pipeline"]
    if payload.get("refinement_status") in {"completed", "partial", "timeout", "worker_failed"}:
        result["refinement_status"] = payload["refinement_status"]
    # Attempts deliberately omit free-form errors that may contain secrets.
    result["attempts"] = []
    for attempt in payload.get("attempts", []) if isinstance(payload.get("attempts"), list) else []:
        if isinstance(attempt, dict):
            clean = {}
            for key in ("device", "execution_target", "status"):
                value = attempt.get(key)
                if isinstance(value, str) and value.lower() in allowed_values[key]:
                    clean[key] = value.lower()
            if clean:
                result["attempts"].append(clean)
    return result


def _map_records(payload: dict, width: int, height: int) -> list[dict]:
    raw_records = payload.get("records")
    if not isinstance(raw_records, list) or len(raw_records) > 10_000:
        raise ValueError("Invalid OCR record list")
    expected_dimensions = [width * 2, height * 2]
    dimensions = payload.get("input_dimensions")
    if isinstance(dimensions, dict):
        dimensions = [dimensions.get("width"), dimensions.get("height")]
    if dimensions is not None and list(dimensions) != expected_dimensions:
        raise ValueError("OCR returned coordinates for an unexpected image size")
    mapped = []
    seen = set()
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
            raise ValueError("Invalid OCR text")
        record_id = raw.get("id", f"ocr-{index + 1}")
        if not isinstance(record_id, (str, int)) or isinstance(record_id, bool) or record_id in seen:
            raise ValueError("Invalid OCR ID")
        seen.add(record_id)
        text = raw["text"].strip()
        if not text:
            continue
        if len(text) > 10_000:
            raise ValueError("OCR text is too long")
        box = raw.get("bbox_xyxy")
        if not isinstance(box, (list, tuple)) or len(box) != 4 or any(
            isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number)
            for number in box
        ):
            raise ValueError("Invalid OCR bounding box")
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError("Degenerate OCR bounding box")
        coordinates = [max(0, min(limit, float(number) / 2)) for number, limit in zip(box, [width, height, width, height])]
        if coordinates[2] <= coordinates[0] or coordinates[3] <= coordinates[1]:
            raise ValueError("OCR bounding box outside image")
        confidence = raw.get("confidence")
        if confidence is not None and (
            isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0 <= confidence <= 1
        ):
            raise ValueError("Invalid OCR confidence")
        record = {"id": record_id, "text": text, "confidence": confidence, "bbox_xyxy": coordinates}
        # 재인식 정보도 허용한 값만 공개하고 입력 좌표는 한 번만 복원한다.
        if "requires_review" in raw:
            if type(raw["requires_review"]) is not bool:
                raise ValueError("Invalid review flag")
            record["requires_review"] = raw["requires_review"]
        if "baseline_text" in raw:
            if not isinstance(raw["baseline_text"], str) or len(raw["baseline_text"]) > 10_000:
                raise ValueError("Invalid baseline text")
            record["baseline_text"] = raw["baseline_text"]
        # 원문 근거의 점수·엔진·보류 사유를 검증해 항목별 재판정에 전달한다.
        if "baseline_confidence" in raw:
            baseline_confidence = raw["baseline_confidence"]
            if baseline_confidence is not None and (type(baseline_confidence) not in (int, float)
                    or not math.isfinite(baseline_confidence) or not 0 <= baseline_confidence <= 1):
                raise ValueError("Invalid baseline confidence")
            record["baseline_confidence"] = baseline_confidence
        if "baseline_requires_review" in raw:
            if type(raw["baseline_requires_review"]) is not bool:
                raise ValueError("Invalid baseline review flag")
            record["baseline_requires_review"] = raw["baseline_requires_review"]
        if "baseline_recognition_engine" in raw:
            if raw["baseline_recognition_engine"] not in RECOGNITION_ENGINES:
                raise ValueError("Invalid baseline recognition engine")
            record["baseline_recognition_engine"] = raw["baseline_recognition_engine"]
        if raw.get("selection_reason") in SELECTION_REASONS:
            record["selection_reason"] = raw["selection_reason"]
        alternatives = raw.get("alternatives", [])
        if not isinstance(alternatives, list) or len(alternatives) > 10:
            raise ValueError("Invalid OCR alternatives")
        if alternatives:
            # 후보에도 같은 좌표 검증을 적용하고 임의 속성은 공개하지 않는다.
            candidates = [{"id": f"candidate-{i}", "text": c.get("text"), "confidence": c.get("confidence"),
                           "bbox_xyxy": c.get("bbox_xyxy")} for i, c in enumerate(alternatives)]
            record["alternatives"] = _map_records({"records": candidates}, width, height)
            for candidate, raw_candidate in zip(record["alternatives"], alternatives):
                if raw_candidate.get("engine") in RECOGNITION_ENGINES:
                    candidate["engine"] = raw_candidate["engine"]
        # 단독 Paddle의 출처가 빠져도 EasyOCR 기본 점수 기준으로 낮아지지 않게 한다.
        if "recognition_engine" in raw:
            if not isinstance(raw["recognition_engine"], str) or raw["recognition_engine"] not in RECOGNITION_ENGINES:
                raise ValueError("Invalid recognition engine")
            record["recognition_engine"] = raw["recognition_engine"]
        if payload.get("engine") == "paddleocr" or payload.get("pipeline") == "paddle_full_v1":
            if record.get("recognition_engine", "paddleocr") != "paddleocr":
                raise ValueError("Paddle payload recognition engine mismatch")
            record["recognition_engine"] = "paddleocr"
        if raw.get("polygon") is not None:
            polygon = raw["polygon"]
            if not isinstance(polygon, list) or len(polygon) < 4 or len(polygon) > 32 or any(
                not isinstance(p, list) or len(p) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in p) for p in polygon
            ):
                raise ValueError("Invalid OCR polygon")
            record["polygon"] = [[max(0, min(width, p[0] / 2)), max(0, min(height, p[1] / 2))] for p in polygon]
        mapped.append(record)
    return mapped


def create_app(
    *, recognizer: Callable[[Path], dict] | None = None,
    runtime_status: Callable[[], dict] | None = None, review_root: Path | None = None,
) -> FastAPI:
    recognize = recognizer or _default_recognizer
    runtime_health = runtime_status or _default_runtime_status
    storage = review_root or PROJECT_ROOT / ".runtime" / "reviews"
    storage.mkdir(parents=True, exist_ok=True)
    lock = threading.RLock()
    inference_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Background work is local to this process. Never present interrupted
        # persisted jobs as if inference were still running after a restart.
        for path in storage.glob("*/result.json"):
            try:
                value = _read_json(path)
                if value.get("status") in {"queued", "running"}:
                    value.update(status="failed", stage="failed", error="서버가 재시작되어 검수가 중단됐습니다. 다시 실행해 주세요.", updated_at=_now())
                    _write_json(path, value)
            except (OSError, ValueError):
                continue
        yield

    application = FastAPI(title="바로검수 API", version="1.0.0", lifespan=lifespan)
    # localhost의 임시 admin 로그인은 환경 변수로 해제할 수 있다.
    if os.getenv("LOCAL_ADMIN_LOGIN", "1") == "1":
        application.include_router(local_auth_router())
    allowed_origins = [value.strip() for value in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if value.strip()]
    allowed_origins = [value for value in allowed_origins if value.startswith(("http://localhost:", "http://127.0.0.1:"))]
    application.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    def directory(review_id: str) -> Path:
        try:
            if str(UUID(review_id)) != review_id:
                raise ValueError
        except ValueError:
            raise HTTPException(404, "검수 기록을 찾을 수 없습니다.") from None
        target = storage / review_id
        if not (target / "result.json").is_file():
            raise HTTPException(404, "검수 기록을 찾을 수 없습니다.")
        return target

    def update(target: Path, **changes) -> None:
        with lock:
            result = _read_json(target / "result.json")
            result.update(changes, updated_at=_now())
            _write_json(target / "result.json", result)

    def _run_review(target: Path) -> None:
        stage = "preprocessing"
        try:
            update(target, status="running", stage=stage, progress=15)
            with Image.open(target / "normalized.png") as image:
                width, height = image.size
                image.resize((width * 2, height * 2), Image.Resampling.LANCZOS).save(target / "ocr_input.png")
            stage = "ocr"
            update(target, stage=stage, progress=30)
            payload = recognize(target / "ocr_input.png")
            if not isinstance(payload, dict):
                raise ValueError("Invalid OCR response")
            runtime = _safe_runtime(payload)
            update(target, runtime=runtime)
            if payload.get("status") in {"failed", "error", "unavailable"}:
                raise RuntimeError("OCR runtime failed")
            records = _map_records(payload, width, height)
            if payload.get("pipeline") in MULTILINE_PIPELINES:
                # 원시 후보와 시도 이력은 비공개 실행 폴더에 보존한다.
                _write_json(target / "ocr.raw.json", payload)
            ocr = {"records": records, "input_dimensions": [width * 2, height * 2], "coordinate_space": "normalized_original_pixels", "scale": 2}
            multiline = payload.get("pipeline") in MULTILINE_PIPELINES
            if multiline:
                from .event_layout import extract_event_multiline
                ocr["event"] = extract_event_multiline(records)
            _write_json(target / "ocr.json", {**ocr, **runtime})
            stage = "review"
            update(target, stage=stage, progress=80, ocr=ocr)
            snapshot = _read_json(target / "snapshot.json")
            results = review_criteria(snapshot["criteria"], records, multiline=multiline)
            update(target, status="completed", stage="completed", progress=100, results=results, error=None)
        except Exception as error:
            messages = {
                "preprocessing": "이미지 전처리에 실패했습니다. 이미지 크기와 서버 저장 공간을 확인해 주세요.",
                "ocr": "OCR 실행에 실패했습니다. 모델 설치·다운로드 설정과 CUDA/MPS/CPU 또는 RunPod 연결 설정을 확인한 뒤 다시 실행해 주세요.",
                "review": "OCR 결과를 검수 기준과 비교하지 못했습니다. 기준 형식과 OCR 출력을 확인해 주세요.",
            }
            if stage == "ocr" and getattr(error, "code", None) == "remote_image_too_large":
                messages["ocr"] = "2배 전처리한 이미지가 RunPod의 전송 한도 7 MiB를 초과했습니다. 원본 이미지 크기를 줄이거나 로컬 실행(CUDA → MPS → CPU)을 선택해 주세요."
            failure_metadata = {}
            if stage == "ocr":
                failure_metadata["runtime"] = _safe_runtime({**_runtime_identity(), "status": "failed", "attempts": getattr(error, "attempts", [])})
            update(target, status="failed", stage="failed", error=messages[stage], **failure_metadata)

    def run_review(target: Path) -> None:
        # Only one image is expanded/inferred at a time in this local process.
        # A job waiting for this lock remains truthfully queued.
        with inference_lock:
            _run_review(target)

    @application.get("/api/health")
    def health():
        try:
            status = runtime_health()
            return {"status": "ok", "runtime": _safe_runtime(status), "limits": {"upload_bytes": MAX_UPLOAD_BYTES, "image_pixels": MAX_IMAGE_PIXELS, "image_side": MAX_IMAGE_SIDE}}
        except Exception:
            return {"status": "degraded", "runtime": {"status": "unavailable"}, "message": "OCR 실행 환경을 확인해 주세요."}

    @application.get("/api/sample")
    def sample():
        return {
            "project_name": "2026 김천김밥축제 포스터 검수",
            "requirements": "행사명: 김천김밥축제\n기간: 2026.10.23 ~ 10.25\n장소: 직지문화공원\n장소: 사명대사공원\n장소: 친환경생태공원\n김천시 로고가 표시되어 있어야 합니다.\n문구와 이미지가 잘리지 않아야 합니다.",
        }

    @application.get("/api/sample/image")
    def sample_image():
        reference = sample_image_path()
        if reference is None:
            raise HTTPException(404, "샘플 이미지를 찾을 수 없습니다.")
        with Image.open(reference) as image:
            image_format = image.format
        media_type = Image.MIME.get(image_format, "application/octet-stream")
        extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[image_format]
        return FileResponse(reference, media_type=media_type, filename=f"gimcheon-2026.{extension}")

    @application.post("/api/reviews", status_code=202)
    async def submit(
        background_tasks: BackgroundTasks,
        image: UploadFile = File(...), criteria: str = Form(...),
        criteria_version: int = Form(..., ge=1, le=1_000_000),
        project_name: str = Form(..., min_length=1, max_length=120),
    ):
        if len(criteria) > CRITERIA_MAX_JSON:
            raise HTTPException(422, "검수 기준이 너무 깁니다. 최대 100개, 항목당 500자까지 입력해 주세요.")
        try:
            frozen = CriteriaSnapshot(criteria=json.loads(criteria), criteria_version=criteria_version, project_name=project_name)
        except (json.JSONDecodeError, ValidationError, TypeError):
            raise HTTPException(422, "확정한 기준을 확인해 주세요. 고유 ID와 유형, 1~500자의 내용이 있는 기준 1~100개가 필요합니다.") from None
        try:
            data = await image.read(MAX_UPLOAD_BYTES + 1)
        finally:
            await image.close()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "이미지 파일은 20 MiB 이하로 업로드해 주세요.")
        if not data:
            raise HTTPException(400, "빈 이미지 파일입니다.")
        rgb, source = await run_in_threadpool(_normalise_image, data)
        review_id = str(uuid4())
        target = storage / review_id
        target.mkdir()
        # Original bytes are retained exactly; user-controlled names are never
        # used as paths or served directly. snapshots have no update endpoint.
        (target / "original.bin").write_bytes(data)
        rgb.save(target / "normalized.png")
        snapshot = {**frozen.model_dump(), "image_source": source, "created_at": _now()}
        _write_json(target / "snapshot.json", snapshot)
        result = {
            "id": review_id, "status": "queued", "stage": "queued", "progress": 5,
            "criteria_version": frozen.criteria_version, "project_name": frozen.project_name,
            "criteria": [item.model_dump() for item in frozen.criteria],
            "image": {"width": rgb.width, "height": rgb.height, "url": f"/api/reviews/{review_id}/image", "sha256": source["original_sha256"], "actual_format": source["actual_format"]},
            "results": [], "runtime": None, "ocr": None, "error": None, "created_at": _now(), "updated_at": _now(),
        }
        _write_json(target / "result.json", result)
        rgb.close()
        background_tasks.add_task(run_review, target)
        return {"id": review_id, "status": "queued"}

    @application.get("/api/reviews/{review_id}")
    def get_review(review_id: str):
        with lock:
            return _read_json(directory(review_id) / "result.json")

    @application.get("/api/reviews/{review_id}/image")
    def review_image(review_id: str):
        return FileResponse(directory(review_id) / "normalized.png", media_type="image/png")

    # API routes are defined first. Only the built React assets are public.
    frontend = PROJECT_ROOT / "frontend" / "dist"
    if frontend.is_dir():
        application.mount("/", ReviewStaticFiles(directory=frontend, html=True), name="frontend")
    return application


app = create_app()
