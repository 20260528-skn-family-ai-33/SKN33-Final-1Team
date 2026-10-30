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
import queue
import threading
from typing import Callable, Literal
from uuid import UUID, uuid4
import warnings

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .review_rules import review_criteria
from .sample import sample_image_path
from .spa import ReviewStaticFiles
from .local_auth import local_auth_router
from .ocr_config import selected_pipeline
from .requirements_pdf import DocumentError, MAX_PDF_BYTES, extract_document
from .document_conditions import DocumentCondition, matches_condition_text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# The OCR runtime accepts 40 MP. A fixed 2x scale multiplies pixels by four.
MAX_IMAGE_PIXELS = 10_000_000
MAX_IMAGE_SIDE = 8192
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}
CRITERIA_MAX_JSON = 250_000
PIPELINE_NAMES = {"baseline", "multiline", "adaptive", "paddle", "paddle-only", "qwen"}
MULTILINE_PIPELINES = {
    "multiline_v2",
    "adaptive_v1",
    "paddle_regions_v1",
    "paddle_full_v1",
    "qwen_regions_v1",
}
LOCAL_PIPELINES = {"adaptive", "paddle", "paddle-only", "qwen"}
RECOGNITION_ENGINES = {"easyocr", "paddleocr", "qwen3-vl"}
SELECTION_REASONS = {
    "new_model_candidate_unverified",
    "model_abstained_keep_original",
    "conflicting_candidates_keep_original",
    "stronger_candidate_unverified",
    "new_detection_unverified",
    "vlm_location_unverified",
    "same_text_without_confidence_boost",
    "refinement_incomplete",
}


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=500)
    type: Literal["information", "visual"]
    source: str = Field(default="user", min_length=1, max_length=500)
    document_condition: DocumentCondition | None = None

    @model_validator(mode='after')
    def consistent_document_condition(self):
        if self.document_condition and not matches_condition_text(self.document_condition.model_dump(), self.text):
            raise ValueError('PDF 조건 기대값과 표시 문구가 다릅니다. 편집한 항목의 이전 문서 구조를 제거하세요.')
        return self


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
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
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
                    raise HTTPException(
                        415,
                        "JPEG, PNG, WEBP 래스터 이미지만 지원합니다. PDF·SVG는 이미지로 변환해 주세요.",
                    )
                width, height = decoded.size
                if (
                    width * height > MAX_IMAGE_PIXELS
                    or max(width, height) > MAX_IMAGE_SIDE
                ):
                    raise HTTPException(
                        413,
                        "이미지는 1,000만 픽셀 이하, 한 변 8,192픽셀 이하로 줄여 주세요.",
                    )
                if getattr(decoded, "n_frames", 1) != 1:
                    raise HTTPException(
                        415,
                        "애니메이션은 지원하지 않습니다. 단일 이미지로 저장해 주세요.",
                    )
                decoded.load()
                oriented = ImageOps.exif_transpose(decoded).convert("RGBA")
                rgb = Image.alpha_composite(
                    Image.new("RGBA", oriented.size, "white"), oriented
                ).convert("RGB")
                metadata = {
                    "actual_format": actual_format,
                    "original_mode": decoded.mode,
                    "original_frames": getattr(decoded, 'n_frames', 1),
                    "original_dimensions": [width, height],
                    "original_sha256": hashlib.sha256(data).hexdigest(),
                    "original_bytes": len(data),
                    "oriented_dimensions": list(rgb.size),
                    "ocr_scale": 2,
                    "normalization": "EXIF transpose; alpha composited onto white; RGB; 2x Lanczos for OCR",
                }
                return rgb, metadata
    except HTTPException:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(413, "이미지의 픽셀 수가 허용 범위를 초과합니다.") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise HTTPException(
            415,
            "이미지를 읽을 수 없습니다. 손상되지 않은 JPEG, PNG, WEBP 파일을 선택해 주세요.",
        ) from None


