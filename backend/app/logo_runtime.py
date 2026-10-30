"""로고 guided 검수 런타임 — 로컬(Pod 안) 추론 ↔ RunPod 원격 호출의 경계.

OCR(ocr_runtime.py)과 같은 원칙:
  - RUN_TARGET=local  : 이 프로세스에서 모델을 올려 직접 판정한다(Pod 안, 또는 로컬 점검).
  - RUN_TARGET=runpod : 프레임·쿼리 로고만 LOGO_RUNPOD_ENDPOINT_ID 엔드포인트로 보낸다(서버가 호출).
서버는 영상에서 프레임만 뽑아(ffmpeg) 이 모듈을 부른다. 무거운 모델(OWLv2·CLIP·LightGlue)은
RunPod Pod에만 올린다(상현님 로컬 PC엔 GPU 없음). HTTP/폴링 하부는 OCR 경로와 공유한다.

원격 상태 확인(health)은 모델을 받거나 올리지 않는다.
"""
from __future__ import annotations

import base64
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

from . import logo_models as reg
from .logo_review import ReviewConfig, VERDICTS, build_reviewer, sample_frames, summarize
# HTTP·env 파싱 하부는 OCR 경로와 단일 소스로 공유한다(엔드포인트만 로고 전용).
from .ocr_runtime import OCRRuntimeError, _RemoteHTTPError, _http_json, _seconds

MAX_REMOTE_IMAGE_BYTES = 7 * 1024 * 1024
MAX_FRAMES_PER_REQUEST = 32  # 워커(logo_runpod_worker.MAX_FRAMES)와 맞춘다
_LOCAL_REVIEWERS: dict = {}


class LogoRuntimeError(RuntimeError):
    pass


def _target() -> str:
    value = os.getenv("RUN_TARGET", "local").strip().lower()
    if value not in {"local", "runpod"}:
        raise LogoRuntimeError("RUN_TARGET must be local or runpod")
    return value


def _remote_config() -> tuple[str, str]:
    key = os.getenv("RUNPOD_API_KEY", "").strip()
    endpoint = os.getenv("LOGO_RUNPOD_ENDPOINT_ID", "").strip()
    if not key or not endpoint:
        raise LogoRuntimeError("Set server-side RUNPOD_API_KEY and LOGO_RUNPOD_ENDPOINT_ID")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", endpoint):
        raise LogoRuntimeError("LOGO_RUNPOD_ENDPOINT_ID has an invalid format")
    return key, endpoint


def get_runtime_status(matcher: str = "clip") -> dict:
    """구성 준비 상태만 돌려준다(추론·원격요청·다운로드 없음)."""
    status = {"engine": "logo-guided", "execution_target": "local", "device": "unknown", "ready": False}
    try:
        status["execution_target"] = _target()
        if status["execution_target"] == "runpod":
            status["device"] = "remote-auto"
            _remote_config()
            status.update(ready=True, detail="RunPod 로고 엔드포인트 구성됨; 워커 가용성은 제출 때 확인됩니다.")
            return status
        import importlib.util
        missing = [name for name in ("torch", "transformers", "kornia", "numpy", "PIL")
                   if importlib.util.find_spec(name) is None]
        if missing:
            status["detail"] = "런타임 의존성 설치 필요: " + ", ".join(missing)
            return status
        not_ready = [key for key in ("owlv2", matcher) if not reg.is_prepared(key)]
        if not_ready:
            status["detail"] = ("모델 준비 필요: python scripts/prepare_logo_models.py --models "
                                + ",".join(not_ready))
            return status
        status.update(ready=True, detail="로컬 모델 캐시 있음(LightGlue는 최초 로드 시 kornia가 받음).")
    except Exception as exc:
        status["detail"] = str(exc) if isinstance(exc, LogoRuntimeError) else f"런타임 확인 불가 ({type(exc).__name__})"
    return status


# ---------------------------------------------------------------------------
# 로컬(Pod 안) 추론
# ---------------------------------------------------------------------------
def _local_reviewer(matcher: str, verify: bool):
    key = (matcher, bool(verify))
    if key not in _LOCAL_REVIEWERS:  # 모델은 조합별로 1회만 올린다
        _LOCAL_REVIEWERS[key] = build_reviewer(matcher, verify=verify)
    return _LOCAL_REVIEWERS[key]


def _review_local(frame_paths, query_logo, *, matcher, verify, overrides) -> list[dict]:
    reviewer = _local_reviewer(matcher, verify)
    reviewer.config = ReviewConfig.from_overrides(overrides)
    reviewer.set_query(query_logo)
    return [reviewer.review_image(path) for path in frame_paths]


# ---------------------------------------------------------------------------
# 원격(RunPod) 추론
# ---------------------------------------------------------------------------
def _encode_image(path: Path) -> str:
    data = Path(path).read_bytes()
    if not 0 < len(data) <= MAX_REMOTE_IMAGE_BYTES:
        raise LogoRuntimeError("원격 전송 이미지는 1장당 7 MiB 이하여야 합니다.")
    return base64.b64encode(data).decode("ascii")


def _validate_frames(output, expected: int) -> list[dict]:
    if not isinstance(output, dict) or output.get("status") != "success" or output.get("engine") != "logo-guided":
        raise LogoRuntimeError("RunPod 로고 워커가 올바르지 않은 결과를 돌려줬습니다.")
    frames = output.get("frames")
    if not isinstance(frames, list) or len(frames) != expected:
        raise LogoRuntimeError("RunPod 로고 워커의 프레임 수가 요청과 다릅니다.")
    for frame in frames:
        if not isinstance(frame, dict) or frame.get("verdict") not in VERDICTS:
            raise LogoRuntimeError("RunPod 로고 워커가 잘못된 판정을 돌려줬습니다.")
    return frames


