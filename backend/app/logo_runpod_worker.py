"""로고 guided 검수 RunPod Serverless handler. 컨테이너 GPU로 돌고, 다른 엔드포인트를 부르지 않는다.

입력(job["input"]):
  query_logo_base64 : 검사할 지정 로고 1장(필수)
  frames_base64     : 검수할 프레임 이미지 목록(1~MAX_FRAMES). 서버가 ffmpeg로 뽑아 보낸다.
  overrides         : {cosine_certain, cosine_present, lightglue_min_matches, det_threshold,
                       max_boxes, verify_budget} 중 일부(선택, 범위 밖이면 무시).
출력:
  {status, engine, matcher, verify, config, frames:[review_image 결과 ...]}

모델(OWLv2 + CLIP/SigLIP2)은 이미지에 미리 받아 둔다(scripts/prepare_logo_models.py --combo A).
LightGlue는 kornia가 기동(warmup) 때 받는다. 매처·검증 여부는 환경변수로 고정한다:
  LOGO_MATCHER=clip   LOGO_VERIFY=1
"""
import base64
import binascii
import io
import os

from .logo_review import ReviewConfig, build_reviewer

MAX_REMOTE_IMAGE_BYTES = 7 * 1024 * 1024   # OCR 경로와 같은 1장 상한
MAX_FRAMES = 32                            # 요청당 프레임 수 상한(페이로드·시간 보호)
_B64_MAX = ((MAX_REMOTE_IMAGE_BYTES + 2) // 3) * 4

_REVIEWER = None  # 모델은 프로세스에 1회만 올려 재사용한다


def _matcher_key() -> str:
    key = os.getenv("LOGO_MATCHER", "clip").strip().lower()
    return key if key in {"clip", "siglip2"} else "clip"


def _verify() -> bool:
    return os.getenv("LOGO_VERIFY", "1").strip().lower() not in {"0", "false", "no", ""}


def warmup():
    """모델을 올려 둔다. 서버리스 콜드스타트에서 1회 호출한다(다운로드는 하지 않는 것이 기본)."""
    global _REVIEWER
    if _REVIEWER is None:
        _REVIEWER = build_reviewer(_matcher_key(), verify=_verify())
    return _REVIEWER


def _decode_image(encoded, label):
    if not isinstance(encoded, str) or not 0 < len(encoded) <= _B64_MAX:
        raise ValueError(f"{label} must be a base64 image of at most 7 MiB")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(f"{label} is invalid") from None
    if not data or len(data) > MAX_REMOTE_IMAGE_BYTES:
        raise ValueError(f"{label} is empty or too large")
    from PIL import Image
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        raise ValueError(f"{label} is not a readable image") from None
    return image


def handler(job):
    payload = job.get("input", {}) if isinstance(job, dict) else {}
    if not isinstance(payload, dict):
        raise ValueError("input must be a JSON object")
    frames_b64 = payload.get("frames_base64")
    if not isinstance(frames_b64, list) or not 1 <= len(frames_b64) <= MAX_FRAMES:
        raise ValueError(f"input.frames_base64 must be a list of 1..{MAX_FRAMES} images")
    query = _decode_image(payload.get("query_logo_base64"), "input.query_logo_base64")
    frames = [_decode_image(item, f"input.frames_base64[{i}]") for i, item in enumerate(frames_b64)]

    overrides = payload.get("overrides")
    config = ReviewConfig.from_overrides(overrides if isinstance(overrides, dict) else None)

    reviewer = warmup()
    reviewer.config = config
    try:
        reviewer.set_query(query)
        results = [reviewer.review_image(frame) for frame in frames]
    except Exception:
        # API 응답에 서버 설정·경로를 노출하지 않는다.
        raise RuntimeError("Logo review inference failed; verify model cache and worker device") from None
    return {"status": "success", "engine": "logo-guided", "matcher": _matcher_key(),
            "verify": _verify(), "config": config.as_dict(), "frames": results}


if __name__ == "__main__":
    import runpod
    os.environ["RUN_TARGET"] = "local"  # Pod 안에서는 모델을 직접 올린다(원격 재호출 금지)
    warmup()
    runpod.serverless.start({"handler": handler})
