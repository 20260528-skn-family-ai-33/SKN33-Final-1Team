"""격리된 Paddle 환경에서 로컬 탐지·한국어 인식 모델만 실행한다."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

from scripts.prepare_paddle import DETECTION_MODEL, MODEL, verify_model


def run(image_path, output_path, detection_dir, recognition_dir):
    """문서 방향·형태 변환 없이 한 이미지의 탐지 좌표와 원시 인식을 보존한다."""
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["HF_HUB_OFFLINE"] = "1"
    root = Path(__file__).resolve().parents[2]
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(root / ".runtime/paddlex")
    identities = {"detection": verify_model(detection_dir, DETECTION_MODEL),
                  "recognition": verify_model(recognition_dir, MODEL)}
    started = time.perf_counter()
    from PIL import Image
    import paddle
    from paddleocr import PaddleOCR
    with Image.open(image_path) as opened:
        dimensions = list(opened.size)
    model = PaddleOCR(
        text_detection_model_name=DETECTION_MODEL, text_detection_model_dir=str(detection_dir),
        text_recognition_model_name=MODEL, text_recognition_model_dir=str(recognition_dir),
        use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
        device="cpu", enable_mkldnn=False, cpu_threads=4,
        text_det_limit_side_len=1280, text_det_limit_type="max", text_det_thresh=.3,
        text_det_box_thresh=.6, text_det_unclip_ratio=1.5, text_rec_score_thresh=0.0,
        text_recognition_batch_size=8,
    )
    loaded = time.perf_counter()
    outputs = list(model.predict(input=str(image_path)))
    if len(outputs) != 1:
        raise ValueError("Expected exactly one Paddle OCR image result")
    raw = outputs[0].json
    if isinstance(raw, str):
        raw = json.loads(raw)
    identities.update(engine="paddleocr", paddleocr_version=importlib.metadata.version("paddleocr"),
                      paddle_version=paddle.__version__, paddlex_version=importlib.metadata.version("paddlex"))
    payload = {"raw": raw, "device": paddle.get_device(), "input_dimensions": dimensions,
               "model_identity": identities, "load_seconds": loaded-started,
               "inference_seconds": time.perf_counter()-loaded}
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        payload["peak_rss_bytes"] = rss if sys.platform == "darwin" else rss * 1024
    except ImportError:
        payload["peak_rss_bytes"] = None
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def main():
    """로컬 입력·출력과 준비된 두 모델 경로만 명령행으로 받는다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--detection-dir", required=True)
    parser.add_argument("--recognition-dir", required=True)
    args = parser.parse_args()
    run(args.input, args.output, args.detection_dir, args.recognition_dir)


if __name__ == "__main__":
    main()