def _run_remote(base: str, key: str, body: dict):
    timeout = _seconds("RUNPOD_TIMEOUT_SECONDS", 300, 1, 1800)
    poll = _seconds("RUNPOD_POLL_SECONDS", 2, 0.01, 30)
    deadline = time.monotonic() + timeout
    response = _http_json("POST", f"{base}/run", key, min(30, timeout), body)
    job_id = response.get("id")
    if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
        raise LogoRuntimeError("RunPod가 유효한 job ID를 돌려주지 않았습니다(제출은 재시도하지 않습니다).")
    failures = 0
    try:
        while True:
            state = response.get("status")
            if state == "COMPLETED":
                return response.get("output"), job_id
            if state in {"FAILED", "CANCELLED", "TIMED_OUT"}:
                raise LogoRuntimeError(f"RunPod 작업 {str(state).lower()}; 워커 로그를 확인하세요.")
            if state not in {"IN_QUEUE", "IN_PROGRESS"}:
                raise LogoRuntimeError("RunPod가 알 수 없는 작업 상태를 돌려줬습니다.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LogoRuntimeError("RunPod 작업이 제한 시간을 넘겼습니다.")
            time.sleep(min(poll * (2 ** failures), remaining))
            if deadline - time.monotonic() <= 0:
                raise LogoRuntimeError("RunPod 작업이 제한 시간을 넘겼습니다.")
            try:  # 멱등한 상태 조회만 재시도한다(최대 3회)
                response = _http_json("GET", f"{base}/status/{job_id}", key, min(30, deadline - time.monotonic()))
                failures = 0
            except OCRRuntimeError as exc:
                if isinstance(exc, _RemoteHTTPError) and exc.status not in {429, 500, 502, 503, 504}:
                    raise
                failures += 1
                if failures > 3:
                    raise
    except Exception:
        try:
            _http_json("POST", f"{base}/cancel/{job_id}", key, 5, {})
        except Exception:
            pass  # 로컬 실패가 원격 취소를 보장하지는 않는다(최선 노력).
        raise


def _review_runpod(frame_paths, query_logo, *, overrides) -> list[dict]:
    """프레임을 요청 단위로 쪼개 보낸다. matcher·verify는 엔드포인트 배포(LOGO_MATCHER/LOGO_VERIFY)가 정한다."""
    key, endpoint = _remote_config()
    base = f"https://api.runpod.ai/v2/{endpoint}"
    timeout = _seconds("RUNPOD_TIMEOUT_SECONDS", 300, 1, 1800)
    query_b64 = _encode_image(query_logo)
    results: list[dict] = []
    for start in range(0, len(frame_paths), MAX_FRAMES_PER_REQUEST):
        chunk = frame_paths[start:start + MAX_FRAMES_PER_REQUEST]
        body = {"input": {"query_logo_base64": query_b64,
                          "frames_base64": [_encode_image(path) for path in chunk],
                          "overrides": overrides or {}},
                "policy": {"executionTimeout": max(5000, int(timeout * 1000)),
                           "ttl": max(10000, int(timeout * 1000))}}
        output, _ = _run_remote(base, key, body)
        results.extend(_validate_frames(output, len(chunk)))
    return results


# ---------------------------------------------------------------------------
# 공개 API — 서버/CLI가 부르는 진입점
# ---------------------------------------------------------------------------
def review_frames(frame_paths, query_logo_path, *, matcher: str = "clip", verify: bool = True,
                  overrides: dict | None = None) -> list[dict]:
    """프레임 경로 목록을 지정 로고와 대조해 프레임별 판정을 돌려준다(로컬 ↔ RunPod 자동 분기)."""
    paths = [Path(p) for p in frame_paths]
    if not paths:
        return []
    if _target() == "runpod":
        return _review_runpod(paths, Path(query_logo_path), overrides=overrides)
    return _review_local(paths, Path(query_logo_path), matcher=matcher, verify=verify, overrides=overrides)


def review_video(video_path, query_logo_path, *, fps: float = 1.0, matcher: str = "clip",
                 verify: bool = True, overrides: dict | None = None) -> dict:
    """영상 1편 guided 검수: ffmpeg 프레임 추출 → 프레임별 판정 → 영상 요약.

    원격 모드에서도 프레임 추출은 호출자(서버)가 로컬 ffmpeg로 하고, 추론만 RunPod로 보낸다.
    """
    if not math.isfinite(fps) or not 0 < fps <= 30:
        raise LogoRuntimeError("fps must be between 0 and 30")
    video, query = Path(video_path), Path(query_logo_path)
    if not video.is_file():
        raise LogoRuntimeError(f"동영상을 찾을 수 없습니다: {video}")
    if not query.is_file():
        raise LogoRuntimeError(f"쿼리 로고를 찾을 수 없습니다: {query}")
    work = Path(tempfile.mkdtemp(prefix="logo-video-"))
    try:
        frames = sample_frames(video, work / "frames", fps)
        if not frames:
            raise LogoRuntimeError("프레임을 추출하지 못했습니다. 동영상을 확인하세요.")
        reviews = review_frames([path for _, path in frames], query,
                                matcher=matcher, verify=verify, overrides=overrides)
        per_frame = [{**review, "time_s": time_s} for (time_s, _), review in zip(frames, reviews)]
        return {"video": str(video), "query_logo": str(query), "fps": fps, "frames": len(per_frame),
                "execution_target": _target(), "summary": summarize(per_frame), "per_frame": per_frame}
    finally:
        shutil.rmtree(work, ignore_errors=True)