def _default_recognizer(path: Path) -> dict:
    # 실행 전 검사와 같은 기본값을 사용하고 명시한 비교 경로도 유지한다.
    pipeline = selected_pipeline()
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
        raise ValueError(
            "OCR_PIPELINE must be baseline, multiline, adaptive, paddle, paddle-only or qwen"
        )
    from .ocr_runtime import recognize_image

    payload = recognize_image(path)
    if pipeline == "multiline":
        payload["pipeline"] = "multiline_v2"
    return payload


def _runtime_identity() -> dict:
    """실패 전에도 선택한 엔진과 실행 경로를 민감 정보 없이 표시한다."""
    pipeline = selected_pipeline()
    engine = {
        "paddle-only": "paddleocr",
        "paddle": "easyocr+paddleocr",
        "qwen": "easyocr+qwen3-vl",
    }.get(pipeline, "easyocr")
    metadata = {
        "engine": engine,
        "execution_target": os.getenv("RUN_TARGET", "local").strip().lower(),
    }
    name = {
        "multiline": "multiline_v2",
        "adaptive": "adaptive_v1",
        "paddle": "paddle_regions_v1",
        "paddle-only": "paddle_full_v1",
        "qwen": "qwen_regions_v1",
    }.get(pipeline)
    if name:
        metadata["pipeline"] = name
    return metadata


def _default_runtime_status() -> dict:
    """선택한 경로만 점검하여 Paddle 단독 건강 검사에서 EasyOCR를 불러오지 않는다."""
    pipeline = selected_pipeline()
    target = os.getenv("RUN_TARGET", "local").strip().lower()
    identity = _runtime_identity()
    if pipeline not in PIPELINE_NAMES or target not in {"local", "runpod"}:
        return {
            **identity,
            "ready": False,
            "status": "not_ready",
            "detail": "Invalid OCR configuration",
        }
    if pipeline in LOCAL_PIPELINES and target != "local":
        return {
            **identity,
            "ready": False,
            "status": "not_ready",
            "detail": "Selected pipeline requires RUN_TARGET=local",
        }
    if pipeline == "paddle-only":
        from .paddle_ocr import paddle_readiness

        ready, detail = paddle_readiness()
        return {
            **identity,
            "device": "cpu",
            "ready": ready,
            "status": "ready" if ready else "not_ready",
            "detail": detail,
        }
    from .ocr_runtime import get_runtime_status

    status = get_runtime_status()
    if pipeline in {"paddle", "qwen"}:
        from .model_ocr import model_readiness, ENGINES, PIPELINES

        ready, detail = model_readiness(pipeline)
        status.update(
            engine=ENGINES[pipeline],
            pipeline=PIPELINES[pipeline],
            ready=status["ready"] and ready,
            detail=detail,
            device="unknown",
        )
    return status


