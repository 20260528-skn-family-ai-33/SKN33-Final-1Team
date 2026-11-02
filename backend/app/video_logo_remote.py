"""영상 로고 검수의 RunPod Pod 워커(video_logo_worker) 호출. 로컬 PC에는 GPU가 없어 모델은 Pod에서만 돈다.

VIDEO_LOGO_WORKER_URL 이 있으면 원격, 없으면 이 프로세스에서 직접 추론한다(Pod 안에서 백엔드를 띄운 경우).
보통은 SSH 터널로 Pod의 127.0.0.1:8791 을 로컬 같은 포트에 연결하고 http://127.0.0.1:8791 을 넣는다:
  ssh -i ~/.ssh/skn33_final -p <포트> -N -L 8791:127.0.0.1:8791 root@<Pod 주소>
VIDEO_LOGO_WORKER_TOKEN 은 워커와 같은 값을 둔다(선택). 둘 다 서버 환경변수이고 화면에는 내보내지 않는다.
"""
from __future__ import annotations

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

from PIL import Image

POLL_SECONDS = 2
MAX_RESPONSE_BYTES = 256 * 1024 * 1024


class WorkerError(RuntimeError):
    pass


def worker_url() -> str | None:
    value = os.getenv("VIDEO_LOGO_WORKER_URL", "").strip().rstrip("/")
    if not value:
        return None
    if not re.fullmatch(r"https?://[A-Za-z0-9.\-\[\]:]+(/[A-Za-z0-9._\-/]*)?", value):
        raise WorkerError("VIDEO_LOGO_WORKER_URL 형식이 올바르지 않습니다(예: http://127.0.0.1:8791).")
    return value


def _timeout() -> float:
    try:
        value = float(os.getenv("VIDEO_LOGO_WORKER_TIMEOUT_SECONDS", "7200"))
    except ValueError:
        raise WorkerError("VIDEO_LOGO_WORKER_TIMEOUT_SECONDS는 숫자여야 합니다.") from None
    return min(max(value, 30), 6 * 3600)


