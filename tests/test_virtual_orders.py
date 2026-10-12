"""가상 발주 3건(data/virtual-orders)을 앱 주제로 연결한 설정 검증.

정답 좌표를 '글자를 정확히 읽는 OCR'이 읽은 것처럼 가정하고, 새 주제의 규칙 설정(영역·정규화)이
기대 판정과 맞는지만 확인한다. 실제 OCR 성능이나 일반화 성능을 검증하는 테스트가 아니다.
"""
import hashlib
import json
from pathlib import Path

import pytest

from app.inspector import inspect_text_rules
from app.topics import TopicCatalog

ROOT = Path(__file__).resolve().parents[1]
VO = ROOT / 'data' / 'virtual-orders'
TOPICS = {'fnb-ongi-v1': 'VO-01-fnb', 'festival-dalbit-v1': 'VO-02-festival', 'virtual-seoha-v1': 'VO-03-virtual-person'}

pytestmark = pytest.mark.skipif(not VO.exists(), reason='data/virtual-orders 가 없습니다')


def catalog():
    return TopicCatalog(ROOT / 'data/criteria.json', ROOT / 'data/samples', ROOT / 'data/topics')


def gold(slug, name):
    return json.loads((VO / slug / 'gold' / name).read_text(encoding='utf-8'))


def perfect_ocr_lines(obs):
    """선명한(clear) 글자만 읽은 것으로 본다. 사람도 확정하기 어려운 글자는 OCR이 읽지 못한 것으로 둔다."""
    w, h = obs['file']['w'], obs['file']['h']
    return [{'text': r['transcription_raw'], 'bbox': [r['bbox'][0] / w, r['bbox'][1] / h, (r['bbox'][2] - r['bbox'][0]) / w, (r['bbox'][3] - r['bbox'][1]) / h],
             'confidence': 1} for r in obs['regions'] if r['legibility'] == 'clear']


def expected_app_verdict(judgment):
    # 앱은 '글자가 없음'과 'OCR이 못 읽음'을 구분하지 못해 둘 다 확인 필요로 둔다.
    if judgment['judgment'] == '미충족' and judgment['reason_code'] == 'text_missing':
        return '확인 필요'
    return judgment['judgment']


def test_new_topics_are_listed_with_six_samples():
    topics = {t['id']: t for t in catalog().list()}
    for topic in TOPICS:
        assert topics[topic]['sample_count'] == 6


@pytest.mark.parametrize('topic', TOPICS)
def test_rules_are_well_formed_and_regions_inside_image(topic):
    rules = catalog().criteria(topic)['rules']
    assert len({r['id'] for r in rules}) == len(rules)
    for rule in rules:
        assert rule['inspection']['kind'] in ('text', 'manual')
        if rule['inspection']['kind'] == 'text':
            x, y, w, h = rule['inspection']['region']
            assert 0 <= x and 0 <= y and w > 0 and h > 0 and x + w <= 1.0001 and y + h <= 1.0001


@pytest.mark.parametrize('topic', TOPICS)
def test_sample_images_are_unchanged_copies_of_deliveries(topic):
    slug = TOPICS[topic]
    inp = json.loads((VO / slug / 'input' / 'case_input.json').read_text(encoding='utf-8'))
    for n, delivery in enumerate(inp['deliveries'][:6], 1):
        copied = (ROOT / 'data/topics' / topic / 'images' / f'sample-{n:02d}.png').read_bytes()
        assert hashlib.sha256(copied).hexdigest() == delivery['sha256']


@pytest.mark.parametrize('topic', TOPICS)
def test_text_rules_match_gold_when_ocr_reads_clear_text(topic):
    slug = TOPICS[topic]
    rules = catalog().criteria(topic)['rules']
    obs = gold(slug, 'observations.json')['deliveries']
    judgments = {(j['delivery_id'], j['criterion_id']): j for j in gold(slug, 'judgments.json')['judgments']}
    for n in range(1, 7):
        delivery = f'D{n}'
        results = inspect_text_rules(perfect_ocr_lines(obs[delivery]), rules)
        for rule, result in zip(rules, results):
            if rule['inspection']['kind'] == 'manual':
                assert result['verdict'] == '확인 필요' and result['execution_status'] == 'not_run'
                continue
            want = expected_app_verdict(judgments[(delivery, rule['source_criterion'])])
            assert result['verdict'] == want, (topic, delivery, rule['id'], rule['name'], result['observed'], result['reason'])


@pytest.mark.parametrize('topic', TOPICS)
def test_no_text_is_read_when_ocr_finds_nothing(topic):
    rules = catalog().criteria(topic)['rules']
    results = inspect_text_rules([], rules)
    assert all(r['verdict'] == '확인 필요' for r in results)
