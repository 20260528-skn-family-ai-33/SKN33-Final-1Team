"""격리된 Paddle 환경에서 모델을 한 번만 불러 영상 프레임 여러 장을 차례로 읽는다.

모델 설정은 paddle_full_worker와 같다(그 파일은 v1 code_revision에 묶여 있어 건드리지 않는다).
프레임마다 결과를 한 줄씩 바로 기록해 호출 쪽이 진행률을 읽을 수 있게 한다.
"""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import time

from scripts.prepare_paddle import DETECTION_MODEL, MODEL, verify_model


def run(frames, output_path, detection_dir, recognition_dir, *, threads=4):
    """프레임 목록을 순서대로 추론하고 프레임당 JSON 한 줄을 남긴다."""
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["HF_HUB_OFFLINE"] = "1"
    root = Path(__file__).resolve().parents[2]
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(root / ".runtime/paddlex")
    identities = {"detection": verify_model(detection_dir, DETECTION_MODEL),
                  "recognition": verify_model(recognition_dir, MODEL)}
    started = time.perf_counter()
    from PIL import Image
    import numpy as np
    import paddle
    from paddleocr import PaddleOCR
    model = PaddleOCR(
        text_detection_model_name=DETECTION_MODEL, text_detection_model_dir=str(detection_dir),
        text_recognition_model_name=MODEL, text_recognition_model_dir=str(recognition_dir),
        use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
        # oneDNN(enable_mkldnn)은 Paddle 3.3.1 CPU에서 NotImplementedError로 실패해 끈다.
        device="cpu", enable_mkldnn=False, cpu_threads=threads,
        text_det_limit_side_len=1280, text_det_limit_type="max", text_det_thresh=.3,
        text_det_box_thresh=.6, text_det_unclip_ratio=1.5, text_rec_score_thresh=0.0,
        text_recognition_batch_size=8,
    )
    identities.update(engine="paddleocr", cpu_threads=threads, paddleocr_version=importlib.metadata.version("paddleocr"),
                      paddle_version=paddle.__version__, paddlex_version=importlib.metadata.version("paddlex"))
    output = Path(output_path)
    with output.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps({"kind": "header", "model_identity": identities, "device": paddle.get_device(),
                                 "load_seconds": time.perf_counter() - started}, ensure_ascii=False) + "\n")
        stream.flush()
        for index, frame in enumerate(frames):
            began = time.perf_counter()
            try:
                with Image.open(frame) as opened:
                    dimensions = list(opened.size)
                    pixels = np.asarray(opened.convert("RGB"))[:, :, ::-1].copy()
                outputs = list(model.predict(input=pixels))
                if len(outputs) != 1:
                    raise ValueError("Expected exactly one Paddle OCR image result")
                raw = outputs[0].json
                if isinstance(raw, str):
                    raw = json.loads(raw)
                row = {"kind": "frame", "index": index, "input_dimensions": dimensions, "raw": raw}
            except Exception as error:  # 한 프레임의 예외는 그 프레임만 실패로 남긴다(문서 3단계)
                row = {"kind": "frame", "index": index, "error": type(error).__name__}
            row["inference_seconds"] = time.perf_counter() - began
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()


def main():
    """프레임 목록 파일과 출력 경로, 준비된 두 모델 경로만 명령행으로 받는다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", required=True, help="한 줄에 프레임 PNG 경로 하나")
    parser.add_argument("--output", required=True)
    parser.add_argument("--detection-dir", required=True)
    parser.add_argument("--recognition-dir", required=True)
    parser.add_argument("--threads", type=int, choices=range(1, 33), default=4, metavar="1-32")
    args = parser.parse_args()
    frames = [line for line in Path(args.frames).read_text(encoding="utf-8").splitlines() if line.strip()]
    run(frames, args.output, args.detection_dir, args.recognition_dir, threads=args.threads)


if __name__ == "__main__":
    main()
