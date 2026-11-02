"""Persistent, isolated PaddleOCR worker. JSON lines on stdout; diagnostics on stderr."""
import argparse
from contextlib import redirect_stdout
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import sys
import time
import traceback

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from scripts.prepare_paddle import DETECTION_MODEL, MODEL, verify_model


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def serve(args):
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
    os.environ['HF_HUB_OFFLINE'] = '1'
    identities = {name: verify_model(args.models / model, model)
                  for name, model in (('detection', DETECTION_MODEL), ('recognition', MODEL))}
    # Keep the application's detection/Korean recognition models and preprocessing.
    config = dict(text_detection_model_name=DETECTION_MODEL,
                  text_detection_model_dir=str(args.models / DETECTION_MODEL),
                  text_recognition_model_name=MODEL,
                  text_recognition_model_dir=str(args.models / MODEL),
                  use_doc_orientation_classify=False, use_doc_unwarping=False,
                  use_textline_orientation=False, device=args.device,
                  enable_mkldnn=False, cpu_threads=4,
                  text_det_limit_side_len=1280, text_det_limit_type='max',
                  text_det_thresh=.3, text_det_box_thresh=.6,
                  text_det_unclip_ratio=1.5, text_rec_score_thresh=0.0,
                  text_recognition_batch_size=8)
    started = time.perf_counter()
    with redirect_stdout(sys.stderr):
        import numpy as np
        import paddle
        from PIL import Image
        from paddleocr import PaddleOCR
        if args.device.startswith('gpu') and not paddle.is_compiled_with_cuda():
            raise RuntimeError('GPU Paddle build required; CPU fallback is forbidden')
        paddle.set_device(args.device)
        model = PaddleOCR(**config)
        if paddle.get_device() != args.device:
            raise RuntimeError('Paddle device differs from requested device')

    def synchronize():
        if args.device.startswith('gpu'):
            paddle.device.cuda.synchronize()

    synchronize()
    emit(dict(ok=True, event='ready', device=paddle.get_device(), model_identity=identities,
              config=config, load_seconds_excluded=time.perf_counter()-started,
              versions={p: metadata.version(p) for p in
                        ('paddleocr', 'paddlex', 'numpy', 'pillow')},
              paddle_version=paddle.__version__,
              CUDA=paddle.version.cuda() if paddle.is_compiled_with_cuda() else None))
    for line in sys.stdin:
        request = json.loads(line)
        try:
            with redirect_stdout(sys.stderr):
                synchronize()
                start = time.perf_counter()
                # Full-resolution OCR. The visual model's 448px letterbox is separate.
                with Image.open(request['path']) as source:
                    dimensions = list(source.size)
                    pixels = np.asarray(source.convert('RGB'))[:, :, ::-1].copy()
                outputs = list(model.predict(input=pixels))
                if len(outputs) != 1:
                    raise ValueError('Expected exactly one Paddle OCR image result')
                raw = outputs[0].json
                if isinstance(raw, str):
                    raw = json.loads(raw)
                texts = raw.get('res', raw)['rec_texts']
                if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
                    raise ValueError('Malformed OCR text output')
                synchronize()
                elapsed = (time.perf_counter()-start)*1000
            emit(dict(ok=True, image_id=request['image_id'], raw=raw,
                      text_count=len(texts), input_dimensions=dimensions,
                      engine_ms=elapsed))
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            emit(dict(ok=False, image_id=request.get('image_id'), error=repr(exc)))
            return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', type=Path, default=REPO/'.runtime/paddle-models')
    parser.add_argument('--device', choices=['cpu', 'gpu:0'], default='gpu:0')
    args = parser.parse_args()
    try:
        return serve(args)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        emit(dict(ok=False, event='startup_failed', error=repr(exc)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
