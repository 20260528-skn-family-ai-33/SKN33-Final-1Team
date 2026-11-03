"""Connect the local review API to the RunPod models through an SSH tunnel."""
import base64
import json
import math
import os
from urllib.parse import urlsplit
import urllib.request

LABELS = ('person', 'food_drink', 'animal', 'plant_flower', 'vehicle',
          'building_structure', 'musical_instrument', 'fireworks')
LABEL_NAMES = dict(zip(LABELS, ('인물', '음식·음료', '동물', '식물·꽃', '탈것', '건물·구조물', '악기', '불꽃놀이')))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirects are not allowed')


def configured():
    return bool(os.getenv('REVIEW_MODELS_URL', '').strip())


def infer(image, criteria):
    url = os.getenv('REVIEW_MODELS_URL', '').rstrip('/')
    parsed = urlsplit(url)
    token = os.getenv('REVIEW_MODELS_TOKEN', '')
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or
            parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or
            len(token) < 32):
        raise ValueError('Set a loopback REVIEW_MODELS_URL and a REVIEW_MODELS_TOKEN of at least 32 characters')
    data = image.read_bytes()
    if not 0 < len(data) <= 20 * 1024 * 1024:
        raise ValueError('Normalized image exceeds the model service limit')
    payload = {'image_base64': base64.b64encode(data).decode('ascii'),
               'criteria': [{'id': item['id'], 'text': item['text']} for item in criteria]}
    req = urllib.request.Request(url + '/infer', data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=900) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('Model response too large')
    result = json.loads(raw)
    scores, extracted = result['image_labels'], result['criteria']
    if set(scores) != set(LABELS) or len(extracted) != len(criteria):
        raise ValueError('Incomplete model response')
    if [row['id'] for row in extracted] != [row['id'] for row in criteria]:
        raise ValueError('Criteria identity changed')
    for label, score in scores.items():
        if (type(score.get('prediction')) is not int or score['prediction'] not in (0, 1) or
            any(type(score.get(k)) not in (float, int) or not math.isfinite(score[k]) or
                not 0 <= score[k] <= 1 for k in ('score', 'threshold')) or
            score['prediction'] != int(score['score'] >= score['threshold'])):
            raise ValueError('Invalid model prediction for ' + label)
    return result


def enrich(results, output):
    """Show class-level evidence without asserting object identity, count or compliance."""
    for result, extraction in zip(results, output['criteria']):
        parsed = extraction.get('value') if extraction.get('valid') is True else None
        if not isinstance(parsed, dict) or not isinstance(parsed.get('items'), list):
            result['model_analysis'] = {'status': 'unavailable', 'message': '요구조건을 해석하지 못했습니다.'}
            result['status'] = 'review'
            result['reason'] += ' 요구조건 모델이 해석하지 못해 원문 확인이 필요합니다.'
            continue
        items = parsed['items']
        candidates = []
        for item in items:
            field = item.get('field')
            if field in output['image_labels']:
                prediction = output['image_labels'][field]
                candidates.append({'label': field, 'target': item.get('target'),
                    'operator': item.get('operator'), **prediction})
        result['model_analysis'] = {'status': 'success', 'conditions': items,
                                    'needs_review': parsed.get('needs_review', []),
                                    'visual_candidates': candidates}
        if parsed.get('needs_review'):
            result['status'] = 'review'
            result['reason'] += ' 요구조건 중 확인이 필요한 내용이 있습니다.'
        if candidates:
            result['status'] = 'review'
            summary = ', '.join(f"{LABEL_NAMES[item['label']]}: " + ('있음 가능성' if item['prediction'] else '없음 가능성')
                                for item in candidates)
            result['reason'] = (f'이미지 분류 참고 결과: {summary}. 분류기는 물체의 종류·수량·위치를 '
                                '확정하지 못하므로 원본과 요구조건을 확인해 주세요.')
    return results