def _request(method: str, path: str, payload: dict | None = None, timeout: float = 30) -> dict:
    headers = {"Content-Type": "application/json"}
    if os.getenv("VIDEO_LOGO_WORKER_TOKEN"):
        headers["X-Worker-Token"] = os.environ["VIDEO_LOGO_WORKER_TOKEN"]
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(f"{worker_url()}{path}", method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read(64 * 1024)).get("detail") or ""
        except (ValueError, AttributeError, OSError):
            pass
        raise WorkerError(f"RunPod 워커 응답 오류 {exc.code}{f': {detail}' if isinstance(detail, str) and detail else ''}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise WorkerError("RunPod 워커에 연결하지 못했습니다. Pod 워커 실행과 SSH 터널을 확인하세요.") from None
    if len(content) > MAX_RESPONSE_BYTES:
        raise WorkerError("RunPod 워커 응답이 너무 큽니다.")
    try:
        body = json.loads(content)
    except ValueError:
        raise WorkerError("RunPod 워커가 올바르지 않은 응답을 돌려줬습니다.") from None
    if not isinstance(body, dict):
        raise WorkerError("RunPod 워커가 올바르지 않은 응답을 돌려줬습니다.")
    return body


def health() -> tuple[bool, str]:
    """워커 준비 상태(모델은 올리지 않는다)."""
    try:
        body = _request("GET", "/health", timeout=10)
    except WorkerError as exc:
        return False, str(exc)
    detail = body.get("detail") or ""
    return bool(body.get("ready")), f"RunPod 워커: {detail}" + (f" · 대기·실행 중 작업 {body['busy']}개" if body.get("busy") else "")


def _encode(image: Image.Image, fmt: str) -> str:
    buffer = BytesIO()
    image.save(buffer, format=fmt, **({"quality": 95} if fmt == "JPEG" else {}))
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _run(payload: dict, progress) -> object:
    """작업을 한 번 제출하고(재제출하지 않는다) 끝날 때까지 진행률을 읽는다."""
    job_id = _request("POST", "/jobs", payload, timeout=600).get("id")
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise WorkerError("RunPod 워커가 작업 번호를 돌려주지 않았습니다.")
    deadline, failures = time.monotonic() + _timeout(), 0
    while True:
        if time.monotonic() > deadline:
            raise WorkerError("RunPod 워커 작업이 제한 시간을 넘겼습니다.")
        time.sleep(POLL_SECONDS)
        try:
            state = _request("GET", f"/jobs/{job_id}", timeout=60); failures = 0
        except WorkerError:
            failures += 1  # 상태 조회만 다시 시도한다(터널이 잠깐 끊긴 경우)
            if failures > 5:
                raise
            continue
        if state.get("status") == "completed":
            return state.get("result")
        if state.get("status") == "failed":
            raise WorkerError(f"RunPod 워커 작업 실패: {state.get('error') or '원인 미상'}")
        progress(state.get("progress") or 0, state.get("detail"))


def build_candidates(frames_dir: Path, frames: list[dict], refs: list[dict], progress=lambda p, d: None) -> dict:
    """video_logo_candidates.build_candidates와 같은 입출력. 프레임은 원본 해상도(full/<sec>.jpg)를 그대로 보낸다."""
    progress(2, "RunPod 워커로 프레임 보내는 중")
    payload = {"kind": "candidates",
               "frames": [{"sec": f["sec"], "jpg_base64": base64.b64encode((frames_dir / "full" / f"{f['sec']}.jpg").read_bytes()).decode("ascii")}
                          for f in frames],
               "refs": [{"name": r["name"], "png_base64": _encode(r["image"], "PNG"), "text": r["text"]} for r in refs]}
    data = _run(payload, lambda p, d: progress(5 + 0.9 * p, f"RunPod · {d}" if d else "RunPod에서 후보 찾는 중"))
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list) or not isinstance(data.get("refs"), list):
        raise WorkerError("RunPod 워커의 후보 결과 형식이 올바르지 않습니다.")
    return data


def compare_crops(ref_image: Image.Image, ref_text: str, crops: list[Image.Image], progress=lambda done: None) -> list[dict]:
    """원본 로고 1장 대 crop 여러 장. 결과는 video_logo_compare.compare 반환값의 목록(crops와 같은 순서)."""
    if not crops:
        return []
    payload = {"kind": "compare", "ref": {"png_base64": _encode(ref_image, "PNG"), "text": ref_text},
               "crops": [_encode(c, "JPEG") for c in crops]}
    results = _run(payload, lambda p, d: progress(round(len(crops) * p / 100)))
    if not isinstance(results, list) or len(results) != len(crops) or not all(isinstance(r, dict) and r.get("outcome") in {"match", "diff", "unknown"} for r in results):
        raise WorkerError("RunPod 워커의 대조 결과 형식이 올바르지 않습니다.")
    return results


def ocr_frames(frame_paths: list[Path], progress=lambda done: None) -> list[dict]:
    """동영상 검수(자막) 프레임을 Pod 워커의 GPU Paddle로 읽는다. 반환은 paddle_video_worker 출력 줄(헤더 + 프레임별)."""
    payload = {"kind": "ocr_frames", "frames": [base64.b64encode(Path(p).read_bytes()).decode("ascii") for p in frame_paths]}
    result = _run(payload, lambda p, d: progress(round(len(frame_paths) * p / 100)))
    rows = result.get("rows") if isinstance(result, dict) else None
    if not isinstance(rows, list) or len(rows) != len(frame_paths) + 1 or not all(isinstance(r, dict) for r in rows):
        raise WorkerError("RunPod 워커의 자막 OCR 결과 형식이 올바르지 않습니다.")
    return rows
