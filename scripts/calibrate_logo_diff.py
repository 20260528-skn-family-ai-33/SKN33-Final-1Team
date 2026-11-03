#!/usr/bin/env python3
"""logo_diff 임계값 보정 — 정상 세트와 오답 세트의 측정값을 모아 유형별 판정 수를 낸다.

정상 세트: 원본을 여러 축소율·JPEG 품질로 만든 이미지 + (선택) 실제 영상 로고 crop 폴더.
오답 세트: 1~10 TEXT, 11~20 COLOR, 21~30 RATIO, 31~40 CROP, 41~50 ADDED 순서의 오답 이미지.

homography는 GPU 없이 돌 수 있도록 SIFT + RANSAC으로 구한다(검사 대상 → 원본 방향, 원본 픽셀 좌표).
Pod의 XFeat homography와 완전히 같지 않으므로 RATIO·CROP 경계는 Pod 결과로 한 번 더 확인한다.
--ocr 를 주지 않으면 OCR 없이 측정하며 TEXT는 판정하지 않는다(문자 영역 없음으로 처리).

판정 수는 mismatch / uncertain / pass 를 따로 센다. uncertain은 오답 검출 수에 합치지 않는다.
경계 후보(suggested)는 정상 세트에서 가장 나쁜 측정값이며, 실제 경계는 사람이 정한다.

사용:
  python scripts/calibrate_logo_diff.py --original 대전광역시.jpg --wrong-dir 01_오답이미지 --out calibration.json
  python scripts/calibrate_logo_diff.py ... --positives-dir video_crops --thresholds bands.json --ocr
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.modeltest import adapters  # noqa: E402

CATEGORIES = ((1, 10, 'TEXT'), (11, 20, 'COLOR'), (21, 30, 'RATIO'), (31, 40, 'CROP'), (41, 50, 'ADDED'))
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}


def category_of(path):
    found = re.findall(r'\d+', path.stem)
    if not found: return 'UNKNOWN'
    number = int(found[-1])
    return next((name for low, high, name in CATEGORIES if low <= number <= high), 'UNKNOWN')


def make_positives(original, scales, qualities, out_dir):
    from PIL import Image
    paths = []
    with Image.open(original) as source:
        image = source.convert('RGB')
    for scale in scales:
        size = (max(1, round(image.width * scale / 100)), max(1, round(image.height * scale / 100)))
        resized = image.resize(size, Image.Resampling.LANCZOS)
        target = out_dir / f'positive-{scale}pct.png'; resized.save(target); paths.append(target)
        for quality in qualities:
            target = out_dir / f'positive-{scale}pct-q{quality}.jpg'; resized.save(target, quality=quality); paths.append(target)
    return paths


def sift_homography(query_path, reference_path):
    import cv2
    import numpy as np
    from PIL import Image
    def features(path):
        with Image.open(path) as source:
            image = source.convert('L'); width, height = image.size; image.thumbnail((1024, 1024))
        scale = np.array([width / image.width, height / image.height])
        keypoints, descriptors = cv2.SIFT_create(nfeatures=4000).detectAndCompute(np.asarray(image), None)
        return keypoints, descriptors, scale
    qk, qd, qs = features(query_path); rk, rd, rs = features(reference_path)
    if qd is None or rd is None or len(qk) < 4 or len(rk) < 4: return None, {'matches': 0, 'inliers': 0}
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(qd, rd, k=2)
    good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < .75 * pair[1].distance]
    if len(good) < 4: return None, {'matches': len(good), 'inliers': 0}
    a = np.float32([qk[m.queryIdx].pt for m in good]); b = np.float32([rk[m.trainIdx].pt for m in good])
    matrix, mask = cv2.findHomography(a, b, cv2.RANSAC, 3.0, maxIters=2000, confidence=0.995)
    if matrix is None or mask is None or not np.isfinite(matrix).all(): return None, {'matches': len(good), 'inliers': 0}
    homography = np.diag([rs[0], rs[1], 1.0]) @ matrix @ np.linalg.inv(np.diag([qs[0], qs[1], 1.0]))
    return (homography / homography[2, 2]).tolist(), {'matches': len(good), 'inliers': int(mask.sum()), 'inlier_ratio': round(float(mask.mean()), 4)}


def make_ocr(enabled):
    empty = {'text': '', 'ocr_words': []}
    if not enabled: return lambda path: empty
    import numpy as np
    from PIL import Image
    from paddleocr import PaddleOCR
    engine = PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, device='cpu')
    cache = {}
    def read(path):
        key = str(Path(path).resolve())
        if key in cache: return cache[key]
        with Image.open(path) as source:
            pixels = np.asarray(source.convert('RGB'))
        result = list(engine.predict(input=pixels[:, :, ::-1].copy()))[0].json
        if isinstance(result, str): result = json.loads(result)
        result = result.get('res', result)
        words = [{'text': text, 'polygon': np.asarray(poly).tolist(), 'score': float(score)} for text, poly, score in zip(result['rec_texts'], result['rec_polys'], result['rec_scores'])]
        cache[key] = {'text': '\n'.join(word['text'] for word in words), 'ocr_words': words}
        return cache[key]
    return read


def measurements(result):
    regions = result.get('regions') or {}; whole = regions.get('whole', {}); text = regions.get('text', {}); symbol = regions.get('symbol', {}); margin = regions.get('margin', {})
    return {
        'anisotropy_ratio': None if whole.get('anisotropy_percent') is None else whole['anisotropy_percent'] / 100,
        'shear_degrees': whole.get('shear_degrees'), 'crop_coverage_min': whole.get('crop_coverage'), 'crop_outside_max': whole.get('crop_outside_fraction'),
        'delta_e2000': max([value for value in (text.get('delta_e2000'), symbol.get('delta_e2000')) if value is not None], default=None),
        'color_diff_ratio': max([value for value in (text.get('color_diff_ratio'), symbol.get('color_diff_ratio')) if value is not None], default=None),
        'text_pixel_diff': text.get('pixel_diff_ratio'), 'symbol_edge_iou_min': symbol.get('edge_iou'), 'symbol_pixel_diff': symbol.get('pixel_diff_ratio'),
        'added_component_area_ratio': max(margin.get('added_component_area_ratios') or [0.0]),
        'edit_distance': text.get('edit_distance'),
    }


def evaluate(path, original, label, category, read_ocr, bands):
    homography, matching = sift_homography(path, original)
    result = adapters.logo_diff_pair(path, original, homography, ocr_query=read_ocr(path), ocr_reference=read_ocr(original), thresholds=bands)
    return {'file': path.name, 'set': label, 'category': category, 'decision': result['decision'], 'reason_codes': result.get('reason_codes', []),
            'uncertain_reason_codes': result.get('uncertain_reason_codes', []), 'checks': result.get('checks', {}), 'matching': matching,
            'ecc': (result.get('metrics') or {}).get('ecc', {}).get('status'), 'measurements': measurements(result), 'note': result.get('note')}


def summarize(rows):
    table = {}
    for row in rows:
        key = 'POSITIVE' if row['set'] == 'positive' else row['category']
        counts = table.setdefault(key, {'mismatch': 0, 'uncertain': 0, 'match': 0, 'expected_code_hit': 0, 'total': 0})
        counts[row['decision']] += 1; counts['total'] += 1
        if row['set'] == 'negative' and row['category'] in row['reason_codes']: counts['expected_code_hit'] += 1
    return table


def suggestions(rows):
    positives = [row['measurements'] for row in rows if row['set'] == 'positive']
    suggested = {}
    for key in adapters.LOGO_DIFF_BANDS:
        values = [item[key] for item in positives if item.get(key) is not None]
        if values: suggested[key] = {'positive_worst': min(values) if key.endswith('_min') else max(values), 'current': list(adapters.LOGO_DIFF_BANDS[key])}
    return suggested


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--wrong-dir', type=Path, required=True)
    parser.add_argument('--positives-dir', type=Path, help='실제 영상 crop 등 정답 이미지 폴더(선택)')
    parser.add_argument('--scales', default='100,75,50,25', help='정상 세트 축소율(%%)')
    parser.add_argument('--jpeg', default='90,70', help='정상 세트 JPEG 품질')
    parser.add_argument('--thresholds', type=Path, help='{"키": [uncertain, mismatch]} 형식 JSON. 없으면 현재 기본값')
    parser.add_argument('--ocr', action='store_true', help='로컬 PaddleOCR로 TEXT까지 측정')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    bands = json.loads(args.thresholds.read_text(encoding='utf-8')) if args.thresholds else None
    read_ocr = make_ocr(args.ocr)
    rows = []
    with tempfile.TemporaryDirectory() as temp:
        positives = make_positives(args.original, [float(value) for value in args.scales.split(',')], [int(value) for value in args.jpeg.split(',') if value], Path(temp))
        if args.positives_dir: positives += sorted(path for path in args.positives_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)
        for path in positives:
            rows.append(evaluate(path, args.original, 'positive', 'POSITIVE', read_ocr, bands)); print(f"[positive] {path.name}: {rows[-1]['decision']}", flush=True)
        for path in sorted((path for path in args.wrong_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES), key=lambda item: [int(n) for n in re.findall(r'\d+', item.stem)] or [0]):
            rows.append(evaluate(path, args.original, 'negative', category_of(path), read_ocr, bands)); print(f"[{rows[-1]['category']}] {path.name}: {rows[-1]['decision']} {rows[-1]['reason_codes']}", flush=True)
    report = {'homography': 'sift_ransac', 'ocr': bool(args.ocr), 'bands': {key: list(value) for key, value in {**adapters.LOGO_DIFF_BANDS, **(bands or {})}.items()},
              'summary': summarize(rows), 'suggested': suggestions(rows), 'rows': rows}
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for key, counts in report['summary'].items(): print(key, counts)
    print(f'저장: {args.out}')


if __name__ == '__main__':
    main()
