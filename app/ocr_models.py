"""Pinned local OCR models, downloaded only by the explicit preparation command.

Model URLs and SHA-256 values are from RapidOCR 3.4.5's official manifest:
https://github.com/RapidAI/RapidOCR/blob/v3.4.5/python/rapidocr/default_models.yaml
The Korean PP-OCRv5 recognizer includes Korean, English, numbers and symbols.
https://www.paddleocr.ai/main/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
_BASE_URL = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.4.0/onnx"
_MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
_MODELS = {
    "det": {
        "filename": "ch_PP-OCRv5_mobile_det.onnx",
        "url": f"{_BASE_URL}/PP-OCRv5/det/ch_PP-OCRv5_mobile_det.onnx",
        "sha256": "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
    },
    "cls": {
        "filename": "ch_ppocr_mobile_v2.0_cls_infer.onnx",
        "url": f"{_BASE_URL}/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_infer.onnx",
        "sha256": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
    },
    "rec": {
        "filename": "korean_PP-OCRv5_rec_mobile_infer.onnx",
        "url": f"{_BASE_URL}/PP-OCRv5/rec/korean_PP-OCRv5_rec_mobile_infer.onnx",
        "sha256": "cd6e2ea50f6943ca7271eb8c56a877a5a90720b7047fe9c41a2e541a25773c9b",
    },
}

# Official RapidOCR 3.9.0 manifest; Korean v5 is not released for Torch.
# Use v5 detection + v4 Korean recognition on CUDA/MPS. The dictionary digest
# is pinned from that official release URL (the manifest has no dictionary hash).
_TORCH_BASE = 'https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.0'
_TORCH_MODELS = {
    'det': {'filename': 'ch_PP-OCRv5_det_mobile.pth',
            'url': f'{_TORCH_BASE}/torch/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.pth',
            'sha256': 'df848ed5060bac4d0f6e58572aea97d92e909a8a87cf292849237b0e84f6ffdb'},
    'cls': {'filename': 'ch_ptocr_mobile_v2.0_cls_mobile.pth',
            'url': f'{_TORCH_BASE}/torch/PP-OCRv4/cls/ch_ptocr_mobile_v2.0_cls_mobile.pth',
            'sha256': 'bfe13860824b3365c0c7f7ccfcddc8ff11645c60051739ff18bc9913f60c98e1'},
    'rec': {'filename': 'korean_PP-OCRv4_rec_mobile.pth',
            'url': f'{_TORCH_BASE}/torch/PP-OCRv4/rec/korean_PP-OCRv4_rec_mobile.pth',
            'sha256': 'f50614ab06416037454ac2cc6ade9a758f0222f518254fd57dd65f428f763a01'},
    'dict': {'filename': 'korean_dict.txt',
             'url': f'{_TORCH_BASE}/paddle/PP-OCRv4/rec/korean_PP-OCRv4_rec_mobile/korean_dict.txt',
             'sha256': 'aa1fdc8ae8f7cd40a0ec4edb472eb0421e11427e6ccfee9915440742c18b0a20'},
    'en_rec': {'filename': 'en_PP-OCRv4_rec_mobile.pth',
               'url': f'{_TORCH_BASE}/torch/PP-OCRv4/rec/en_PP-OCRv4_rec_mobile.pth',
               'sha256': 'f9723c94847de59df9b059faaed041fbb35f014ea973da40290b1e97004e1d4e'},
    'en_dict': {'filename': 'en_dict.txt',
                'url': f'{_TORCH_BASE}/paddle/PP-OCRv4/rec/en_PP-OCRv4_rec_mobile/en_dict.txt',
                'sha256': '5662df9d2d03f0e8ca0d3b0649d6acbab904b6a14b3d3521463c71c37c668ce3'},
}


def model_manifest(profile='onnx'):
    if profile == 'onnx':
        return _MODELS
    if profile == 'torch':
        return _TORCH_MODELS
    raise ValueError('OCR 모델 profile은 onnx 또는 torch여야 합니다.')


def model_directory() -> Path:
    """Return the shared model cache without creating it or accessing the network."""
    configured = os.environ.get("LATTE_OCR_MODEL_DIR")
    return Path(configured or ROOT / ".runtime" / "ocr-models").expanduser().resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _present(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def model_paths(verify: bool = True, profile='onnx') -> dict[str, Path]:
    """Require all local models; optionally validate their exact pinned contents."""
    directory = model_directory()
    result: dict[str, Path] = {}
    for name, model in model_manifest(profile).items():
        path = directory / model["filename"]
        if not _present(path):
            raise RuntimeError(
                f"OCR 모델이 준비되지 않았습니다: {path.name}. "
                "프로젝트 폴더에서 python scripts/prepare_ocr.py를 실행하세요."
            )
        if verify:
            try:
                valid = _sha256(path) == model["sha256"]
            except OSError as exc:
                raise RuntimeError(f"OCR 모델을 읽을 수 없습니다: {path.name}.") from exc
            if not valid:
                raise RuntimeError(
                    f"OCR 모델의 SHA-256 검증에 실패했습니다: {path.name}. "
                    "python scripts/prepare_ocr.py로 모델을 다시 준비하세요."
                )
        result[name] = path
    return result


def models_ready(profile='onnx') -> bool:
    """Cheap readiness probe; actual loading must use model_paths(verify=True)."""
    try:
        directory = model_directory()
        return all(_present(directory / model["filename"]) for model in model_manifest(profile).values())
    except (OSError, RuntimeError, ValueError):
        return False


class _HTTPSRedirectHandler(HTTPRedirectHandler):
    """Allow the model host's CDN redirects while refusing an HTTP downgrade."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme.lower() != "https":
            raise HTTPError(newurl, code, "OCR 모델은 HTTPS로만 다운로드합니다.", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def prepare_models(profile='onnx') -> dict[str, Path]:
    """Download missing/corrupt models, verify them, and atomically publish each file.

    This function is an explicit setup operation. Inference and health checks
    must use model_paths/models_ready instead. urllib's default HTTPS handler
    verifies certificates; no unverified SSL context is used.
    """
    directory = model_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"OCR 모델 저장 폴더를 만들 수 없습니다: {directory}") from exc

    opener = build_opener(_HTTPSRedirectHandler())
    for model in model_manifest(profile).values():
        destination = directory / model["filename"]
        try:
            if _present(destination) and _sha256(destination) == model["sha256"]:
                continue
        except OSError as exc:
            raise RuntimeError(f"OCR 모델을 읽을 수 없습니다: {destination.name}.") from exc

        temporary: Path | None = None
        try:
            request = Request(model["url"], headers={"User-Agent": "CreativeChecker-OCR-Setup/1.0"})
            digest = hashlib.sha256()
            total_bytes = 0
            with opener.open(request, timeout=30) as response:
                if urlsplit(response.geturl()).scheme.lower() != "https":
                    raise RuntimeError("OCR 모델은 HTTPS로만 다운로드합니다.")
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=directory, prefix=f".{destination.name}.", suffix=".download", delete=False
                ) as output:
                    temporary = Path(output.name)
                    while chunk := response.read(1024 * 1024):
                        total_bytes += len(chunk)
                        if total_bytes > _MAX_DOWNLOAD_BYTES:
                            raise RuntimeError("OCR 모델 다운로드가 허용 크기를 초과했습니다.")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            if digest.hexdigest() != model["sha256"]:
                raise RuntimeError("다운로드한 OCR 모델의 SHA-256 값이 공식 배포값과 다릅니다.")
            # The temporary file is already closed, which is also required on Windows.
            os.replace(temporary, destination)
            temporary = None
        except Exception as exc:
            raise RuntimeError(
                f"OCR 모델 준비에 실패했습니다: {destination.name}. {exc} "
                "네트워크·저장 권한을 확인한 뒤 python scripts/prepare_ocr.py를 다시 실행하세요."
            ) from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return model_paths(verify=True, profile=profile)
