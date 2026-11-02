"""조건 종류와 기존 OCR/화소의 기하로 영역을 제안해 Paddle로 다시 읽는다.

기대 문자열은 OCR 모델에 전달하지 않는다. 새 후보는 원문·변환·점수를 보존하고
자동 확정하지 않는다. 흰 곡선 글자 탐색은 원호형 제목에 제한된 휴리스틱이다.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import subprocess
import time

from PIL import Image

from .document_conditions import attached_condition, plain_condition, field_for_label
from .paddle_ocr import paddle_paths, worker_environment
from .ocr_runtime import PROJECT_ROOT
from .review_rules import review_criteria
from .structured_review import compare
from scripts.prepare_paddle import MODEL, ENGLISH_MODEL, verify_model

MAX_CROPS = 8
RETRY_TIMEOUT = 90
LANGUAGES = {'ko': MODEL, 'en': ENGLISH_MODEL}


def white_mask(pixels):
    import numpy as np
    return ((pixels.min(2) > 170) & ((pixels.max(2).astype(float) - pixels.min(2)) < 75)).astype('uint8') * 255


def _box(points):
    return [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]


def line_views(image, record, kind):
    import cv2
    import numpy as np
    box = record['bbox_xyxy']
    padding = max(4, min(16, round((box[3] - box[1]) * .022)))
    region = [max(0, math.floor(box[0]) - padding), max(0, math.floor(box[1]) - padding),
              min(image.width, math.ceil(box[2]) + padding), min(image.height, math.ceil(box[3]) + padding)]
    pixels = np.asarray(image.crop(region).convert('RGB'))
    if kind == 'year':
        settings = [('rgb', 1, .6), ('rgb', 1, .4)]
    else:
        mask = white_mask(pixels)
        active = np.where((mask > 0).sum(1) > pixels.shape[1] * .015)[0]
        if not len(active):
            return []
        groups = np.split(active, np.where(np.diff(active) > 8)[0] + 1)
        main = max(groups, key=len)
        pad = max(2, round(pixels.shape[0] * .009))
        top, bottom = max(0, int(main[0]) - pad), min(len(mask), int(main[-1]) + pad + 1)
        pixels, mask = pixels[top:bottom], mask[top:bottom]
        region[1] += top
        region[3] = region[1] + len(mask)
        settings = [('mask', 1, .4), ('mask', max(1, round(len(mask) * .011)), .6),
                    ('mask', max(1, round(len(mask) * .02)), .4)]
    views = []
    for mode, dilation, squash in settings:
        if mode == 'mask':
            ink = cv2.dilate(mask, np.ones((dilation, 1), np.uint8))
            output = np.repeat((255 - ink)[:, :, None], 3, axis=2)
        else:
            output = pixels
        output = cv2.resize(output, (output.shape[1], max(1, round(output.shape[0] * squash))), interpolation=cv2.INTER_CUBIC)
        views.append((Image.fromarray(output), {'kind': kind, 'language': 'ko', 'source_ocr_id': record['id'],
                      'source_box': region, 'mode': mode, 'vertical_dilation': dilation, 'vertical_scale': squash,
                      'bbox_xyxy': record['bbox_xyxy'], 'polygon': record.get('polygon')}))
    return views


def arc_views(image, records):
    import cv2
    import numpy as np
    width, height = image.size
    anchors = [r for r in records if len(re.findall('[A-Za-z]', r['text'])) >= 2
               and (r.get('confidence') or 0) < .9 and r['bbox_xyxy'][3] - r['bbox_xyxy'][1] > height * .025]
    anchors = sorted(anchors, key=lambda r: -(r['bbox_xyxy'][2]-r['bbox_xyxy'][0]) * (r['bbox_xyxy'][3]-r['bbox_xyxy'][1]))[:2]
    if len(anchors) < 2:
        return [], {'status': 'no_arc_anchors'}
    start = max(0, int(min(r['bbox_xyxy'][1] for r in anchors) - .4 * max(r['bbox_xyxy'][3] - r['bbox_xyxy'][1] for r in anchors)))
    mask = white_mask(np.asarray(image.convert('RGB')))
    mask[:start] = 0
    _, _, stats, centroids = cv2.connectedComponentsWithStats(mask)
    components = [(index, centroids[index], row) for index, row in enumerate(stats[1:], 1)
                  if height * .018 < row[3] < height * .18 and row[4] > width * height * .00012 and row[2] > width * .008]
    if len(components) < 8:
        return [], {'status': 'insufficient_arc_components'}
    components = sorted(sorted(components, key=lambda item: -item[2][4])[:64], key=lambda item: item[0])
    points = np.array([c[1] for c in components])
    triples = list(itertools.combinations(range(len(points)), 3))
    if len(triples) > 8000:
        chosen = np.random.default_rng(0).choice(len(triples), 8000, replace=False)
        triples = [triples[i] for i in sorted(chosen)]
    best = None
    for indices in triples:
        triple = points[list(indices)]
        try:
            center = np.linalg.solve(2 * (triple[1:] - triple[0]), (triple[1:] ** 2).sum(1) - (triple[0] ** 2).sum())
        except np.linalg.LinAlgError:
            continue
        radius = np.linalg.norm(triple[0] - center)
        if not (0 < center[0] < width and start < center[1] < 1.5 * height and .15 * width < radius < height):
            continue
        residual = np.abs(np.linalg.norm(points - center, axis=1) - radius)
        good = residual < max(8, round(height * .009))
        score = (int(good.sum()), -float(residual[good].mean())) if good.any() else (0, 0)
        if best is None or score > best[0]:
            best = (score, center, radius, good)
    if best is None or best[0][0] < 8:
        return [], {'status': 'no_supported_circle'}
    selected = points[best[3]]
    solution = np.linalg.lstsq(np.column_stack([2 * selected, np.ones(len(selected))]), (selected ** 2).sum(1), rcond=None)[0]
    center = solution[:2]
    radius = float(np.sqrt(solution[2] + (center ** 2).sum()))
    angles = np.arctan2(selected[:, 1] - center[1], selected[:, 0] - center[0])
    order = np.argsort(angles)
    groups = np.split(order, np.where(np.diff(angles[order]) > .3)[0] + 1)
    group = max(groups, key=len)
    if len(group) < 8:
        return [], {'status': 'no_continuous_arc'}
    low, high = float(angles[group].min() - .11), float(angles[group].max() + .11)
    if not .7 < high - low < 3.3:
        return [], {'status': 'unsupported_arc_span'}
    heights = [components[i][2][3] for i in np.where(best[3])[0][group]]
    band = max(80, min(480, round(float(np.median(heights)) * 1.3 / 16) * 16))
    pixels = np.asarray(image.convert('RGB'))
    views = []
    for thickness in (band, round(band * 4 / 3 / 16) * 16):
        out_width = min(4096, max(1, round(radius * (high - low))))
        theta = np.linspace(low, high, out_width)[None, :]
        distance = np.linspace(radius + thickness / 2, radius - thickness / 2, thickness)[:, None]
        mx = (center[0] + distance * np.cos(theta)).astype('float32')
        my = (center[1] + distance * np.sin(theta)).astype('float32')
        unwrapped = cv2.remap(pixels, mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(255,255,255))
        polygon = [[max(0., min(float(width), float(center[0] + distance * math.cos(angle)))),
                    max(0., min(float(height), float(center[1] + distance * math.sin(angle))))]
                   for distance, arc in [(radius + thickness / 2, np.linspace(low, high, 12)),
                                         (radius - thickness / 2, np.linspace(high, low, 12))] for angle in arc]
        views.append((Image.fromarray(unwrapped), {'kind': 'arc', 'language': 'en', 'source_ocr_id': None,
                      'center': center.tolist(), 'radius': radius, 'angle_range': [low, high], 'band': thickness,
                      'bbox_xyxy': _box(polygon), 'polygon': polygon}))
    return views, {'status': 'found', 'anchor_ids': [r['id'] for r in anchors], 'component_inliers': len(group)}


def plan(image, records, criteria, source):
    base = compare(criteria, review_criteria(criteria, records, multiline=True), records, source)
    needs = []
    for criterion, result in zip(criteria, base):
        if result['status'] == 'pass' or criterion['type'] != 'information':
            continue
        parsed = attached_condition(criterion) or plain_condition(criterion)
        if parsed:
            field = field_for_label(parsed['label'])
            needs.append((criterion['id'], field, parsed['label'], bool(re.search(r'\d{4}', parsed['requirement']))))
    targets = {field for _, field, _, _ in needs}
    views, discovery = [], {}
    if 'title' in targets:
        candidates = [r for r in records if len(re.findall('[가-힣]', r['text'])) >= 3 and len(r['text']) <= 40]
        if candidates:
            candidate = max(candidates, key=lambda r: (r['bbox_xyxy'][2]-r['bbox_xyxy'][0]) * (r['bbox_xyxy'][3]-r['bbox_xyxy'][1]))
            views.extend(line_views(image, candidate, 'title'))
    if any(year and field in {'title','date'} for _,field,_,year in needs):
        candidates = [r for r in records if re.fullmatch(r'\s*\d{4}\s*', r['text'])]
        if candidates:
            candidate = max(candidates, key=lambda r: (r['bbox_xyxy'][2]-r['bbox_xyxy'][0]) * (r['bbox_xyxy'][3]-r['bbox_xyxy'][1]))
            views.extend(line_views(image, candidate, 'year'))
    if any('영문' in label for _,_,label,_ in needs):
        curve, discovery = arc_views(image, records)
        views.extend(curve)
    return views[:MAX_CROPS], {'condition_ids': [item[0] for item in needs], 'arc_discovery': discovery}


def read_crops(crops, directory, timeout):
    python, _, _ = paddle_paths()
    model_root = PROJECT_ROOT / '.runtime/paddle-models'
    manifest = [{'id':c['id'], 'path':str(c['path']), 'language':c['language']} for c in crops]
    (directory/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with (directory/'worker.log').open('w', encoding='utf-8') as log:
        subprocess.run([str(python), '-m', 'backend.app.paddle_condition_worker', '--manifest', str(directory/'manifest.json'),
                        '--output', str(directory/'raw.json'), '--model-root', str(model_root)], cwd=PROJECT_ROOT,
                       env=worker_environment(), timeout=timeout, check=True, stdout=log, stderr=subprocess.STDOUT)
    raw = json.loads((directory/'raw.json').read_text(encoding='utf-8'))
    if raw.get('device') != 'cpu' or [r['id'] for r in raw.get('outputs',[])] != [c['id'] for c in crops]:
        raise ValueError('Conditional Paddle crop output mismatch')
    for language in {c['language'] for c in crops}:
        if raw['model_identity'][language] != verify_model(model_root/LANGUAGES[language], LANGUAGES[language]):
            raise ValueError('Conditional Paddle model identity mismatch')
    for crop, result in zip(crops, raw['outputs']):
        if (result['image_sha256'] != crop['image_sha256'] or result['input_dimensions'] != crop['dimensions']
                or not isinstance(result['text'], str) or len(result['text']) > 2000
                or type(result['confidence']) not in (int,float) or not math.isfinite(result['confidence'])
                or not 0 <= result['confidence'] <= 1):
            raise ValueError('Invalid conditional Paddle recognition')
    return raw


def refine(image_path, records, criteria, source, directory, *, read=read_crops, check_models=True):
    started = time.monotonic()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    with Image.open(image_path) as image:
        views, metadata = plan(image.convert('RGB'), records, criteria, source)
    crops, skipped = [], []
    for index, (image, transform) in enumerate(views):
        language = transform['language']
        if check_models:
            try:
                verify_model(PROJECT_ROOT/'.runtime/paddle-models'/LANGUAGES[language], LANGUAGES[language])
            except (OSError,ValueError,KeyError,TypeError):
                skipped.append({'kind':transform['kind'],'status':'missing_model','model':LANGUAGES[language]})
                image.close()
                continue
        identifier = f'condition-crop-{index:02d}'
        path = directory/(identifier+'.png')
        dimensions = list(image.size)
        image.save(path)
        image.close()
        crops.append({'id':identifier,'path':path.resolve(),'language':language,'transform':transform,
                      'dimensions':dimensions,'image_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    (directory/'plan.json').write_text(json.dumps([{**c,'path':str(c['path'])} for c in crops],ensure_ascii=False,indent=2),encoding='utf-8')
    original = copy.deepcopy(records)
    summary = {'version':'conditional_paddle_v1','status':('unavailable' if skipped else 'not_needed') if not crops else 'success',
               'discovery':metadata,'skipped':skipped,'attempts':[], 'original_records':len(records)}
    if crops:
        try:
            raw = read(crops,directory,RETRY_TIMEOUT)
            if [r.get('id') for r in raw.get('outputs',[])] != [c['id'] for c in crops]:
                raise ValueError('Conditional output IDs do not match the crop plan')
            for crop, observed in zip(crops,raw['outputs']):
                if (not isinstance(observed.get('text'),str) or len(observed['text'])>2000
                        or type(observed.get('confidence')) not in (int,float)
                        or not math.isfinite(observed['confidence']) or not 0<=observed['confidence']<=1):
                    raise ValueError('Invalid conditional observation')
                summary['attempts'].append({'id':crop['id'],'text':observed['text'],'confidence':observed['confidence'],
                    'language':crop['language'],'model':LANGUAGES[crop['language']],**crop['transform']})
            groups = {}
            for attempt in summary['attempts']:
                if attempt['text'].strip():groups.setdefault((attempt['kind'],attempt['source_ocr_id']),[]).append(attempt)
            for (kind,source_id),attempts in groups.items():
                selected = max(attempts,key=lambda a:a['confidence'])
                if source_id is not None:
                    target = next(r for r in original if r['id']==source_id)
                    target.update(baseline_text=target['text'],baseline_confidence=target.get('confidence'),
                                  baseline_recognition_engine=target.get('recognition_engine','paddleocr'),
                                  baseline_requires_review=bool(target.get('requires_review')))
                else:
                    target={'id':f'conditional-{kind}', 'baseline_text':'','baseline_confidence':None,
                            'baseline_recognition_engine':'paddleocr','baseline_requires_review':True}
                    original.append(target)
                target.update(text=selected['text'].strip(),confidence=selected['confidence'],
                    bbox_xyxy=selected['bbox_xyxy'],polygon=selected['polygon'] or [[selected['bbox_xyxy'][0],selected['bbox_xyxy'][1]],
                        [selected['bbox_xyxy'][2],selected['bbox_xyxy'][1]],[selected['bbox_xyxy'][2],selected['bbox_xyxy'][3]],
                        [selected['bbox_xyxy'][0],selected['bbox_xyxy'][3]]],recognition_engine='paddleocr',requires_review=True,
                    selection_reason='new_model_candidate_unverified',alternatives=[{'text':a['text'],'confidence':a['confidence'],
                        'bbox_xyxy':a['bbox_xyxy'],'engine':'paddleocr','attempt_id':a['id']} for a in attempts])
            summary['model_identity']=raw.get('model_identity',{})
        except Exception as error:
            original=copy.deepcopy(records)
            summary.update(status='failed',error_type=type(error).__name__)
    summary['elapsed_seconds']=round(time.monotonic()-started,3)
    (directory/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return original, summary
