"""포스터 로고 검수의 RunPod Pod 워커 호출. 영상 로고와 같은 워커(backend/app/video/logos/worker.py)를 쓴다.

모델(OWLv2·SigLIP2·BiRefNet·OCR)이 같으므로 워커를 따로 띄우지 않는다. 연결 설정도 영상 로고와 같다:
  VIDEO_LOGO_WORKER_URL(없으면 이 프로세스에서 직접 추론), VIDEO_LOGO_WORKER_TOKEN, VIDEO_LOGO_WORKER_TIMEOUT_SECONDS
  - 후보 찾기: 워커 작업 kind="poster_candidates"(포스터 1장 + 원본 로고)
  - 정밀 대조: 영상과 같은 kind="compare"(원본 로고 1장 + crop 여러 장)를 그대로 쓴다
워커가 poster_candidates를 모르는 이전 코드면 422로 거절하고, 그 문구를 사용자에게 그대로 보여 준다(Pod 코드 갱신 필요).
"""
from __future__ import annotations

from PIL import Image

from backend.app.video.logos.client import WorkerError
from backend.app.video.logos.client import _encode
from backend.app.video.logos.client import _run
from backend.app.video.logos.client import compare_crops  # noqa: F401 - 정밀 대조는 영상과 같은 워커 작업을 쓴다
from backend.app.video.logos.client import health  # noqa: F401
from backend.app.video.logos.client import worker_url  # noqa: F401


def build_candidates(poster: Image.Image, refs: list[dict], progress=lambda p, d: None) -> dict:
    """poster.candidates.build_candidates와 같은 입출력. 포스터는 원본 해상도 JPEG(품질 95)로 보낸다."""
    progress(2, "RunPod 워커로 포스터 보내는 중")
    payload = {"kind": "poster_candidates", "poster": {"jpg_base64": _encode(poster.convert("RGB"), "JPEG")},
               "refs": [{"name": r["name"], "png_base64": _encode(r["image"], "PNG"), "text": r["text"]} for r in refs]}
    data = _run(payload, lambda p, d: progress(5 + 0.9 * p, f"RunPod · {d}" if d else "RunPod에서 후보 찾는 중"))
    if (not isinstance(data, dict) or not isinstance(data.get("candidates"), list) or not isinstance(data.get("refs"), list)
            or len(data["refs"]) != len(refs) or data.get("size") != list(poster.size)):
        raise WorkerError("RunPod 워커의 포스터 후보 결과 형식이 올바르지 않습니다.")
    return data
