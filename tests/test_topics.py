"""Topic isolation and predeclared order selection at the API boundary."""
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.topics import DEFAULT_TOPIC, TopicCatalog
from app.inspector import inspect_text_rules

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path, monkeypatch):
    def inspect(path, rules):
        return {'results': inspect_text_rules([], rules), 'trace': [], 'method': 'test-only'}
    monkeypatch.setitem(sys.modules, 'app.mcp_client', SimpleNamespace(inspect_via_mcp=inspect))
    app = create_app(tmp_path / 'runtime')
    app.state.store.prepare()
    with TestClient(app, base_url='http://localhost') as c:
        c.headers['X-CSRFToken'] = c.get('/api/csrf').json()['csrf_token']
        yield c


def criteria(client, topic, condition=None):
    params = {'topic_id': topic}
    if condition:
        params['condition_id'] = condition
    return client.get('/api/criteria', params=params).json()


def confirm(client, topic, condition=None):
    current = criteria(client, topic, condition)
    result = client.post('/api/criteria/confirm', json={
        'version': current['version'], 'topic_id': topic, 'condition_id': condition})
    assert result.status_code == 200
    return result.json()


def test_three_topics_eighteen_images_and_legacy_default(client):
    topics = client.get('/api/topics').json()
    assert [(t['id'], t['sample_count']) for t in topics] == [
        (DEFAULT_TOPIC, 6), ('festival-v1', 6), ('croissant-v2', 6)]
    assert client.get('/api/criteria').json()['rules'][0]['expected'] == '딸기라떼'
    for topic in topics:
        samples = client.get('/api/samples', params={'topic_id': topic['id']}).json()
        assert len(samples) == 6
        with Image.open(io.BytesIO(client.get(samples[0]['url']).content)) as image:
            assert image.size == (1254, 1254)
    assert client.get('/api/criteria?topic_id=../../reviewer').status_code == 404
    assert client.get('/api/samples/sample-01/image?topic_id=../festival-v1').status_code == 404


def test_croissant_requires_predeclared_order_and_does_not_infer_from_filename(client):
    topic = 'croissant-v2'
    unset = criteria(client, topic)
    assert unset['can_confirm'] is False
    assert client.post('/api/criteria/confirm', json={'topic_id': topic, 'version': unset['version']}).status_code == 409
    raw = (ROOT / 'data/topics/croissant-v2/images/sample-01.png').read_bytes()
    assert client.post('/api/assets', data={'topic_id': topic}, files={'file': ('sample-01.png', raw, 'image/png')}).status_code == 409
    result = client.post('/api/assets', data={'topic_id': topic, 'condition_id': 'sample-04'}, files={'file': ('sample-01.png', raw, 'image/png')})
    asset = result.json()
    assert result.status_code == 200 and asset['condition_id'] == 'sample-04'
    assert asset['sample_id'] is None
    current = confirm(client, topic, 'sample-04')
    assert current['rules'][3]['expected_counts'] == {'large': 2, 'mini': 3, 'total': 5}
    other = confirm(client, topic, 'sample-01')
    request = {'asset_id': asset['id'], 'criteria_version': other['version'], 'idempotency_key': 'wrong-order'}
    assert client.post('/api/runs', json=request).status_code == 409
    run = client.post('/api/runs', json={**request, 'criteria_version': current['version'], 'idempotency_key': 'right-order'}).json()
    assert run['status'] == 'partial'
    assert run['criteria_snapshot']['rules'][3]['expected_counts']['large'] == 2
    assert run['results'][3]['execution_status'] == 'not_run'


