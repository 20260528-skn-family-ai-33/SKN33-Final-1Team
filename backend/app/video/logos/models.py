"""영상 로고 확정·정밀 대조 흐름이 쓰는 모델을 프로세스에 한 번만 올려 재사용한다.

모델은 RunPod GPU Pod에서만 돈다(상현님 로컬 PC엔 GPU 없음). 실험(2026-10-31~11-01)과 같은 모델·설정을 쓴다.
  - OWLv2 base patch16      : 로고 후보 상자 탐지(텍스트 프롬프트 6개, 전체 화면 + 타일 10개)
  - SigLIP2 so400m 384      : 후보와 원본 로고의 유사도, 같은 로고 묶기
  - 자막 검사 OCR 모델(PP-OCRv5 server det + korean mobile rec, paddle_video_worker.build_model과 같은 설정)
                            : 후보 OCR 가산점, 요소 단위 문자 비교. 모델 파일은 자막 검사와 같은 .runtime/paddle-models(해시 확인)
                              VIDEO_OCR_PYTHON(Pod의 GPU용 Paddle 환경)이 있으면 그 환경의 상주 프로세스(paddle_ocr_server)에서,
                              없으면 이 프로세스 안에서 CPU로 읽는다.
  - BiRefNet               : 배경 분리(M3c = 지금 방식 ∩ BiRefNet 범위)
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading

import numpy as np

OWL_MODEL = "google/owlv2-base-patch16"
SIGLIP_MODEL = "google/siglip2-so400m-patch14-384"
BIREFNET_MODEL = "ZhengPeng7/BiRefNet"

_lock = threading.Lock()
_cache: dict = {}


def _device() -> str:
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def owl():
    """(processor, model) — OWLv2 텍스트 탐지."""
    with _lock:
        if "owl" not in _cache:
            from transformers import AutoProcessor, Owlv2ForObjectDetection
            _cache["owl"] = (AutoProcessor.from_pretrained(OWL_MODEL), Owlv2ForObjectDetection.from_pretrained(OWL_MODEL).to(_device()).eval())
        return _cache["owl"]


def siglip():
    """(processor, model) — SigLIP2 이미지 임베딩(GPU면 fp16)."""
    with _lock:
        if "siglip" not in _cache:
            import torch
            from transformers import AutoModel, AutoProcessor
            dtype = torch.float16 if _device() == "cuda" else torch.float32
            _cache["siglip"] = (AutoProcessor.from_pretrained(SIGLIP_MODEL), AutoModel.from_pretrained(SIGLIP_MODEL, torch_dtype=dtype).to(_device()).eval())
        return _cache["siglip"]


def birefnet():
    """(transform, model) — BiRefNet 배경 분리. 원격 코드(trust_remote_code)로 올린다."""
    with _lock:
        if "birefnet" not in _cache:
            import torch
            from torchvision import transforms
            from transformers import AutoModelForImageSegmentation
            model = AutoModelForImageSegmentation.from_pretrained(BIREFNET_MODEL, trust_remote_code=True).to(_device()).eval()
            if _device() == "cuda":
                model = model.half()
            tf = transforms.Compose([transforms.Resize((1024, 1024)), transforms.ToTensor(),
                                     transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
            _cache["birefnet"] = (tf, model, torch.float16 if _device() == "cuda" else torch.float32)
        return _cache["birefnet"]


def ocr():
    """자막 검사와 같은 OCR 모델 파일(무결성 확인)·설정으로 프로세스 안에 한 번 올린다."""
    with _lock:
        if "ocr" not in _cache:
            from scripts.prepare_paddle import DETECTION_MODEL, MODEL, verify_model
            from backend.app.ocr.paddle import paddle_paths
            from backend.app.ocr.paddle import worker_environment
            from backend.app.ocr.paddle_video_worker import build_model
            _, detection, recognition = paddle_paths()
            verify_model(detection, DETECTION_MODEL); verify_model(recognition, MODEL)
            for key in ("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "PADDLE_PDX_CACHE_HOME"):  # 모델 자동 조회 끔, 캐시는 프로젝트 안
                os.environ.setdefault(key, worker_environment()[key])
            _cache["ocr"] = build_model(detection, recognition)
        return _cache["ocr"]


_server_lock = threading.Lock()


def _ocr_server() -> subprocess.Popen:
    """VIDEO_OCR_PYTHON 환경에 상주 OCR 프로세스를 한 번 띄운다(죽었으면 다시 띄운다). _server_lock 안에서만 부른다."""
    proc = _cache.get("ocr_server")
    if proc is not None and proc.poll() is None:
        return proc
    from backend.app.ocr.runtime import PROJECT_ROOT
    from backend.app.ocr.paddle import paddle_paths
    from backend.app.ocr.paddle import worker_environment
    _, detection, recognition = paddle_paths()
    log = tempfile.TemporaryFile()  # Paddle 로그는 시스템 임시 파일로(프로젝트에 남기지 않는다)
    proc = subprocess.Popen([os.environ["VIDEO_OCR_PYTHON"], "-m", "backend.app.paddle_ocr_server", "--detection-dir", str(detection),
                             "--recognition-dir", str(recognition), "--device", os.getenv("VIDEO_OCR_DEVICE", "gpu")],
                            cwd=PROJECT_ROOT, env=worker_environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
    if not json.loads(proc.stdout.readline() or "{}").get("ready"):
        proc.kill(); proc.wait(); log.seek(0)
        tail = log.read().decode("utf-8", "replace").strip().splitlines()[-1:] or ["원인 미상"]
        raise RuntimeError(f"OCR 프로세스를 띄우지 못했습니다: {tail[0][:300]}")
    _cache["ocr_server"] = proc
    return proc


def _ocr_remote(rgb: np.ndarray) -> dict:
    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    with _server_lock:
        proc = _ocr_server()
        proc.stdin.write(json.dumps({"h": rgb.shape[0], "w": rgb.shape[1]}).encode() + b"\n" + rgb.tobytes()); proc.stdin.flush()
        line = proc.stdout.readline()
    if not line:
        raise RuntimeError("OCR 프로세스가 응답 없이 끝났습니다.")
    out = json.loads(line)
    if "error" in out:
        raise RuntimeError(f"OCR 실패: {out['error']}")
    return {"rec_texts": out["texts"], "rec_polys": out["polys"], "rec_scores": out["scores"]}


def ocr_predict(rgb: np.ndarray) -> list[tuple[str, np.ndarray, float]]:
    """RGB 배열 1장을 읽어 (글자, 다각형, 점수) 목록을 돌려준다."""
    if os.getenv("VIDEO_OCR_PYTHON"):
        res = _ocr_remote(rgb)
        return [(t, np.asarray(p, float), float(s)) for t, p, s in zip(res["rec_texts"], res["rec_polys"], res["rec_scores"])]
    res = list(ocr().predict(input=rgb[:, :, ::-1].copy()))[0].json
    res = json.loads(res) if isinstance(res, str) else res
    res = res.get("res", res)
    return [(t, np.asarray(p, float), float(s)) for t, p, s in zip(res["rec_texts"], res["rec_polys"], res["rec_scores"])]


def siglip_embed(images) -> np.ndarray:
    """PIL 이미지 목록 → 정규화한 SigLIP2 임베딩(64장씩)."""
    import torch
    proc, model = siglip(); out = []
    for i in range(0, len(images), 64):
        x = proc(images=images[i:i + 64], return_tensors="pt").to(model.device)
        with torch.inference_mode():
            f = model.get_image_features(pixel_values=x["pixel_values"].to(model.dtype))
        out.append(torch.nn.functional.normalize(getattr(f, "pooler_output", f).float(), dim=-1).cpu())
    return torch.cat(out).numpy() if out else np.zeros((0, 1152), np.float32)
