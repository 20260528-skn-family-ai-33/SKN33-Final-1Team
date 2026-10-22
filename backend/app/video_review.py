"""숏폼 동영상 검수 시연 모듈 — 기존 시제품(api.py) 전용.

계획 문서(숏폼_검수_최종판 2026-10-20)의 0~7단계를 따른다.
  0 입력 검증 → 1 프레임 추출·검사 범위 → 2 장면 경계 → 3 프레임 OCR(배치) → 3-1 자막 줄 분류
  → 4 자막 트랙·구간 → 5 대표 프레임 → 6 기준 판정(영상당 1회) + 6-A 광고·협찬 표시 → (6-1 이중 검수: 꺼짐) → 7 결과
결과는 .runtime/video-reviews/<id>/ 아래에만 둔다. 문서의 실행 구조(v1 PostgreSQL 큐·독립 worker·DB 저장)는
팀 결정 사항이라 여기서는 기존 시제품의 추론 잠금으로 대신하며, v1 앱(main.py)에는 연결하지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Callable
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from . import video_subtitle_roles as roles
from .video_disclosure import judge_disclosure, validate_input
from .video_frames import coverage, detect_scenes, extract_frames, plan_times, probe, resolve_ffmpeg, sniff_container
from .video_judge import judge
from .video_tracks import build_segments, build_tracks, choose_representatives, text_groups

PIPELINE = "paddle_frames_v1"   # 포스터 경로(paddle_full_v1)와 구분
CHUNK = 1024 * 1024
MIN_OCR_SUCCESS = 0.9
# 문서 '실행 구조' 표의 stage와 progress
PROGRESS = {"queued": 5, "extracting": 5, "scene_detection": 10, "ocr": 15, "classifying": 88,
            "segmenting": 90, "review": 93, "double_check": 97, "completed": 100}
FAILURE_MESSAGES = {
    "extracting": "영상에서 표본 프레임을 추출하지 못했습니다. 잘린 파일이거나 코덱을 읽을 수 없습니다.",
    "ocr": "프레임 OCR 실행에 실패했습니다. Paddle 전용 환경과 모델 준비 상태를 확인한 뒤 다시 실행해 주세요.",
    "segmenting": "OCR 결과를 자막 트랙과 구간으로 정리하지 못했습니다. 실행 기록을 확인해 주세요.",
    "review": "기준 판정 또는 광고 표시 판정을 완료하지 못했습니다. 기준 형식을 확인해 주세요.",
}
CONTAINER_MIME = {"mov": "video/mp4", "matroska": "video/webm", "avi": "video/x-msvideo"}
FRAME_ID = re.compile(r"^t\d{6}$")


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def limits() -> dict:
    """시연 기본값 3분·500MB(사용자 결정, 2026-10-22). 계획 문서 0단계의 기본값은 기능정의서 X-01의 30초·50MB이며,
    환경 변수 VIDEO_MAX_SECONDS·VIDEO_MAX_MB로 바꿀 수 있다. 3분 영상은 CPU에서 약 1시간 걸린다(204장 × 장당 약 18초)."""
    return {
        "upload_bytes": _int_env("VIDEO_MAX_MB", 500, 1, 2048) * 1024 * 1024,
        "duration_seconds": _int_env("VIDEO_MAX_SECONDS", 180, 1, 600),
        "short_side_min": 360, "long_side_max": 3840, "fps_max": 120,
        "active_jobs": _int_env("VIDEO_MAX_ACTIVE", 3, 1, 20),
        "formats": ["mp4", "mov", "webm", "avi"],
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value) -> None:
    temp = path.with_suffix(f".{uuid4().hex}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows), encoding="utf-8")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 3단계 — Paddle 전용 환경에서 프레임 전체를 한 프로세스로
# ---------------------------------------------------------------------------
def ocr_readiness() -> tuple[bool, str]:
    from .paddle_ocr import paddle_readiness
    return paddle_readiness()


def paddle_frames(frames: list[Path], directory: Path, progress: Callable[[int], None]) -> dict:
    """모델을 한 번 불러 모든 프레임을 읽는다. 프레임 하나의 실패는 그 프레임만 'failed'로 남긴다."""
    from PIL import Image
    from scripts.prepare_paddle import DETECTION_MODEL, MODEL, verify_model
    from .ocr_runtime import OCRRuntimeError, PROJECT_ROOT
    from .paddle_ocr import normalize_records, paddle_paths, paddle_readiness, worker_environment

    ready, detail = paddle_readiness()
    if not ready:
        raise OCRRuntimeError(detail, code="model_not_ready")
    python, detection, recognition = paddle_paths()
    listing, output = directory / "frames.txt", directory / "paddle.raw.jsonl"
    listing.write_text("\n".join(str(p.resolve()) for p in frames), encoding="utf-8")
    # 3분 영상은 204장 × CPU 장당 약 18초(실측) ≈ 1시간이라 상한을 4시간으로 둔다
    deadline = time.monotonic() + min(4 * 3600, 120 + 90 * len(frames))
    with (directory / "paddle.video.log").open("w", encoding="utf-8") as log:
        worker = subprocess.Popen([str(python), "-m", "backend.app.paddle_video_worker", "--frames", str(listing),
                                   "--output", str(output), "--detection-dir", str(detection),
                                   "--recognition-dir", str(recognition)],
                                  cwd=PROJECT_ROOT, env=worker_environment(), stdout=log, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL)
        try:
            while worker.poll() is None:
                if time.monotonic() > deadline:
                    raise subprocess.TimeoutExpired(worker.args, 0)
                if output.exists():
                    with output.open(encoding="utf-8") as stream:
                        progress(max(0, sum(1 for _ in stream) - 1))
                time.sleep(1)
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait()
    if worker.returncode != 0:
        raise subprocess.CalledProcessError(worker.returncode, worker.args)
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
    header, results = rows[0], rows[1:]
    identity = header["model_identity"]
    if (header.get("kind") != "header" or header.get("device") != "cpu"
            or identity["detection"] != verify_model(detection, DETECTION_MODEL)
            or identity["recognition"] != verify_model(recognition, MODEL)):
        raise ValueError("Paddle video output identity mismatch")
    if [row.get("index") for row in results] != list(range(len(frames))):
        raise ValueError("Paddle video frame count mismatch")
    out = []
    for frame, row in zip(frames, results):
        try:
            if "error" in row:
                raise ValueError(row["error"])
            with Image.open(frame) as opened:
                width, height = opened.size
            if row.get("input_dimensions") != [width, height]:
                raise ValueError("dimensions")
            records = normalize_records(row["raw"], width, height)
            raw = row["raw"].get("res", row["raw"])
            out.append({"status": "ok", "records": records, "unread_boxes": len(raw.get("rec_texts", [])) - len(records)})
        except (ValueError, KeyError, TypeError, AttributeError):
            out.append({"status": "failed", "records": []})
    return {"frames": out, "load_seconds": header.get("load_seconds"),
            "inference_seconds": [row.get("inference_seconds") for row in results]}


def load_role_weights():
    """VIDEO_ROLES_MODEL(학습한 가중치 JSON)이 있으면 모델, 없으면 원본의 '신뢰도 0.5 하한' 규칙(문서 3-1 첫 판)."""
    configured = os.getenv("VIDEO_ROLES_MODEL")
    if not configured:
        return None, "rule_fallback"
    return roles.LogRegWeights.load(configured), "logreg"


def recover_jobs(root: Path) -> None:
    """프로세스 안에서만 도는 작업이라, 재시작 전 대기·실행 중이던 작업을 실패로 닫는다."""
    for path in root.glob("*/result.json"):
        try:
            value = _read_json(path)
            if value.get("status") in {"queued", "running"}:
                value.update(status="failed", stage="failed", failed_stage=value.get("stage"), updated_at=_now(),
                             error="서버가 재시작되어 동영상 검수가 중단됐습니다. 다시 실행해 주세요.")
                _write_json(path, value)
        except (OSError, ValueError):
            continue


def _mean(values) -> float | None:
    numbers = [v for v in values or [] if isinstance(v, (int, float))]
    return round(sum(numbers) / len(numbers), 2) if numbers else None


# ---------------------------------------------------------------------------
# 1~7단계 실행
# ---------------------------------------------------------------------------
class Pipeline:
    def __init__(self, target: Path, recognize, update):
        self.target, self.recognize, self.update = target, recognize, update
        self.warnings: list[str] = []

    def run(self, ffmpeg: str, demuxer: str) -> dict:
        target = self.target
        media, snapshot = _read_json(target / "media.json"), _read_json(target / "snapshot.json")
        duration = media["duration_ms"]
        disclosure = snapshot["disclosure"]

        self.stage = "extracting"
        self.update(target, status="running", stage=self.stage, progress=PROGRESS["extracting"])
        plan = plan_times(duration, edges=disclosure["kind"] != "self")  # 자체 광고면 시작·끝 창 전용 프레임 생략
        frames = extract_frames(ffmpeg, demuxer, target / "original.bin", target / "frames", plan)
        cover = coverage(plan, duration)
        _write_json(target / "frames.json", frames)
        _write_json(target / "coverage.json", cover)

        self.stage = "scene_detection"
        self.update(target, stage=self.stage, progress=PROGRESS["scene_detection"], stage_detail=f"프레임 {len(frames)}장")
        scenes = detect_scenes(target / "original.bin", [f["time_ms"] for f in frames if f["sampling"] == "base"])
        _write_json(target / "scenes.json", scenes)
        if scenes["status"] != "completed":
            self.warnings.append(scenes["reason"])

        self.stage = "ocr"
        unique = [f for f in frames if not f["same_as"]]
        total = len(unique)
        span = PROGRESS["classifying"] - PROGRESS["ocr"]
        last = [0.0]

        def progress(done: int) -> None:
            now = time.monotonic()
            if now - last[0] >= 1 or done >= total:  # 1초에 한 번 기록
                last[0] = now
                self.update(target, progress=PROGRESS["ocr"] + span * min(done, total) // max(1, total),
                            stage_detail=f"프레임 {min(done, total)} / {total}")

        self.update(target, stage=self.stage, progress=PROGRESS["ocr"], stage_detail=f"프레임 0 / {total}")
        (target / "ocr").mkdir(exist_ok=True)
        payload = self.recognize([target / "frames" / f"{f['frame_id']}.jpg" for f in unique], target / "ocr", progress)
        if not isinstance(payload, dict) or len(payload.get("frames", [])) != total:
            raise ValueError("Invalid video OCR response")
        by_id = {f["frame_id"]: r for f, r in zip(unique, payload["frames"])}
        for frame in frames:
            result = by_id[frame["same_as"] or frame["frame_id"]]
            frame["ocr_status"], frame["records"] = result["status"], result["records"]
            frame["unread_boxes"] = result.get("unread_boxes", 0)
        succeeded = sum(1 for f in frames if f["ocr_status"] == "ok") / max(1, len(frames))
        _write_jsonl(target / "ocr" / "frames.jsonl", [{
            "frame_id": f["frame_id"], "time_ms": f["time_ms"], "sampling": f["sampling"], "width": f["width"],
            "height": f["height"], "status": f["ocr_status"], "same_as": f["same_as"], "unread_boxes": f["unread_boxes"],
            "lines": [{"line_id": f"{f['frame_id']}:{r['id']}", "text": r["text"], "bbox_xyxy": r["bbox_xyxy"],
                       "confidence": r["confidence"]} for r in f["records"]]} for f in frames])
        if succeeded < MIN_OCR_SUCCESS:
            raise ValueError(f"OCR success ratio {succeeded:.2f}")
        failed = sum(1 for f in frames if f["ocr_status"] == "failed")
        if failed:
            self.warnings.append(f"OCR이 실패한 프레임 {failed}장은 판정에서 위반을 확정하지 않습니다.")
        runtime = {"engine": "paddleocr", "pipeline": PIPELINE, "device": "cpu", "load_seconds": payload.get("load_seconds"),
                   "seconds_per_frame": _mean(payload.get("inference_seconds")), "ocr_frames": total,
                   "ocr_success_ratio": round(succeeded, 4)}

        self.stage = "classifying"
        self.update(target, stage=self.stage, progress=PROGRESS["classifying"], runtime=runtime)
        role_frames = [{"video_id": target.name, "frame_id": f["frame_id"], "time_ms": f["time_ms"], "width": f["width"],
                        "height": f["height"], "lines": [{"line_id": f"{f['frame_id']}:{r['id']}", "text": r["text"],
                                                          "bbox_xyxy": r["bbox_xyxy"], "confidence": r["confidence"]}
                                                         for r in f["records"]]} for f in frames]
        try:
            weights, classifier = load_role_weights()
            role_rows = roles.classify(role_frames, weights)
        except Exception:  # 분류 실패는 경고만 — 신뢰도 0.5 하한으로 대체(문서 실행 구조)
            self.warnings.append("자막 줄 분류 모델을 쓰지 못해 신뢰도 0.5 하한 규칙으로 대체했습니다.")
            classifier, role_rows = "rule_fallback", roles.classify(role_frames, None)
        _write_jsonl(target / "ocr" / "roles.jsonl", role_rows)
        role_of = {row["line_id"]: row for row in role_rows}
        meta = {f["frame_id"]: f for f in frames}
        lines = []
        for frame in role_frames:
            for line in frame["lines"]:
                row = role_of[line["line_id"]]
                lines.append({**line, "frame_id": frame["frame_id"], "time_ms": frame["time_ms"],
                              "sampling": meta[frame["frame_id"]]["sampling"], "role": row["role"],
                              "kept": row["kept"], "reason": row["reason"], "probs": row["probs"]})

        self.stage = "segmenting"
        self.update(target, stage=self.stage, progress=PROGRESS["segmenting"], stage_detail=f"줄 {len(lines)}개")
        tracks = build_tracks(frames, lines, duration)
        segments = build_segments(tracks, frames, duration, scenes.get("grid_ms", []))
        choose_representatives(segments, tracks, frames, target / "frames")
        groups = text_groups(tracks)
        _write_json(target / "tracks.json", {"tracks": tracks, "text_groups": groups})
        _write_json(target / "segments.json", segments)

        self.stage = "review"
        self.update(target, stage=self.stage, progress=PROGRESS["review"], stage_detail=f"구간 {len(segments)}개")
        review = judge(snapshot["criteria"], tracks, segments, frames)
        disclosure_result = judge_disclosure(disclosure, frames, lines, duration)
        _write_json(target / "review.json", {**review, "disclosure": disclosure_result})

        self.update(target, stage="double_check", progress=PROGRESS["double_check"])
        double_check = {"status": "skipped", "reason": "이중 검수(6-1)는 기본 꺼짐입니다."}

        public_frames = [{
            "frame_id": f["frame_id"], "time_ms": f["time_ms"], "sampling": f["sampling"], "width": f["width"],
            "height": f["height"], "ocr_status": f["ocr_status"], "same_as": f["same_as"],
            "lines": [{key: line[key] for key in ("line_id", "text", "confidence", "bbox_xyxy", "role", "kept", "reason")}
                      for line in lines if line["frame_id"] == f["frame_id"]]} for f in frames]
        kept = sum(1 for line in lines if line["kept"])
        statuses = [r["status"] for r in review["results"]]
        return {
            "classifier": classifier, "frames": public_frames, "coverage": cover,
            "scenes": {"status": scenes["status"], "cut_ms": scenes["cut_ms"]},
            "tracks": tracks, "text_groups": groups, "segments": segments,
            "review": {"results": review["results"], "mismatch": review["mismatch"], "calls": review["calls"]},
            "disclosure": disclosure_result, "double_check": double_check, "warnings": self.warnings,
            "counts": {"frames": len(frames), "ocr_frames": total, "lines": len(lines), "kept": kept,
                       "excluded": len(lines) - kept, "tracks": len(tracks), "segments": len(segments),
                       "results": {s: statuses.count(s) for s in ("pass", "fail", "review")},
                       "mismatch": len(review["mismatch"])},
        }


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def video_review_router(*, root: Path, inference_lock: threading.Lock, readonly: bool = False,
                        ocr: Callable[[list[Path], Path, Callable[[int], None]], dict] | None = None,
                        readiness: Callable[[], tuple[bool, str]] | None = None,
                        authenticate: Callable[[Request], object] | None = None) -> APIRouter:
    """ocr·readiness는 테스트에서 실제 모델 대신 주입한다. authenticate는 로컬 로그인 세션 확인 함수다."""

    def guard(request: Request) -> None:
        """로그인 경로(local_auth)와 같은 경계: localhost 접속만 받고, 로그인 기능이 켜져 있으면 세션도 확인한다.
        서버를 네트워크 주소에 바인딩해도 영상·프레임·결과가 인증 없이 열리지 않게 한다."""
        try:
            local = bool(request.client and ip_address(request.client.host).is_loopback)
        except ValueError:
            local = False
        if not local or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise HTTPException(404, "찾을 수 없습니다.")
        if authenticate is not None and authenticate(request) is None:
            raise HTTPException(401, {"code": "authentication_required", "message": "로그인이 필요합니다."})

    router = APIRouter(prefix="/api/video-reviews", dependencies=[Depends(guard)])
    recognize = ocr or paddle_frames
    check_ocr = readiness or ocr_readiness
    lock = threading.RLock()
    admission, reserved = threading.Lock(), [0]  # 동시 제출이 한도 검사를 함께 통과하지 않게 자리를 예약한다
    policy = limits()

    def directory(job_id: str) -> Path:
        try:
            if str(UUID(job_id)) != job_id:
                raise ValueError
        except ValueError:
            raise HTTPException(404, "동영상 검수 기록을 찾을 수 없습니다.") from None
        target = root / job_id
        if not (target / "result.json").is_file():
            raise HTTPException(404, "동영상 검수 기록을 찾을 수 없습니다.")
        return target

    def update(target: Path, **changes) -> None:
        with lock:
            result = _read_json(target / "result.json")
            result.update(changes, updated_at=_now())
            _write_json(target / "result.json", result)

    def status() -> dict:
        ffmpeg = resolve_ffmpeg()
        try:
            ocr_ready, ocr_detail = check_ocr()
        except Exception:
            ocr_ready, ocr_detail = False, "OCR 준비 상태를 확인하지 못했습니다."
        detail = ocr_detail if ffmpeg else "ffmpeg를 찾을 수 없습니다. `pip install imageio-ffmpeg` 하거나 FFMPEG_BIN을 지정하세요."
        if readonly:
            detail = "읽기 전용 모드라 새 동영상 검수를 받지 않습니다. 기존 결과만 볼 수 있습니다."
        return {"ready": bool(ffmpeg) and ocr_ready and not readonly, "ffmpeg": bool(ffmpeg), "ocr": ocr_ready, "detail": detail,
                "readonly": readonly, "pipeline": PIPELINE, "limits": policy,
                "sampling": "1초 간격 표본 검사 · 시작·끝 3초는 0.2초 간격"}

    def active_jobs() -> int:
        count = 0
        for path in root.glob("*/result.json"):
            try:
                count += _read_json(path).get("status") in {"queued", "running"}
            except (OSError, ValueError):
                continue
        return count

    def reserve() -> bool:
        with admission:
            if active_jobs() + reserved[0] >= policy["active_jobs"]:
                return False
            reserved[0] += 1
            return True

    def release() -> None:
        with admission:
            reserved[0] -= 1

    def run(target: Path, demuxer: str) -> None:
        pipeline = Pipeline(target, recognize, update)
        pipeline.stage = "extracting"
        try:
            with inference_lock:  # 시연 환경: 포스터 검수와 같은 잠금으로 CPU 추론을 하나씩
                ffmpeg = resolve_ffmpeg()
                if not ffmpeg:
                    raise RuntimeError("ffmpeg unavailable")
                result = pipeline.run(ffmpeg, demuxer)
            update(target, status="completed", stage="completed", progress=100, error=None, stage_detail=None, **result)
        except Exception:
            # 문서: failed_stage는 extracting·ocr·segmenting·review 중 하나
            stage = {"scene_detection": "extracting", "classifying": "segmenting"}.get(pipeline.stage, pipeline.stage)
            stage = stage if stage in FAILURE_MESSAGES else "review"
            update(target, status="failed", stage="failed", failed_stage=stage, error=FAILURE_MESSAGES[stage],
                   warnings=pipeline.warnings)

    @router.get("/status")
    def get_status():
        return status()

    @router.get("")
    def listing():
        jobs = []
        for path in root.glob("*/result.json"):
            try:
                value = _read_json(path)
            except (OSError, ValueError):
                continue
            jobs.append({key: value.get(key) for key in ("id", "status", "stage", "progress", "created_at", "media",
                                                         "counts", "project_name")})
        jobs.sort(key=lambda job: job.get("created_at") or "", reverse=True)
        return {"items": jobs[:20]}

    if not readonly:
        @router.post("", status_code=202)
        async def submit(request: Request, background_tasks: BackgroundTasks, video: UploadFile = File(...),
                         criteria: str = Form("[]"), criteria_version: int = Form(1, ge=1, le=1_000_000),
                         project_name: str = Form("동영상 검수", min_length=1, max_length=120),
                         disclosure: str = Form("{}")):
            try:
                if "content-length" not in request.headers:
                    raise HTTPException(411, "Content-Length가 필요합니다.")
                from .api import CRITERIA_MAX_JSON, Criterion  # 포스터와 같은 기준 형식
                try:
                    parsed = json.loads(criteria) if len(criteria) <= CRITERIA_MAX_JSON else None
                    if not isinstance(parsed, list) or len(parsed) > 100:
                        raise ValueError
                    frozen = [Criterion.model_validate(item).model_dump() for item in parsed]
                    if len({item["id"] for item in frozen}) != len(frozen):
                        raise ValueError
                    ad = validate_input(json.loads(disclosure))
                except (ValueError, TypeError):
                    raise HTTPException(422, "검수 기준 또는 광고 입력 형식을 확인해 주세요.") from None
                ready = await run_in_threadpool(status)
                if not ready["ready"]:
                    raise HTTPException(503, ready["detail"])
                if not await run_in_threadpool(reserve):
                    raise HTTPException(429, f"동영상 검수는 실행·대기 합쳐 {policy['active_jobs']}건까지 받습니다. 잠시 후 다시 시도해 주세요.")
            except BaseException:
                await video.close()
                raise
            try:  # 예약한 자리는 queued 결과를 쓴 뒤(그때부터 active_jobs가 센다) 또는 거부 뒤 돌려준다
                return await accept(background_tasks, video, frozen, criteria_version, project_name, ad)
            finally:
                await run_in_threadpool(release)

        async def accept(background_tasks, video, frozen, criteria_version, project_name, ad):
            job_id = str(uuid4())
            target = root / job_id
            target.mkdir(parents=True)
            source = target / "original.bin"  # 사용자 파일명은 경로나 응답에 쓰지 않는다
            digest, size, head = hashlib.sha256(), 0, b""
            try:
                with source.open("wb") as output:  # 1MiB씩 디스크에 흘려 쓰면서 SHA-256 계산
                    while chunk := await video.read(CHUNK):
                        size += len(chunk)
                        if size > policy["upload_bytes"]:
                            raise HTTPException(413, f"동영상은 {policy['upload_bytes'] // (1024 * 1024)}MB 이하로 업로드해 주세요.")
                        if len(head) < 16:
                            head += chunk[:16 - len(head)]
                        digest.update(chunk)
                        output.write(chunk)
                if not size:
                    raise HTTPException(400, "빈 동영상 파일입니다.")
                demuxer = sniff_container(head)
                if demuxer is None:
                    raise HTTPException(415, "MP4·MOV·WEBM·AVI 동영상만 지원합니다.")
                try:
                    info = await run_in_threadpool(probe, resolve_ffmpeg(), demuxer, source)
                except (ValueError, OSError, IndexError, subprocess.SubprocessError):
                    raise HTTPException(415, "영상을 디코딩하지 못했습니다. 손상되지 않은 동영상인지 확인해 주세요.") from None
                short, long = sorted((info["width"], info["height"]))
                if not 0 < info["duration_ms"] <= policy["duration_seconds"] * 1000:
                    raise HTTPException(422, f"동영상 길이는 {policy['duration_seconds']}초 이하여야 합니다.")
                if short < policy["short_side_min"] or long > policy["long_side_max"]:
                    raise HTTPException(422, "해상도는 짧은 변 360픽셀 이상, 긴 변 3840픽셀 이하여야 합니다.")
                if info["fps"] and info["fps"] > policy["fps_max"]:
                    raise HTTPException(422, "120fps 이하 동영상만 지원합니다.")
            except BaseException:  # 어떤 거부 뒤에도 저장 폴더에 남는 파일이 0개
                shutil.rmtree(target, ignore_errors=True)
                raise
            finally:
                await video.close()
            created = _now()
            media = {**info, "bytes": size, "sha256": digest.hexdigest()}
            _write_json(target / "media.json", media)
            _write_json(target / "snapshot.json", {"project_name": project_name, "criteria_version": criteria_version,
                                                   "criteria": frozen, "disclosure": ad, "created_at": created})
            warnings = ["HDR 영상입니다. 톤매핑 없이 처리해 색이 다르게 보일 수 있습니다."] if info["hdr"] else []
            _write_json(target / "result.json", {
                "id": job_id, "status": "queued", "stage": "queued", "progress": PROGRESS["queued"],
                "stage_detail": f"대기 {await run_in_threadpool(active_jobs)}건", "project_name": project_name,
                "criteria_version": criteria_version, "criteria": frozen, "disclosure_input": ad, "media": media,
                "runtime": None, "warnings": warnings, "error": None, "created_at": created, "updated_at": created,
            })
            background_tasks.add_task(run, target, demuxer)
            return {"id": job_id, "status": "queued"}

    @router.get("/{job_id}")
    def get_job(job_id: str):
        with lock:
            return _read_json(directory(job_id) / "result.json")

    @router.get("/{job_id}/video")
    def video_file(job_id: str):
        """구간 이동을 위해 Range 요청에 206으로 답한다."""
        target = directory(job_id)
        media = _read_json(target / "media.json")
        return FileResponse(target / "original.bin", media_type=CONTAINER_MIME.get(media["container"], "application/octet-stream"))

    @router.get("/{job_id}/frames/{frame_id}")
    def frame(job_id: str, frame_id: str):
        path = directory(job_id) / "frames" / f"{frame_id}.jpg"
        if not FRAME_ID.match(frame_id) or not path.is_file():
            raise HTTPException(404, "프레임을 찾을 수 없습니다.")
        return FileResponse(path, media_type="image/jpeg")

    return router
