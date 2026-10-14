"""EasyOCR Korean/English inference, local CUDA → MPS → CPU or explicit RunPod.

Health checks never download or load models. Set OCR_ALLOW_DOWNLOAD=1, or run
``python -m backend.app.ocr_runtime --download-models``, to provision the local model cache.
RUN_TARGET=runpod sends image bytes only to the configured RunPod endpoint;
RUN_TARGET=local is also the mode used inside a RunPod Pod/worker.
"""
from __future__ import annotations

import argparse
import base64
import gc
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_FILES = ("craft_mlt_25k.pth", "korean_g2.pth")
# This is a processed-image limit. The upload API separately caps original
# files at 20 MiB/10 MP; a normalized 2x PNG can approach 120 MB at 40 MP.
MAX_IMAGE_BYTES = 128 * 1024 * 1024
MAX_REMOTE_BYTES = 7 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
_LOCK = threading.RLock()
_READERS: dict = {}
_DEVICE_FAILURES: dict = {}
_ACTIVE_DEVICE: dict = {}


class OCRRuntimeError(RuntimeError):
    def __init__(self, message, attempts=None, code=None):
        super().__init__(message)
        self.attempts = attempts or []
        self.code = code


def _target():
    value = os.getenv("RUN_TARGET", "local").strip().lower()
    if value not in {"local", "runpod"}:
        raise OCRRuntimeError("RUN_TARGET must be local or runpod")
    return value


def _model_dir():
    return Path(os.getenv("OCR_MODEL_DIR", str(PROJECT_ROOT / ".runtime" / "easyocr"))).expanduser().resolve()


def _device_availability():
    torch = importlib.import_module("torch")
    mps = getattr(getattr(torch, "backends", None), "mps", None)
    return [("cuda", bool(torch.cuda.is_available())),
            ("mps", bool(mps and mps.is_available())), ("cpu", True)]


def _remote_config():
    key = os.getenv("RUNPOD_API_KEY", "").strip()
    endpoint = os.getenv("RUNPOD_ENDPOINT_ID", "").strip()
    if not key or not endpoint:
        raise OCRRuntimeError("Set server-side RUNPOD_API_KEY and RUNPOD_ENDPOINT_ID")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", endpoint):
        raise OCRRuntimeError("RUNPOD_ENDPOINT_ID has an invalid format")
    return key, endpoint


def get_runtime_status():
    """Return configuration readiness; no inference, remote requests, or downloads."""
    status = {"engine": "easyocr", "execution_target": "local", "device": "unknown", "ready": False}
    try:
        status["execution_target"] = _target()
        if status["execution_target"] == "runpod":
            status["device"] = "remote-auto"
            _remote_config()
            status.update(ready=True, detail="RunPod configured; worker availability is checked on submission")
            return status
        missing = [name for name in ("torch", "torchvision", "easyocr", "numpy", "PIL") if importlib.util.find_spec(name) is None]
        if missing:
            status["detail"] = "Install runtime dependencies: " + ", ".join(missing)
            return status
        available = [device for device, ready in _device_availability() if ready]
        cache = str(_model_dir())
        status["device"] = _ACTIVE_DEVICE.get(cache, available[0])
        missing_models = [name for name in MODEL_FILES if not (_model_dir() / name).is_file()]
        if missing_models:
            status["detail"] = "Models missing; run python -m backend.app.ocr_runtime --download-models: " + ", ".join(missing_models)
        else:
            status.update(ready=True, detail="Local cache present; model integrity is checked when loading")
    except Exception as exc:
        # Do not return credentials or URLs embedded in third-party error messages.
        status["detail"] = str(exc) if isinstance(exc, OCRRuntimeError) else f"Runtime unavailable ({type(exc).__name__})"
    return status


def _device_error(exc, device):
    """Retry only identifiable backend failures, never bad files/download errors."""
    if device == "cpu" or not isinstance(exc, (RuntimeError, NotImplementedError, AssertionError)):
        return False
    message = str(exc).lower()
    if any(token in message for token in ("md5", "checksum", "download", "http", "file not found")):
        return False
    backend = ("cuda", "cudnn", "cublas", "nvidia") if device == "cuda" else ("mps", "metal")
    return any(token in message for token in backend) and any(token in message for token in (
        "out of memory", "not available", "not compiled", "not implemented", "not supported",
        "unsupported", "driver", "initializ", "allocat", "not linked", "could not run"))


def _load_reader(device, allow_download):
    cache = str(_model_dir())
    key = (cache, device)
    if key not in _READERS:
        easyocr = importlib.import_module("easyocr")
        _READERS[key] = easyocr.Reader(
            ["ko", "en"], gpu=False if device == "cpu" else device,
            model_storage_directory=cache, user_network_directory=str(_model_dir() / "user_network"),
            download_enabled=allow_download, verbose=False, quantize=device == "cpu")
    return _READERS[key]


