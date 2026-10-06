"""Local OCR with automatic CUDA/MPS selection and ONNX CPU fallback."""
from __future__ import annotations

import importlib.util
import json
import math
import os
import platform
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from functools import lru_cache
from pathlib import Path
from threading import Lock

from PIL import Image

from app.ocr_models import model_directory, model_paths, models_ready
from app.ocr_device import select_device

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime'
METHODS = {'rapidocr': 'rapidocr-ppocrv5-korean/onnx-cpu-v1', 'apple': 'apple-vision-ocr/local-v1'}
_INFERENCE_LOCK = Lock()


def selected_backend():
    return os.environ.get('LATTE_OCR_BACKEND', 'rapidocr').strip().lower()


def method_name(device=None):
    backend = selected_backend()
    if backend == 'rapidocr':
        try:
            device = device or select_device().device
        except ValueError:
            return 'ocr/unconfigured'
        if device != 'cpu':
            return f'rapidocr-ppocrv5det-ppocrv4-ko-en/torch-{device}-v1'
    return METHODS.get(backend, 'ocr/unconfigured')


def engine_label(device):
    if device == 'cpu':
        return 'RapidOCR · PP-OCRv5 한국어/영어 · ONNX CPU'
    return f'RapidOCR · PP-OCRv5 검출/PP-OCRv4 한국어·영어 · PyTorch {device.upper()}'


def get_capabilities():
    backend = selected_backend()
    if backend == 'rapidocr':
        selection = select_device()
        device, fallback = selection.device, selection.reason
        installed = all(importlib.util.find_spec(name) is not None for name in ('rapidocr', 'onnxruntime'))
        ready = models_ready()
        if device != 'cpu' and not models_ready('torch'):
            device, fallback = 'cpu', 'GPU 모델이 준비되지 않아 CPU로 실행합니다. scripts/prepare_ocr.py를 실행하세요.'
        available = installed and ready
        engine = engine_label(device)
        note = ('로컬에서 판독합니다. 사용 가능 표시는 실제 추론 성공 보장이 아닙니다.' if available
                else 'OCR 준비가 필요합니다. 의존성 설치 후 scripts/prepare_ocr.py를 실행하세요.')
        if fallback:
            note += ' ' + fallback
    elif backend == 'apple':
        installed = platform.system() == 'Darwin' and shutil.which('swiftc') is not None
        ready = (RUNTIME / 'latte-ocr').is_file()
        available = installed
        engine = 'Apple Vision (Korean + English)'
        note = 'Mac 전용 선택 엔진입니다. 첫 실행 시 Swift 도구를 컴파일합니다.'
    else:
        installed = ready = available = False
        engine = '알 수 없는 OCR 설정'
        note = 'LATTE_OCR_BACKEND는 rapidocr 또는 apple로 지정하세요.'
    return {
        'ocr_backend': backend, 'ocr_available': available, 'ocr_installed': installed,
        'ocr_ready': ready, 'ocr_engine': engine,
        'supported_rules': ['C01', 'C02', 'C03'] if available else [],
        'manual_review_rules': ['C04', 'C05'], 'visual_inspection_available': False,
        'external_transfer': False, 'note': note,
        **({'ocr_device_requested': selection.requested, 'ocr_device_selected': selection.device,
            'ocr_device': device, 'ocr_fallback_reason': fallback} if backend == 'rapidocr' else {}),
    }


