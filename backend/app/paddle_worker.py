"""Paddle 전용 가상환경에서 실행하는 한국어 영역 인식 작업자."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time


def run(manifest_path, output_path, model_dir):
    """한 번 로드한 CPU 인식 모델로 최대 여덟 영역을 읽고 원시 결과를 저장한다."""
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(Path(__file__).resolve().parents[2] / ".runtime/paddlex"))
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(manifest, list) or not 1 <= len(manifest) <= 8:
        raise ValueError("Expected 1..8 crops")
    directory = Path(model_dir)
    identity = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for name in ("inference.json", "inference.pdiparams", "inference.yml"):
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != identity["sha256"][name]:
            raise ValueError("Model hash mismatch")
    started = time.perf_counter()
    from paddleocr import TextRecognition
    import paddle
    model = TextRecognition(model_name="korean_PP-OCRv5_mobile_rec", model_dir=str(directory),
                            device="cpu", enable_mkldnn=False, cpu_threads=4)
    loaded = time.perf_counter()
    outputs = []
    for item in manifest:
        tick = time.perf_counter()
        result = model.predict(input=item["path"], batch_size=1)
        if len(result) != 1:
            raise ValueError("Expected one text result per crop")
        raw = result[0].json
        if isinstance(raw, str):
            raw = json.loads(raw)
        value = raw.get("res", raw)
        outputs.append({"id": item["id"], "text": value["rec_text"], "confidence": float(value["rec_score"]),
                        "raw": raw, "elapsed_seconds": time.perf_counter() - tick})
    identity.update(engine="paddleocr", paddleocr_version=importlib.metadata.version("paddleocr"),
                    paddle_version=paddle.__version__, paddlex_version=importlib.metadata.version("paddlex"))
    payload = {"outputs": outputs, "device": paddle.get_device(), "model_identity": identity,
               "load_seconds": loaded - started, "inference_seconds": time.perf_counter() - loaded}
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        payload["peak_rss_bytes"] = rss if sys.platform == "darwin" else rss * 1024
    except ImportError:
        payload["peak_rss_bytes"] = None
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def main():
    """상위 작업자가 지정한 로컬 crop 목록과 출력 경로만 받는다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-dir", required=True)
    args = parser.parse_args()
    run(args.manifest, args.output, args.model_dir)


if __name__ == "__main__":
    main()