def _release_device(device):
    gc.collect()
    try:
        torch = importlib.import_module("torch")
        backend = torch.cuda if device == "cuda" else getattr(torch, "mps", None)
        if backend:
            backend.empty_cache()
    except (RuntimeError, AttributeError):
        pass


def _local_operation(operation, allow_download=False):
    attempts = []
    cache = str(_model_dir())
    # Loading and inference share a lock: neither duplicate Reader loads nor
    # concurrent GPU inference can exhaust memory inside this process.
    with _LOCK:
        for device, available in _device_availability():
            if not available:
                attempts.append({"device": device, "status": "skipped", "reason": "backend unavailable"})
                continue
            if (cache, device) in _DEVICE_FAILURES:
                attempts.append({"device": device, "status": "skipped", "reason": _DEVICE_FAILURES[cache, device]})
                continue
            try:
                reader = _load_reader(device, allow_download)
                result = operation(reader)
                _ACTIVE_DEVICE[cache] = device
                attempts.append({"device": device, "status": "success"})
                return result, device, attempts
            except Exception as exc:
                if not _device_error(exc, device):
                    raise OCRRuntimeError(f"EasyOCR {type(exc).__name__}: {exc}", attempts) from exc
                reason = f"{type(exc).__name__}: {str(exc)[:400]}"
                attempts.append({"device": device, "status": "fallback", "reason": reason})
                # Keep later images on the known working backend in this process.
                _DEVICE_FAILURES[cache, device] = reason
                _READERS.pop((cache, device), None)
                reader = None
                _release_device(device)
    raise OCRRuntimeError("No working OCR device", attempts)


def warmup_models(allow_download=False):
    """Initialize the local Reader; callers explicitly opt into model download."""
    if _target() != "local":
        raise OCRRuntimeError("Model warmup requires RUN_TARGET=local (also inside a RunPod worker)")
    _, device, attempts = _local_operation(lambda reader: None, allow_download or os.getenv("OCR_ALLOW_DOWNLOAD") == "1")
    return {"engine": "easyocr", "execution_target": "local", "device": device, "ready": True, "attempts": attempts}


def _read_image(image_path):
    from PIL import Image
    image_path = Path(image_path)
    if not image_path.is_file() or not 0 < image_path.stat().st_size <= MAX_IMAGE_BYTES:
        raise OCRRuntimeError("Processed image must be an existing nonempty file of at most 128 MiB")
    with Image.open(image_path) as source:
        if source.width * source.height > MAX_IMAGE_PIXELS:
            raise OCRRuntimeError("Image exceeds the 40 megapixel input limit")
        source.load()
        # Preserve raw input coordinates; preprocessing owns EXIF normalization.
        rgba = source.convert("RGBA")
        rgb = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")
    return rgb


def _records(observations, width, height):
    records = []
    for index, (polygon, text, confidence) in enumerate(observations):
        points = [[float(x), float(y)] for x, y in polygon]
        if len(points) < 4 or not all(math.isfinite(v) for point in points for v in point):
            raise OCRRuntimeError("OCR returned an invalid polygon")
        points = [[max(0.0, min(float(width), x)), max(0.0, min(float(height), y))] for x, y in points]
        box = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]
        if box[0] >= box[2] or box[1] >= box[3]:
            raise OCRRuntimeError("OCR returned a degenerate polygon")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise OCRRuntimeError("OCR returned invalid confidence")
        records.append({"id": f"ocr_{index:03d}", "text": str(text), "confidence": confidence,
                        "bbox_xyxy": box, "polygon": points})
    return records


def _recognize_local(image_path):
    import numpy as np
    rgb = _read_image(image_path)
    # EasyOCR interprets a three-channel ndarray as BGR.
    pixels = np.asarray(rgb)[:, :, ::-1].copy()
    observations, device, attempts = _local_operation(
        lambda reader: reader.readtext(pixels, detail=1, paragraph=False, batch_size=1, workers=0),
        os.getenv("OCR_ALLOW_DOWNLOAD") == "1")
    return {"engine": "easyocr", "execution_target": "local", "device": device, "status": "success",
            "records": _records(observations, rgb.width, rgb.height),
            "input_dimensions": [rgb.width, rgb.height], "attempts": attempts}


class _RemoteHTTPError(OCRRuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"RunPod returned HTTP {status}; check server-side endpoint configuration")


