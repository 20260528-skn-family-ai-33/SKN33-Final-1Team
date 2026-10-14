"""RunPod Serverless handler. Uses the container GPU; never calls another endpoint."""
import base64
import binascii
import os
from pathlib import Path
import tempfile

from .ocr_runtime import MAX_REMOTE_BYTES, OCRRuntimeError, _recognize_local, warmup_models


def handler(job):
    payload = job.get("input", {}) if isinstance(job, dict) else {}
    encoded = payload.get("image_base64") if isinstance(payload, dict) else None
    if not isinstance(encoded, str) or len(encoded) > ((MAX_REMOTE_BYTES + 2) // 3) * 4:
        raise ValueError("input.image_base64 must encode an image of at most 7 MiB")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("input.image_base64 is invalid") from None
    if not data or len(data) > MAX_REMOTE_BYTES:
        raise ValueError("Decoded image is empty or too large")
    with tempfile.TemporaryDirectory(prefix="poster-ocr-") as directory:
        path = Path(directory) / "input.image"
        path.write_bytes(data)
        try:
            return _recognize_local(path)
        except OCRRuntimeError:
            # API response must not echo server configuration or path details.
            raise RuntimeError("OCR inference failed; verify model cache and worker device") from None


if __name__ == "__main__":
    import runpod
    os.environ["RUN_TARGET"] = "local"
    warmup_models(allow_download=False)
    runpod.serverless.start({"handler": handler})