def _safe_runtime(payload: dict) -> dict:
    """Return public execution metadata, never URLs, credentials or tracebacks."""
    allowed_values = {
        "engine": {"easyocr", "paddleocr", "easyocr+paddleocr", "easyocr+qwen3-vl"},
        "execution_target": {"local", "runpod"},
        "device": {"cuda", "mps", "cpu", "gpu", "runpod", "remote-auto", "unknown"},
        "status": {
            "ok",
            "ready",
            "completed",
            "failed",
            "unavailable",
            "not_ready",
            "configured",
            "success",
            "skipped",
            "fallback",
        },
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
    if payload.get("refinement_status") in {
        "completed",
        "partial",
        "timeout",
        "worker_failed",
    }:
        result["refinement_status"] = payload["refinement_status"]
    # Attempts deliberately omit free-form errors that may contain secrets.
    result["attempts"] = []
    for attempt in (
        payload.get("attempts", []) if isinstance(payload.get("attempts"), list) else []
    ):
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
        if (
            not isinstance(record_id, (str, int))
            or isinstance(record_id, bool)
            or record_id in seen
        ):
            raise ValueError("Invalid OCR ID")
        seen.add(record_id)
        text = raw["text"].strip()
        if not text:
            continue
        if len(text) > 10_000:
            raise ValueError("OCR text is too long")
        box = raw.get("bbox_xyxy")
        if (
            not isinstance(box, (list, tuple))
            or len(box) != 4
            or any(
                isinstance(number, bool)
                or not isinstance(number, (int, float))
                or not math.isfinite(number)
                for number in box
            )
        ):
            raise ValueError("Invalid OCR bounding box")
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError("Degenerate OCR bounding box")
        coordinates = [
            max(0, min(limit, float(number) / 2))
            for number, limit in zip(box, [width, height, width, height])
        ]
        if coordinates[2] <= coordinates[0] or coordinates[3] <= coordinates[1]:
            raise ValueError("OCR bounding box outside image")
        confidence = raw.get("confidence")
        if confidence is not None and (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ValueError("Invalid OCR confidence")
        record = {
            "id": record_id,
            "text": text,
            "confidence": confidence,
            "bbox_xyxy": coordinates,
        }
        # 재인식 정보도 허용한 값만 공개하고 입력 좌표는 한 번만 복원한다.
        if "requires_review" in raw:
            if type(raw["requires_review"]) is not bool:
                raise ValueError("Invalid review flag")
            record["requires_review"] = raw["requires_review"]
        if "baseline_text" in raw:
            if (
                not isinstance(raw["baseline_text"], str)
                or len(raw["baseline_text"]) > 10_000
            ):
                raise ValueError("Invalid baseline text")
            record["baseline_text"] = raw["baseline_text"]
        # 원문 근거의 점수·엔진·보류 사유를 검증해 항목별 재판정에 전달한다.
        if "baseline_confidence" in raw:
            baseline_confidence = raw["baseline_confidence"]
            if baseline_confidence is not None and (
                type(baseline_confidence) not in (int, float)
                or not math.isfinite(baseline_confidence)
                or not 0 <= baseline_confidence <= 1
            ):
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
            candidates = [
                {
                    "id": f"candidate-{i}",
                    "text": c.get("text"),
                    "confidence": c.get("confidence"),
                    "bbox_xyxy": c.get("bbox_xyxy"),
                }
                for i, c in enumerate(alternatives)
            ]
            record["alternatives"] = _map_records(
                {"records": candidates}, width, height
            )
            for candidate, raw_candidate in zip(record["alternatives"], alternatives):
                if raw_candidate.get("engine") in RECOGNITION_ENGINES:
                    candidate["engine"] = raw_candidate["engine"]
        # 단독 Paddle의 출처가 빠져도 EasyOCR 기본 점수 기준으로 낮아지지 않게 한다.
        if "recognition_engine" in raw:
            if (
                not isinstance(raw["recognition_engine"], str)
                or raw["recognition_engine"] not in RECOGNITION_ENGINES
            ):
                raise ValueError("Invalid recognition engine")
            record["recognition_engine"] = raw["recognition_engine"]
        if (
            payload.get("engine") == "paddleocr"
            or payload.get("pipeline") == "paddle_full_v1"
        ):
            if record.get("recognition_engine", "paddleocr") != "paddleocr":
                raise ValueError("Paddle payload recognition engine mismatch")
            record["recognition_engine"] = "paddleocr"
        if raw.get("polygon") is not None:
            polygon = raw["polygon"]
            if (
                not isinstance(polygon, list)
                or len(polygon) < 4
                or len(polygon) > 32
                or any(
                    not isinstance(p, list)
                    or len(p) != 2
                    or any(
                        type(v) not in (int, float) or not math.isfinite(v) for v in p
                    )
                    for p in polygon
                )
            ):
                raise ValueError("Invalid OCR polygon")
            record["polygon"] = [
                [max(0, min(width, p[0] / 2)), max(0, min(height, p[1] / 2))]
                for p in polygon
            ]
        mapped.append(record)
    return mapped


def create_app(
    *,
    recognizer: Callable[[Path], dict] | None = None,
    runtime_status: Callable[[], dict] | None = None,
    review_root: Path | None = None,
    paddle_refiner: Callable | None = None,
    postgres_store=None,
    video_root: Path | None = None,
    video_ocr: Callable | None = None,
    video_readiness: Callable[[], tuple[bool, str]] | None = None,
    video_logo_review: Callable | None = None,
    video_logo_readiness: Callable[[], tuple[bool, str]] | None = None,
) -> FastAPI:
    # Retirement is explicit. The original development launcher keeps its default.
    readonly = os.getenv('LEGACY_READ_ONLY', '0')
    if readonly not in ('0', '1') or readonly == '1' and os.getenv('APP_ENV', 'development') not in ('development', 'test'):
        raise ValueError('Legacy read-only mode is restricted to explicit local development.')
    readonly = readonly == '1'
    if postgres_store is None and review_root is None and not readonly:
        configured=Path(os.environ.get('CC_WEB_POSTGRES_ENV_FILE',PROJECT_ROOT/'.runtime/postgres-web.env'))
        if configured.exists() or os.environ.get('CC_WEB_POSTGRES_ENV_FILE'):
            from .web_postgres import PostgresWebStore
            postgres_store=PostgresWebStore.configured(PROJECT_ROOT)
    recognize = recognizer or _default_recognizer
    runtime_health = runtime_status or _default_runtime_status
    storage = review_root or PROJECT_ROOT / ".runtime" / "reviews"
    if readonly:
        if not storage.is_dir():raise ValueError('Legacy read-only mode requires existing history.')
    else:storage.mkdir(parents=True, exist_ok=True)
    # 동영상 검수 시연 결과는 포스터 검수 기록과 섞이지 않게 별도 폴더에 둔다.
    video_storage = video_root or PROJECT_ROOT / ".runtime" / "video-reviews"
    if not readonly:
        video_storage.mkdir(parents=True, exist_ok=True)
    lock = threading.RLock()
    inference_lock = threading.Lock()
    requirements_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if postgres_store is not None:
            try:
                await run_in_threadpool(postgres_store.open,_app)
                yield
            finally:
                await run_in_threadpool(postgres_store.close)
            return
        # Background work is local to this process. Never present interrupted
        # persisted jobs as if inference were still running after a restart.
        for path in (() if readonly else storage.glob("*/result.json")):
            try:
                value = _read_json(path)
                if value.get("status") in {"queued", "running"}:
                    value.update(
                        status="failed",
                        stage="failed",
                        error="서버가 재시작되어 검수가 중단됐습니다. 다시 실행해 주세요.",
                        updated_at=_now(),
                    )
                    _write_json(path, value)
            except (OSError, ValueError):
                continue
        if not readonly:
            from .video_review import recover_jobs

            recover_jobs(video_storage)
        yield

    application = FastAPI(
        title="크리에이티브체커 API", version="1.0.0", lifespan=lifespan
    )
    application.state.postgres_store=postgres_store
    if postgres_store is not None:
        from .routes.v1.auth import router as postgres_auth_router
        from .security.errors import AuthError
        from .persistence.database import PersistenceError
        from starlette.responses import JSONResponse

        @application.middleware('http')
        async def postgres_boundary(request,call_next):
            request.state.request_id=str(uuid4())
            try:
                if request.url.hostname not in postgres_store.settings.hosts:
                    raise AuthError('RESOURCE_NOT_FOUND',404)
                if request.url.path.startswith('/api/'):
                    if request.url.path!='/api/health':
                        from .management.maintenance import active
                        if await run_in_threadpool(active,postgres_store.engine):
                            raise AuthError('MAINTENANCE_ACTIVE',503)
                    if request.method not in ('GET','HEAD','OPTIONS'):
                        application.state.csrf.verify(request,postgres_store.settings)
                    if request.url.path.startswith(('/api/reviews','/api/requirements')):
                        from .routes.v1.business import subject
                        request.state.web_subject=await run_in_threadpool(subject,request)
                response=await call_next(request)
            except (AuthError,PersistenceError) as error:
                response=JSONResponse({'error':{'code':error.code,'message':str(error)}},
                                      status_code=getattr(error,'status',503))
            if request.url.path.startswith('/api/'):
                response.headers['Cache-Control']='no-store'
            return response

        @application.exception_handler(AuthError)
        async def postgres_auth_error(request,error):
            return JSONResponse({'error':{'code':error.code,'message':str(error)}},status_code=error.status)

        @application.exception_handler(PersistenceError)
        async def postgres_database_error(request,error):
            return JSONResponse({'error':{'code':'DATABASE_UNAVAILABLE','message':'DB 저장·조회에 실패했습니다. 연결 상태를 확인해 주세요.'}},status_code=503)

        application.include_router(postgres_auth_router())
    if readonly:
        from .legacy_boundary import local_read_request, review_path, retired_response
        from starlette.responses import JSONResponse
        @application.middleware('http')
        async def legacy_read_boundary(request, call_next):
            if not local_read_request(request):
                return JSONResponse({'detail': '찾을 수 없습니다.'}, status_code=404)
            if review_path(request.url.path) and request.method not in ('GET', 'HEAD', 'OPTIONS'):
                return retired_response(str(uuid4()))
            response = await call_next(request)
            response.headers['Cache-Control'] = 'no-store'
            return response
    # localhost의 임시 admin 로그인은 환경 변수로 해제할 수 있다.
    auth_router = None
    if postgres_store is None and os.getenv("LOCAL_ADMIN_LOGIN", "1") == "1":
        auth_router = local_auth_router()
        application.include_router(auth_router)
    if not readonly and os.getenv('APP_ENV', 'development') in ('development', 'test'):
        from .modeltest_ssh import modeltest_ssh_router
        application.include_router(modeltest_ssh_router(PROJECT_ROOT / '.runtime' / 'modeltest-ssh'))
    allowed_origins = [
        value.strip()
        for value in os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",")
        if value.strip()
    ]
    allowed_origins = [
        value
        for value in allowed_origins
        if value.startswith(("http://localhost:", "http://127.0.0.1:"))
    ]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-CSRFToken"],
    )

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
            if postgres_store is not None:
                postgres_store.update(target.name,result)
            _write_json(target / "result.json", result)

    def _run_review(target: Path) -> None:
        stage = "preprocessing"
        try:
            update(target, status="running", stage=stage, progress=15)
            with Image.open(target / "normalized.png") as image:
                width, height = image.size
                image.resize((width * 2, height * 2), Image.Resampling.LANCZOS).save(
                    target / "ocr_input.png"
                )
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
            snapshot = _read_json(target / 'snapshot.json')
            conditional_retry = None
            if (payload.get('pipeline') == 'paddle_full_v1' and os.getenv('OCR_CONDITION_RETRY', '1') == '1'
                    and (recognizer is None or paddle_refiner is not None)):
                update(target, progress=55, progress_message='조건별 후보 영역을 찾아 문구를 다시 읽고 있습니다.')
                try:
                    from .paddle_conditions import refine
                    records, conditional_retry = (paddle_refiner or refine)(
                        target/'normalized.png', records, snapshot['criteria'], snapshot['image_source'], target/'paddle-conditional')
                except Exception as error:
                    conditional_retry = {'version':'conditional_paddle_v1','status':'failed',
                                         'error_type':type(error).__name__,'attempts':[]}
            ocr = {
                "records": records,
                "input_dimensions": [width * 2, height * 2],
                "coordinate_space": "normalized_original_pixels",
                "scale": 2,
            }
            if conditional_retry is not None:
                ocr['conditional_retry'] = conditional_retry
            multiline = payload.get("pipeline") in MULTILINE_PIPELINES
            if multiline:
                from .event_layout import extract_event_multiline

                ocr["event"] = extract_event_multiline(records)
            _write_json(target / "ocr.json", {**ocr, **runtime})
            stage = "review"
            update(target, stage=stage, progress=80, ocr=ocr, progress_message=None)
            results = review_criteria(
                snapshot["criteria"], records, multiline=multiline
            )
            from . import review_models
            model_runtime = {"status": "not_configured"}
            if review_models.configured():
                stage = "review_models"
                update(target, stage="review", progress=90)
                model_output = review_models.infer(target / "normalized.png", snapshot["criteria"])
                results = review_models.enrich(results, model_output)
                model_runtime = {"status": "success", **model_output["model"]}
            from .structured_review import compare
            results = compare(snapshot["criteria"], results, records, snapshot["image_source"])
            update(
                target,
                status="completed",
                stage="completed",
                progress=100,
                results=results,
                model_runtime=model_runtime,
                error=None,
            )
        except Exception as error:
            messages = {
                "preprocessing": "이미지 전처리에 실패했습니다. 이미지 크기와 서버 저장 공간을 확인해 주세요.",
                "ocr": "OCR 실행에 실패했습니다. 모델 설치·다운로드 설정과 CUDA/MPS/CPU 또는 RunPod 연결 설정을 확인한 뒤 다시 실행해 주세요.",
                "review": "OCR 결과를 검수 기준과 비교하지 못했습니다. 기준 형식과 OCR 출력을 확인해 주세요.",
                "review_models": "런팟 모델 연결이나 추론에 실패했습니다. SSH 터널과 모델 서버 상태를 확인한 뒤 다시 실행해 주세요.",
            }
            if (
                stage == "ocr"
                and getattr(error, "code", None) == "remote_image_too_large"
            ):
                messages["ocr"] = (
                    "2배 전처리한 이미지가 RunPod의 전송 한도 7 MiB를 초과했습니다. 원본 이미지 크기를 줄이거나 로컬 실행(CUDA → MPS → CPU)을 선택해 주세요."
                )
            failure_metadata = {}
            if stage == "ocr":
                failure_metadata["runtime"] = _safe_runtime(
                    {
                        **_runtime_identity(),
                        "status": "failed",
                        "attempts": getattr(error, "attempts", []),
                    }
                )
            update(
                target,
                status="failed",
                stage="failed",
                error=messages[stage],
                **failure_metadata,
            )

    def run_review(target: Path) -> None:
        # Only one image is expanded/inferred at a time in this local process.
        # A job waiting for this lock remains truthfully queued.
        with inference_lock:
            try:
                if postgres_store is not None:postgres_store.claim(target.name)
                _run_review(target)
            finally:
                if postgres_store is not None:postgres_store.claims.pop(target.name,None)

    @application.get("/api/health")
    def health():
        try:
            if postgres_store is not None:
                postgres_store.leader.check()
                with postgres_store.engine.connect() as connection:connection.exec_driver_sql('SELECT 1')
            status = runtime_health()
            return {
                "status": "ok",
                "persistence": "postgres" if postgres_store is not None else "legacy_json",
                "runtime": _safe_runtime(status),
                "limits": {
                    "upload_bytes": MAX_UPLOAD_BYTES,
                    "image_pixels": MAX_IMAGE_PIXELS,
                    "image_side": MAX_IMAGE_SIDE,
                },
            }
        except Exception:
            return {
                "status": "degraded",
                "runtime": {"status": "unavailable"},
                "message": "OCR 실행 환경을 확인해 주세요.",
            }

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
        return FileResponse(
            reference, media_type=media_type, filename=f"gimcheon-2026.{extension}"
        )

    async def read_requirements_upload(document):
        if readonly:
            await document.close()
            raise HTTPException(410, "읽기 전용 모드에서는 문서를 분석할 수 없습니다.")
        try:
            data = await document.read(MAX_PDF_BYTES + 1)
        finally:
            await document.close()
        if not data:
            raise HTTPException(400, "빈 파일입니다. PDF 파일을 선택해 주세요.")
        if len(data) > MAX_PDF_BYTES:
            raise HTTPException(413, "PDF는 10MiB 이하로 올려 주세요.")
        if not data.startswith(b"%PDF-"):
            raise HTTPException(415, "PDF 파일만 읽을 수 있습니다. 파일의 실제 형식을 확인해 주세요.")
        return data

    def acquire_requirements_locks():
        if not requirements_lock.acquire(blocking=False):
            raise HTTPException(429, "다른 문서를 분석하고 있습니다. 잠시 후 다시 시도해 주세요.")
        if not inference_lock.acquire(blocking=False):
            requirements_lock.release()
            raise HTTPException(429, "이미지 검수가 진행 중입니다. 완료된 뒤 PDF를 다시 올려 주세요.")

    @application.post("/api/requirements/extract")
    async def extract_requirements(document: UploadFile = File(...)):
        data = await read_requirements_upload(document)
        acquire_requirements_locks()
        try:
            return await run_in_threadpool(extract_document, data)
        except DocumentError as error:
            raise HTTPException(error.status, str(error)) from None
        except Exception:
            raise HTTPException(502, "PDF 조건 추출에 실패했습니다. 잠시 후 다시 시도해 주세요.") from None
        finally:
            inference_lock.release()
            requirements_lock.release()

    @application.post('/api/requirements/extract/stream')
    async def stream_requirements(document: UploadFile = File(...)):
        data = await read_requirements_upload(document)
        acquire_requirements_locks()
        events = queue.SimpleQueue()
        events.put({'type': 'progress', 'stage': 'uploaded', 'percent': 2,
                    'message': 'PDF 업로드를 마쳤습니다. 분석을 준비하고 있습니다.'})

        def work():
            try:
                result = extract_document(data, progress=lambda update: events.put({'type': 'progress', **update}))
                terminal = {'type': 'result', 'result': result}
            except DocumentError as error:
                terminal = {'type': 'error', 'message': str(error), 'status': error.status}
            except Exception:
                terminal = {'type': 'error', 'message': 'PDF 조건 추출에 실패했습니다. 잠시 후 다시 시도해 주세요.', 'status': 502}
            finally:
                # 브라우저가 닫혀도 실행 중인 추론이 끝나기 전에는 잠금을 풀지 않는다.
                inference_lock.release()
                requirements_lock.release()
            events.put(terminal)

        try:
            threading.Thread(target=work, name='pdf-requirements-stream', daemon=True).start()
        except Exception:
            inference_lock.release()
            requirements_lock.release()
            raise HTTPException(503, 'PDF 분석을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.') from None

        def stream():
            while True:
                try:
                    event = events.get(timeout=1)
                except queue.Empty:
                    yield '\n'
                    continue
                yield json.dumps(event, ensure_ascii=False) + '\n'
                if event['type'] in {'result', 'error'}:
                    break

        return StreamingResponse(stream(), media_type='application/x-ndjson',
                                 headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    @application.post("/api/reviews", status_code=202)
    async def submit(
        request: Request,
        background_tasks: BackgroundTasks,
        image: UploadFile = File(...),
        criteria: str = Form(...),
        criteria_version: int = Form(..., ge=1, le=1_000_000),
        project_name: str = Form(..., min_length=1, max_length=120),
    ):
        if len(criteria) > CRITERIA_MAX_JSON:
            raise HTTPException(
                422,
                "검수 기준이 너무 깁니다. 최대 100개, 항목당 500자까지 입력해 주세요.",
            )
        try:
            frozen = CriteriaSnapshot(
                criteria=json.loads(criteria),
                criteria_version=criteria_version,
                project_name=project_name,
            )
        except (json.JSONDecodeError, ValidationError, TypeError):
            raise HTTPException(
                422,
                "확정한 기준을 확인해 주세요. 고유 ID와 유형, 1~500자의 내용이 있는 기준 1~100개가 필요합니다.",
            ) from None
        try:
            data = await image.read(MAX_UPLOAD_BYTES + 1)
        finally:
            await image.close()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "이미지 파일은 20 MiB 이하로 업로드해 주세요.")
        if not data:
            raise HTTPException(400, "빈 이미지 파일입니다.")
        rgb, source = await run_in_threadpool(_normalise_image, data)
        source['original_extension'] = Path(image.filename or '').suffix.lower()[:16]
        review_id = str(uuid4())
        snapshot = {**frozen.model_dump(exclude_none=True), "image_source": source, "created_at": _now()}
        result = {
            "id": review_id,
            "status": "queued",
            "stage": "queued",
            "progress": 5,
            "criteria_version": frozen.criteria_version,
            "project_name": frozen.project_name,
            "criteria": [item.model_dump(exclude_none=True) for item in frozen.criteria],
            "image": {
                "width": rgb.width,
                "height": rgb.height,
                "url": f"/api/reviews/{review_id}/image",
                "sha256": source["original_sha256"],
                "actual_format": source["actual_format"],
                "original_mode": source['original_mode'],
                "original_extension": source['original_extension'],
                "original_frames": source['original_frames'],
            },
            "results": [],
            "runtime": None,
            "ocr": None,
            "error": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        if postgres_store is not None:
            try:
                review_id=await run_in_threadpool(postgres_store.admit,*request.state.web_subject,data,snapshot,result,request.state.request_id)
            except Exception:
                rgb.close()
                raise
            result['id']=review_id
            result['image']['url']=f'/api/reviews/{review_id}/image'
        target = storage / review_id
        target.mkdir()
        # Diagnostic JSON copies and original bytes remain; PostgreSQL is primary.
        (target / "original.bin").write_bytes(data)
        rgb.save(target / "normalized.png")
        _write_json(target / "snapshot.json", snapshot)
        _write_json(target / "result.json", result)
        rgb.close()
        background_tasks.add_task(run_review, target)
        return {"id": review_id, "status": "queued"}

    @application.get("/api/reviews/{review_id}")
    def get_review(review_id: str, request: Request):
        if postgres_store is not None:
            value=postgres_store.read(review_id,*request.state.web_subject)
            if value is not None:return value
            if request.state.web_subject[0].account_kind!='developer':raise HTTPException(404,'검수 기록을 찾을 수 없습니다.')
        with lock:
            return _read_json(directory(review_id) / "result.json")

    @application.get("/api/reviews/{review_id}/image")
    def review_image(review_id: str, request: Request):
        if postgres_store is not None:
            path=postgres_store.image(review_id,*request.state.web_subject)
            if path is not None:return FileResponse(path,media_type='image/png')
            if request.state.web_subject[0].account_kind!='developer':raise HTTPException(404,'검수 기록을 찾을 수 없습니다.')
        return FileResponse(
            directory(review_id) / "normalized.png", media_type="image/png"
        )

    @application.get('/api/reviews/{review_id}/original')
    def review_original(review_id: str,request: Request):
        if postgres_store is not None:
            path=postgres_store.image(review_id,*request.state.web_subject,original=True)
            if path is not None:return FileResponse(path,media_type='application/octet-stream')
            if request.state.web_subject[0].account_kind!='developer':raise HTTPException(404,'검수 기록을 찾을 수 없습니다.')
        return FileResponse(directory(review_id)/'original.bin',media_type='application/octet-stream')
    # 동영상 검수 시연: 포스터 검수와 같은 추론 잠금으로 CPU 작업을 하나씩 실행한다.
    from .video_review import video_review_router

    application.include_router(video_review_router(
        root=video_storage, inference_lock=inference_lock, readonly=readonly,
        ocr=video_ocr, readiness=video_readiness,
        logo_review=video_logo_review, logo_readiness=video_logo_readiness,
        authenticate=auth_router.session_user if auth_router else None,
    ))

    # API routes are defined first. Only the built React assets are public.
    frontend = PROJECT_ROOT / "frontend" / "dist"
    if frontend.is_dir():
        application.mount(
            "/", ReviewStaticFiles(directory=frontend, html=True), name="frontend"
        )
    return application


app = create_app()
