"""Document conditions and independently observed image evidence. No model imports."""
import hashlib
import json
import math
import re
import unicodedata
from pathlib import Path

LABELS = ['person', 'food_drink', 'animal', 'plant_flower', 'vehicle',
          'building_structure', 'musical_instrument', 'fireworks']
KINDS = {'text_contains', 'image_size', 'image_format', 'file_present',
         'visual_class', 'unsupported'}


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def inside(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('상대 파일 경로가 필요합니다: ' + str(relative))
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('입력 폴더 밖의 경로입니다: ' + relative)
    return path


def validate_packet(root):
    root = Path(root)
    packet = read(root / 'packet.json')
    if packet.get('schema') != 'document-image-review-v1' or not packet.get('cases'):
        raise ValueError('지원하는 조건 패킷이 아니거나 사례가 없습니다.')
    files = packet.get('files', {})
    if not isinstance(files, dict) or not files:
        raise ValueError('공개 파일 해시 목록이 없습니다.')
    for relative, digest in files.items():
        path = inside(root, relative)
        if not valid_sha(digest) or not path.is_file() or sha(path) != digest:
            raise ValueError('파일 누락 또는 SHA-256 불일치: ' + relative)
    ids = set()
    for case in packet['cases']:
        if not case.get('id') or case['id'] in ids or not case.get('group_id'):
            raise ValueError('사례 ID/원본 그룹이 없거나 중복됩니다.')
        ids.add(case['id'])
        image = case['image']
        if image.get('path') not in files or not valid_sha(image.get('sha256')) or files[image['path']] != image['sha256']:
            raise ValueError('이미지와 패킷 파일 목록의 해시가 다릅니다.')
        for item in case.get('delivery_files', []):
            if not item.get('name') or item.get('path') not in files or not valid_sha(item.get('sha256')) or files[item['path']] != item['sha256']:
                raise ValueError('납품 파일 근거가 잘못되었습니다.')
        condition_ids = set()
        for condition in case['conditions']:
            if not condition.get('id') or condition['id'] in condition_ids:
                raise ValueError('조건 ID가 없거나 중복됩니다: ' + case['id'])
            condition_ids.add(condition['id'])
            if condition.get('level') not in ('required', 'recommended', 'optional'):
                raise ValueError('조건의 필수/권장/선택 구분이 필요합니다.')
            source = condition['source']
            if (source.get('path') not in files or not valid_sha(source.get('sha256')) or
                files[source['path']] != source['sha256'] or not source.get('quote') or not source.get('locator')):
                raise ValueError('조건 출처/인용문/위치가 없습니다.')
            text = inside(root, source['path']).read_text(encoding='utf-8')
            if source['quote'] not in text:
                raise ValueError('조건의 인용문을 원문에서 찾을 수 없습니다: ' + condition['id'])
            if not condition.get('requirement') or not condition.get('checks'):
                raise ValueError('요구 문장 또는 검사 항목이 없습니다.')
            for check in condition['checks']:
                kind = check.get('kind')
                if kind not in KINDS:
                    raise ValueError('지원하지 않는 검사 종류: ' + str(kind))
                if kind == 'text_contains':
                    if not isinstance(check.get('expected'), str) or not check['expected'].strip():
                        raise ValueError('문구 조건의 기대값이 없습니다.')
                    if check.get('normalization', 'space') not in ('space', 'price', 'time'):
                        raise ValueError('지원하지 않는 문구 정규화입니다.')
                if kind == 'visual_class' and (check.get('label') not in LABELS or type(check.get('expected')) is not bool):
                    raise ValueError('시각 분류 조건은 8개 범주의 존재 여부만 지원합니다.')
                if kind == 'image_size' and (not isinstance(check.get('expected'), list) or len(check['expected']) != 2 or
                                             any(type(n) is not int or n <= 0 for n in check['expected'])):
                    raise ValueError('이미지 규격은 양의 정수 [너비, 높이]여야 합니다.')
                if kind == 'image_format' and not isinstance(check.get('expected'), str):
                    raise ValueError('파일 형식의 기대값이 없습니다.')
                if kind == 'file_present':
                    exact, extensions = check.get('expected'), check.get('extensions')
                    if bool(exact) == bool(extensions):
                        raise ValueError('납품파일은 정확한 이름 또는 확장자 목록 한 가지로 지정하세요.')
                    if exact and (not isinstance(exact, str) or Path(exact).name != exact):
                        raise ValueError('납품파일 기대값은 파일명이어야 합니다.')
                    if extensions and (not isinstance(extensions, list) or not all(isinstance(x, str) and x.startswith('.') for x in extensions)):
                        raise ValueError('잘못된 확장자 목록입니다.')
    return packet


def normalized(text, policy):
    text = re.sub(r'\s+', '', unicodedata.normalize('NFC', text))
    if policy == 'price':
        text = text.replace(',', '')
    if policy == 'time':
        text = re.sub('[–—−~∼〜]', '-', text)
    return text


def _result(verdict, reason, evidence=None, error=False):
    return {'execution_status': 'error' if error else 'success', 'verdict': verdict,
            'reason': reason, 'evidence': evidence or []}


def check_one(check, case, observed, min_ocr_confidence=.90, visual_margin=.10):
    kind = check['kind']
    if kind == 'unsupported':
        return _result('review', check.get('reason', '현재 관측 도구로 확인할 수 없는 조건입니다.'))
    if kind == 'file_present':
        inventory = case.get('delivery_files', [])
        matches = [f for f in inventory if (f['name'] == check.get('expected') if check.get('expected')
                   else Path(f['name']).suffix.lower() in check['extensions'])]
        if matches:
            # File existence does not prove editability or image identity.
            return _result('pass', '지정한 종류 또는 이름의 파일이 접수 목록에 있습니다.', matches)
        if case.get('delivery_inventory_complete') is not True:
            return _result('review', '접수 파일 목록이 완전한지 확인되지 않았습니다.')
        if check.get('extensions') and any(Path(f['name']).suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp') for f in inventory):
            return _result('review', '다른 형식의 납품물이 있어 역할과 편집 가능 여부 확인이 필요합니다.', inventory)
        return _result('fail', '완전한 접수 목록에서 지정 파일을 찾지 못했습니다.',
                       [{'type': 'delivery_inventory', 'files': inventory, 'complete': True}])
    if observed is None:
        return _result(None, '이미지 관측 결과가 없습니다.', error=True)
    if kind in ('image_size', 'image_format'):
        metadata = observed.get('metadata') or {}
        if not isinstance(metadata, dict):
            return _result(None, '원본 파일 속성 형식이 올바르지 않습니다.', error=True)
        actual = [metadata.get('width'), metadata.get('height')] if kind == 'image_size' else metadata.get('format')
        if actual is None or (isinstance(actual, list) and None in actual):
            return _result(None, '원본 파일 메타데이터를 읽지 못했습니다.', error=True)
        expected = check['expected']
        if kind == 'image_format':
            actual, expected = actual.upper(), expected.upper()
        return _result('pass' if actual == expected else 'fail', '원본 파일 속성과 문서의 지정값을 비교했습니다.',
                       [{'type': 'metadata', 'observed': actual, 'expected': expected}])
    if kind == 'visual_class':
        visual = observed.get('visual') or {}
        if not isinstance(visual, dict) or visual.get('status') != 'success':
            return _result(None, '시각 모델 실행 실패 또는 관측 누락입니다.', error=True)
        labels = visual.get('labels') or {}
        item = labels.get(check['label']) if isinstance(labels, dict) else None
        if not isinstance(item, dict) or any(type(item.get(k)) not in (int, float) or not math.isfinite(item[k]) or not 0 <= item[k] <= 1 for k in ('score', 'threshold')):
            return _result(None, '시각 분류 점수 또는 기준값이 유효하지 않습니다.', error=True)
        if type(item.get('prediction')) is not int or item['prediction'] != int(item['score'] >= item['threshold']):
            return _result(None, '시각 분류의 점수와 판정이 일치하지 않습니다.', error=True)
        evidence = [{'type': 'visual_class', 'label': check['label'], **item}]
        if abs(item['score'] - item['threshold']) < visual_margin:
            return _result('review', '시각 분류 점수가 판정 경계에 가깝습니다.', evidence)
        return _result('pass' if bool(item['prediction']) == check['expected'] else 'fail',
                       '대분류 존재 여부만 비교한 자동 판정입니다. 종류·수량·위치의 근거가 아닙니다.', evidence)
    if kind == 'text_contains':
        ocr = observed.get('ocr') or {}
        if not isinstance(ocr, dict) or ocr.get('status') != 'success':
            return _result(None, 'OCR 실행 실패 또는 관측 누락입니다.', error=True)
        policy = check.get('normalization', 'space')
        wanted = normalized(check['expected'], policy)
        records = ocr.get('records')
        if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
            return _result(None, 'OCR 영역 목록이 올바르지 않습니다.', error=True)
        good = [r for r in records if isinstance(r.get('text'), str) and type(r.get('confidence')) in (int, float)
                and math.isfinite(r['confidence']) and min_ocr_confidence <= r['confidence'] <= 1]
        for record in good:
            if wanted in normalized(record['text'], policy):
                return _result('pass', 'OCR의 한 영역에서 지정 문구를 확인했습니다.', [{'type': 'ocr', **record}])
        # Do not concatenate arbitrary boxes: it can invent a name across unrelated regions.
        return _result('review', '지정 문구를 충분한 신뢰도의 OCR 영역에서 확인하지 못했습니다. 부재를 확정하지 않습니다.',
                       [{'type': 'ocr', **r} for r in records])
    raise ValueError('지원하지 않는 검사 종류입니다.')


def compare(packet, observations, packet_sha256, min_ocr_confidence=.90, visual_margin=.10):
    if observations.get('schema') != 'document-image-observations-v1' or observations.get('packet_sha256') != packet_sha256:
        raise ValueError('관측 결과가 이 조건 패킷과 일치하지 않습니다.')
    expected = {case['id']: case for case in packet['cases']}
    indexed = {}
    for row in observations.get('cases', []):
        key = row.get('case_id')
        if key not in expected or key in indexed or row.get('image_sha256') != expected[key]['image']['sha256']:
            raise ValueError('관측 사례 ID 또는 이미지 해시가 잘못되었습니다.')
        indexed[key] = row
    results = []
    for case in packet['cases']:
        for condition in case['conditions']:
            checks = [check_one(c, case, indexed.get(case['id']), min_ocr_confidence, visual_margin) for c in condition['checks']]
            if any(c['verdict'] == 'fail' for c in checks):
                outcome = _result('fail', '필수로 함께 만족해야 하는 검사 중 불일치가 있습니다.')
            elif any(c['execution_status'] == 'error' for c in checks):
                outcome = _result(None, '필요한 관측을 실행하지 못해 판정하지 않았습니다.', error=True)
            elif any(c['verdict'] == 'review' for c in checks):
                outcome = _result('review', '일부 조건의 근거가 부족하거나 지원하지 않는 검사입니다.')
            else:
                outcome = _result('pass', '명시된 검사 항목이 모두 일치합니다.')
            outcome['evidence'] = [e for c in checks for e in c['evidence']]
            results.append({'case_id': case['id'], 'condition_id': condition['id'],
                            'image_sha256': case['image']['sha256'], 'level': condition['level'],
                            'requirement': condition['requirement'], **outcome, 'checks': checks})
    return {'schema': 'document-image-predictions-v1', 'packet_sha256': packet_sha256,
            'policy': {'min_ocr_confidence': min_ocr_confidence, 'visual_margin': visual_margin,
                       'thresholds_calibrated_for_document_review': False,
                       'unrecognized_text_is_absence': False}, 'results': results}