def _http_json(method, url, key, timeout, payload=None):
    request = urllib.request.Request(url, method=method, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                     data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read(10 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise _RemoteHTTPError(exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        # Do not repeat POST /run: a timed-out request may already be queued.
        raise OCRRuntimeError("RunPod network request failed; submission was not automatically repeated") from None
    if len(content) > 10 * 1024 * 1024:
        raise OCRRuntimeError("RunPod response is too large")
    try:
        body = json.loads(content)
    except (ValueError, UnicodeError):
        raise OCRRuntimeError("RunPod returned invalid JSON") from None
    if not isinstance(body, dict):
        raise OCRRuntimeError("RunPod response must be a JSON object")
    return body


def _seconds(name, default, minimum, maximum):
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        raise OCRRuntimeError(f"{name} must be numeric") from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise OCRRuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _validate_remote_result(result, dimensions):
    if not isinstance(result, dict) or result.get("status") != "success" or result.get("engine") != "easyocr":
        raise OCRRuntimeError("RunPod worker returned an invalid OCR result")
    if result.get("input_dimensions") != list(dimensions) or result.get("device") not in {"cuda", "mps", "cpu"}:
        raise OCRRuntimeError("RunPod worker dimensions/device do not match the OCR contract")
    records = result.get("records")
    if not isinstance(records, list):
        raise OCRRuntimeError("RunPod worker records must be a list")
    try:
        validated = _records([(record["polygon"], record["text"], record["confidence"]) for record in records], *dimensions)
    except (KeyError, TypeError, ValueError):
        raise OCRRuntimeError("RunPod worker returned malformed OCR records") from None
    return {"engine": "easyocr", "execution_target": "runpod", "device": result["device"], "status": "success",
            "records": validated, "input_dimensions": list(dimensions), "attempts": result.get("attempts", [])}


def _recognize_runpod(image_path):
    key, endpoint = _remote_config()
    image_path = Path(image_path)
    dimensions = _read_image(image_path).size
    if image_path.stat().st_size > MAX_REMOTE_BYTES:
        raise OCRRuntimeError("RunPod sample input must be at most 7 MiB before base64 encoding", code="remote_image_too_large")
    timeout = _seconds("RUNPOD_TIMEOUT_SECONDS", 300, 1, 1800)
    poll = _seconds("RUNPOD_POLL_SECONDS", 2, 0.01, 30)
    base = f"https://api.runpod.ai/v2/{endpoint}"
    deadline = time.monotonic() + timeout
    body = _http_json("POST", f"{base}/run", key, min(30, timeout), {
        "input": {"image_base64": base64.b64encode(image_path.read_bytes()).decode("ascii")},
        "policy": {"executionTimeout": max(5000, int(timeout * 1000)), "ttl": max(10000, int(timeout * 1000))}})
    job_id = body.get("id")
    if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
        raise OCRRuntimeError("RunPod did not return a valid job ID; submission was not repeated")
    failures = 0
    try:
        while True:
            state = body.get("status")
            if state == "COMPLETED":
                result = _validate_remote_result(body.get("output"), dimensions)
                result["job_id"] = job_id
                return result
            if state in {"FAILED", "CANCELLED", "TIMED_OUT"}:
                raise OCRRuntimeError(f"RunPod job {state.lower()}; inspect worker logs")
            if state not in {"IN_QUEUE", "IN_PROGRESS"}:
                raise OCRRuntimeError("RunPod returned an unknown job status")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OCRRuntimeError("RunPod job exceeded the configured timeout")
            time.sleep(min(poll * (2 ** failures), remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OCRRuntimeError("RunPod job exceeded the configured timeout")
            try:
                body = _http_json("GET", f"{base}/status/{job_id}", key, min(30, remaining))
                failures = 0
            except OCRRuntimeError as exc:
                # Only idempotent status requests retry, at most three times.
                if isinstance(exc, _RemoteHTTPError) and exc.status not in {429, 500, 502, 503, 504}:
                    raise
                failures += 1
                if failures > 3:
                    raise
    except Exception:
        try:
            _http_json("POST", f"{base}/cancel/{job_id}", key, min(5, timeout), {})
        except Exception:
            pass  # Best effort; a local failure does not guarantee remote cancellation.
        raise


def recognize_image(image_path: Path):
    """Recognize only image pixels, returning boxes in the supplied input's space."""
    return _recognize_runpod(image_path) if _target() == "runpod" else _recognize_local(image_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-models", action="store_true", help="Explicitly download and load ko/en models")
    parser.add_argument("--input", type=Path, help="Run OCR on one image")
    args = parser.parse_args()
    result = warmup_models(allow_download=True) if args.download_models else recognize_image(args.input) if args.input else get_runtime_status()
    print(json.dumps(result, ensure_ascii=False, indent=2))