@lru_cache(maxsize=3)
def _rapid_engine(directory: str, device='cpu'):
    paths = model_paths(verify=True) if device == 'cpu' else model_paths(verify=True, profile='torch')
    # Imports and model initialization must never write to the MCP protocol stream.
    with redirect_stdout(sys.stderr):
        from rapidocr import RapidOCR, EngineType, OCRVersion, LangDet, LangRec, ModelType
        params = {
            'Global.log_level': 'critical',
            'Global.text_score': 0.0,  # Preserve uncertain text for the review policy.
            # The supplied advertisements are upright. The Chinese orientation
            # classifier can incorrectly turn Korean discount labels upside down.
            'Global.use_cls': False,
            'Det.engine_type': EngineType.ONNXRUNTIME,
            'Det.ocr_version': OCRVersion.PPOCRV5,
            'Det.lang_type': LangDet.CH, 'Det.model_type': ModelType.MOBILE,
            'Det.model_path': str(paths['det']),
            'Cls.engine_type': EngineType.ONNXRUNTIME,
            'Cls.ocr_version': OCRVersion.PPOCRV4,
            'Cls.model_path': str(paths['cls']),
            'Rec.engine_type': EngineType.ONNXRUNTIME,
            'Rec.ocr_version': OCRVersion.PPOCRV5,
            'Rec.lang_type': LangRec.KOREAN, 'Rec.model_type': ModelType.MOBILE,
            'Rec.model_path': str(paths['rec']),
            'EngineConfig.onnxruntime.use_cuda': False,
            'EngineConfig.onnxruntime.use_dml': False,
            'EngineConfig.onnxruntime.intra_op_num_threads': min(4, os.cpu_count() or 1),
            'EngineConfig.onnxruntime.inter_op_num_threads': 1,
        }
        if device != 'cpu':
            import torch
            torch.set_num_threads(min(4, os.cpu_count() or 1))
            params.update({
                'Det.engine_type': EngineType.TORCH,
                'Cls.engine_type': EngineType.TORCH,
                'Rec.engine_type': EngineType.TORCH,
                'Rec.ocr_version': OCRVersion.PPOCRV4,
                'Rec.rec_keys_path': str(paths['dict']),
                'EngineConfig.torch.use_cuda': device == 'cuda',
                'EngineConfig.torch.use_mps': device == 'mps',
            })
        engine = RapidOCR(params=params)
        if device != 'cpu':
            from app.ocr_rec import add_english_recognizer
            add_english_recognizer(engine, paths)
            actual_devices = {getattr(engine, stage).session.device.type
                              for stage in ('text_det', 'text_cls', 'text_rec')}
            if actual_devices != {device}:
                raise RuntimeError(f'OCR 장치 불일치: 요청 {device}, 실제 {actual_devices}')
        return engine


def normalize_lines(boxes, texts, scores, width, height):
    """Convert original-image quadrilaterals to normalized top-left XYWH."""
    if boxes is None and texts is None and scores is None:
        return []
    if any(value is None for value in (boxes, texts, scores)) or not (len(boxes) == len(texts) == len(scores)):
        raise ValueError('OCR 텍스트와 좌표 응답이 일치하지 않습니다.')
    lines = []
    for box, text, confidence in zip(boxes, texts, scores):
        if not isinstance(text, str) or not text.strip():
            continue
        points = [(float(point[0]), float(point[1])) for point in box]
        confidence = float(confidence)
        if len(points) != 4 or not all(math.isfinite(v) for point in points for v in point):
            raise ValueError('OCR 좌표가 유효하지 않습니다.')
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError('OCR 신뢰 신호가 유효하지 않습니다.')
        x1 = max(0.0, min(width, min(x for x, _ in points)))
        x2 = max(0.0, min(width, max(x for x, _ in points)))
        y1 = max(0.0, min(height, min(y for _, y in points)))
        y2 = max(0.0, min(height, max(y for _, y in points)))
        if x2 <= x1 or y2 <= y1:
            raise ValueError('OCR 근거 영역의 크기가 유효하지 않습니다.')
        lines.append({'text': text.strip(), 'confidence': confidence,
                      'bbox': [x1 / width, y1 / height, (x2 - x1) / width, (y2 - y1) / height]})
    return sorted(lines, key=lambda row: (row['bbox'][1], row['bbox'][0]))


def merge_same_line_fragments(lines, height_to_width=1.0):
    """Join only close, similarly sized fragments on the same horizontal line.

    Geometry alone controls grouping; expected words and answer keys are not used.
    The lowest confidence is retained so merging cannot turn uncertain text into a pass.
    """
    groups = []
    for line in sorted(lines, key=lambda row: (row['bbox'][0], row['bbox'][1])):
        x, y, w, h = line['bbox']
        target = None
        for group in groups:
            gx, gy, gw, gh = group['bbox']
            if (0 <= x - gx - gw <= max(h, gh) * height_to_width * 1.2
                    and min(h, gh) >= max(h, gh) * .65
                    and abs(y + h / 2 - gy - gh / 2) <= min(h, gh) * .25):
                target = group
                break
        if target is None:
            groups.append({**line, 'bbox': list(line['bbox'])})
        else:
            gx, gy, gw, gh = target['bbox']
            target['text'] += ' ' + line['text']
            target['confidence'] = min(target['confidence'], line['confidence'])
            target['bbox'] = [gx, min(gy, y), max(gx + gw, x + w) - gx,
                              max(gy + gh, y + h) - min(gy, y)]
    return sorted(groups, key=lambda row: (row['bbox'][1], row['bbox'][0]))