def test_sample_order_binding_confirmation_isolation_and_snapshots(client):
    topic = 'croissant-v2'
    a = client.post('/api/assets/from-sample', json={'topic_id': topic, 'sample_id': 'sample-01'}).json()
    b = client.post('/api/assets/from-sample', json={'topic_id': topic, 'sample_id': 'sample-06'}).json()
    assert a['condition_id'] == 'sample-01' and b['condition_id'] == 'sample-06'
    c = confirm(client, topic, 'sample-01')
    assert criteria(client, topic, 'sample-06')['confirmed'] is False
    request = {'asset_id': a['id'], 'criteria_version': c['version'], 'idempotency_key': 'a'}
    assert client.post('/api/runs', json={**request, 'topic_id': 'festival-v1'}).status_code == 409
    run = client.post('/api/runs', json=request).json()
    assert run['criteria_snapshot']['rules'][3]['expected_counts'] == {'large': 1, 'mini': 5, 'total': 6}
    assert client.post('/api/runs', json=request).json()['id'] == run['id']
    assert client.post('/api/runs', json={**request, 'asset_id': b['id']}).status_code == 409
    confirm(client, topic, 'sample-06')
    saved = client.get('/api/runs/' + run['id']).json()
    assert saved['condition_id'] == 'sample-01'
    assert saved['criteria_snapshot']['rules'][3]['expected_counts']['mini'] == 5


def line(text, y, x=.15, width=.6, height=.05, confidence=1):
    return {'text': text, 'bbox': [x,y,width,height], 'confidence': confidence}


def test_festival_fields_read_actual_text_including_wrong_values():
    catalog = TopicCatalog(ROOT/'data/criteria.json', ROOT/'data/samples', ROOT/'data/topics')
    rules = catalog.criteria('festival-v1')['rules']
    lines = [line('노을 사운드', .04), line('페스티벌', .17), line('2026.11.21', .30),
             line('16:00—21:00', .37), line('노을빛 공원', .82), line('얼리버드 39,000원', .93)]
    results = inspect_text_rules(lines, rules)
    assert [r['verdict'] for r in results] == ['충족', '미충족', '충족', '충족', '충족']
    assert all(r['execution_status'] == 'succeeded' for r in results)
    assert all(r['verdict'] == '확인 필요' for r in inspect_text_rules([], rules))
    lines.append(line('2026.11.14', .31))
    assert inspect_text_rules(lines, rules)[1]['verdict'] == '확인 필요'


def test_croissant_fragments_changed_brand_and_overlapping_ocr_are_not_corrected():
    catalog = TopicCatalog(ROOT/'data/criteria.json', ROOT/'data/samples', ROOT/'data/topics')
    rules = catalog.criteria('croissant-v2', 'sample-01')['rules']
    lines = [line('버터 크루아상', .17, .1, .5), line('세트', .16, .65, .25),
             line('7,900원', .31), line('BUTTER', .07, .3, .18, .03), line('MARKET', .068, .5, .2, .03)]
    results = inspect_text_rules(lines, rules)
    assert [r['verdict'] for r in results] == ['충족', '충족', '미충족', '확인 필요', '확인 필요']
    lines[1] = line('상세트', .17, .55, .25)
    assert inspect_text_rules(lines, rules)[0]['verdict'] == '확인 필요'


@pytest.mark.parametrize('price,verdict', [('39000원','충족'), ('39,000원','충족'), ('49000원','미충족'), ('139,000원','미충족'), ('39,O00원','확인 필요')])
def test_price_comparison_preserves_digits_and_never_extracts_partial_number(price, verdict):
    catalog = TopicCatalog(ROOT/'data/criteria.json', ROOT/'data/samples', ROOT/'data/topics')
    rules = catalog.criteria('festival-v1')['rules']
    result = inspect_text_rules([line('얼리버드 ' + price, .93)], rules)[4]
    assert result['verdict'] == verdict


def test_date_and_time_separators_preserve_observed_numbers():
    catalog = TopicCatalog(ROOT/'data/criteria.json', ROOT/'data/samples', ROOT/'data/topics')
    rules = catalog.criteria('festival-v1')['rules']
    result = inspect_text_rules([line('20261114', .3), line('16:00--21:00', .37)], rules)
    assert [result[i]['verdict'] for i in (1,2)] == ['충족','충족']
    wrong = inspect_text_rules([line('20261121', .3), line('17:00--22:00', .37)], rules)
    assert [wrong[i]['verdict'] for i in (1,2)] == ['미충족','미충족']
