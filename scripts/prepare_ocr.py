"""Prepare pinned local OCR models before running macOS/Windows inspections."""

from pathlib import Path
import argparse
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ocr_models import prepare_models
from app.ocr_device import select_device


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description='Prepare local OCR models for the selected device.')
    parser.add_argument('--profile', choices=('auto', 'onnx', 'torch', 'all'), default='auto')
    args = parser.parse_args()
    print("한국어·영어 OCR 모델을 준비합니다. 정상적인 기존 모델은 다시 받지 않습니다.", flush=True)
    try:
        paths = prepare_models()
        if args.profile in ('all', 'torch') or (args.profile == 'auto' and select_device().device != 'cpu'):
            paths.update({f'torch/{key}': path for key, path in prepare_models('torch').items()})
    except RuntimeError as exc:
        print(f"준비 실패: {exc}", file=sys.stderr)
        return 1
    print(f"OCR 모델·사전 {len(paths)}개 준비 및 SHA-256 검증 완료.")
    for name, path in paths.items():
        print(f"  {name}: {path}")
    print("이후 검수는 저장된 모델로 로컬에서 실행하며 이미지를 외부로 보내지 않습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