def _read_rapid(path: Path):
    try:
        with Image.open(path) as image:
            if image.getexif().get(274, 1) != 1:
                raise ValueError('EXIF 회전·반전 정보가 있는 이미지입니다. 방향을 적용해 PNG로 다시 저장한 뒤 제출하세요.')
            width, height = image.size
        with _INFERENCE_LOCK, redirect_stdout(sys.stderr):
            selection = select_device()
            device, fallback = selection.device, selection.reason
            # Bytes avoid platform-specific Unicode path handling in image libraries.
            content = path.read_bytes()
            try:
                engine = _rapid_engine(str(model_directory()), device) if device != 'cpu' else _rapid_engine(str(model_directory()))
                result = engine(content)
            except Exception as exc:
                if device == 'cpu':
                    raise
                fallback = f'{device.upper()} 실행 실패로 CPU 전환 ({type(exc).__name__}): {str(exc)[:400]}'
                device = 'cpu'
                result = _rapid_engine(str(model_directory()))(content)
        raw_lines = normalize_lines(result.boxes, result.txts, result.scores, width, height)
        return {'engine': engine_label(device), 'method': method_name(device),
                'device_requested': selection.requested, 'device_selected': selection.device,
                'device': device, 'fallback_reason': fallback,
                'coordinate_system': 'normalized_top_left_xywh',
                'lines': merge_same_line_fragments(raw_lines, height / width), 'raw_lines': raw_lines}
    except ImportError as exc:
        raise RuntimeError('RapidOCR/ONNX 의존성을 불러올 수 없습니다. README의 설치·런타임 안내를 확인하세요.') from exc
    except (OSError, ValueError, RuntimeError):
        raise
    except Exception as exc:
        raise RuntimeError(f'RapidOCR 추론 실패 ({type(exc).__name__}): {exc}') from exc


def _apple_binary():
    if platform.system() != 'Darwin' or shutil.which('swiftc') is None:
        raise RuntimeError('Apple Vision은 Mac 전용입니다. LATTE_OCR_BACKEND=rapidocr를 사용하세요.')
    import fcntl
    RUNTIME.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'scripts/ocr.swift'
    binary = RUNTIME / 'latte-ocr'
    with (RUNTIME / 'ocr-compile.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
            cache = RUNTIME / 'swift-module-cache'
            cache.mkdir(exist_ok=True)
            temporary = RUNTIME / 'latte-ocr.build'
            completed = subprocess.run(
                [shutil.which('swiftc'), '-O', '-module-cache-path', str(cache), str(source), '-o', str(temporary)],
                capture_output=True, text=True, encoding='utf-8', timeout=120, check=False)
            if completed.returncode:
                raise RuntimeError('OCR 도구 컴파일 실패: ' + completed.stderr[-1500:])
            temporary.replace(binary)
    return binary


def read_image(path: Path):
    backend = selected_backend()
    if backend == 'rapidocr':
        return _read_rapid(path)
    if backend == 'apple':
        completed = subprocess.run([str(_apple_binary()), str(path)], capture_output=True,
                                   text=True, encoding='utf-8', timeout=45, check=False)
        if completed.returncode:
            raise RuntimeError('OCR 실행 실패: ' + (completed.stderr[-1000:] or str(completed.returncode)))
        payload = json.loads(completed.stdout)
        if not isinstance(payload.get('lines'), list):
            raise RuntimeError('OCR 응답 형식 오류')
        return {**payload, 'method': METHODS['apple']}
    raise RuntimeError('LATTE_OCR_BACKEND는 rapidocr 또는 apple로 지정하세요.')
