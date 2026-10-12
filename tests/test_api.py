import io
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app


def image_bytes(size=(24, 24)):
    out = io.BytesIO()
    Image.new('RGB', size, 'pink').save(out, format='PNG')
    return out.getvalue()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    criteria_path = tmp_path / 'criteria.json'
    criteria_path.write_text(json.dumps({'rules': [{'id': 'C01', 'name': '상품명', 'expected': '딸기라떼', 'check': '상품명 확인'}]}))
    samples = tmp_path / 'samples'
    samples.mkdir()
    (samples / 'sample-01.png').write_bytes(image_bytes())
    calls = []
    def inspect(path, rules):
        calls.append(path)
        return {'results': [{'rule_id': r['id'], 'name': r['name'], 'expected': r['expected'],
                              'verdict': '확인 필요', 'execution_status': 'completed', 'observed': '테스트 전용',
                              'reason': 'API 계약 시험', 'evidence': [], 'method': 'test-only'} for r in rules],
                'trace': [], 'method': 'test-only'}
    monkeypatch.setitem(sys.modules, 'app.mcp_client', SimpleNamespace(inspect_image=inspect, inspect_via_mcp=inspect))
    app = create_app(tmp_path / 'runtime', criteria_path, samples)
    app.state.store.prepare()
    with TestClient(app, base_url='http://localhost') as client:
        client.headers['X-CSRFToken'] = client.get('/api/csrf').json()['csrf_token']
        yield client, calls, criteria_path, samples, tmp_path


def upload(client):
    response = client.post('/api/assets', files={'file': ('../test.png', image_bytes(), 'image/png')})
    assert response.status_code == 200
    return response.json()


def confirm(client):
    c = client.get('/api/criteria').json()
    assert client.post('/api/criteria/confirm', json={'version': c['version']}).status_code == 200
    return c['version']


def test_confirmation_idempotency_and_snapshots(setup):
    client, calls, criteria_path, _, _ = setup
    asset = upload(client)
    version = client.get('/api/criteria').json()['version']
    request = {'asset_id': asset['id'], 'criteria_version': version, 'idempotency_key': 'same-click'}
    assert client.post('/api/runs', json=request).status_code == 409
    confirm(client)
    run = client.post('/api/runs', json=request).json()
    assert run['status'] == 'completed'
    assert run['results'][0]['execution_status'] == 'succeeded'
    assert client.post('/api/runs', json=request).json()['id'] == run['id']
    assert len(calls) == 1
    second_asset = upload(client)
    assert client.post('/api/runs', json={**request, 'asset_id': second_asset['id']}).status_code == 409
    criteria_path.write_text(json.dumps({'rules': [{'id': 'C01', 'name': '상품명', 'expected': '말차라떼', 'check': '수정 조건'}]}))
    assert client.get('/api/criteria').json()['confirmed'] is False
    assert client.post('/api/runs', json={**request, 'idempotency_key': 'new'}).status_code == 409
    saved = client.get('/api/runs/' + run['id']).json()
    assert saved['status'] == 'completed'
    assert saved['results'][0]['execution_status'] == 'succeeded'
    assert saved['criteria_snapshot']['rules'][0]['expected'] == '딸기라떼'
    assert saved['asset']['sha256'] == asset['sha256']


def test_rejects_bad_files_and_unlisted_samples(setup):
    client, *_ = setup
    response = client.post('/api/assets', files={'file': ('fake.png', b'not an image', 'image/png')})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'INVALID_IMAGE'
    assert client.post('/api/assets/from-sample', json={'sample_id': '../../reviewer/answer-key'}).status_code == 404
    assert client.get('/api/assets/not-real/image').status_code == 404
    asset = upload(client)
    assert asset['name'] == 'test.png'
    assert 'storage_name' not in asset
    assert client.get(asset['url']).content == image_bytes()


def test_inspection_failure_is_not_a_failed_verdict(setup, monkeypatch):
    client, *_ = setup
    def fail(*_):
        raise RuntimeError('secret internal details')
    monkeypatch.setitem(sys.modules, 'app.mcp_client', SimpleNamespace(inspect_image=fail, inspect_via_mcp=fail))
    asset = upload(client)
    version = confirm(client)
    run = client.post('/api/runs', json={'asset_id': asset['id'], 'criteria_version': version, 'idempotency_key': 'fail'}).json()
    assert run['status'] == 'failed'
    assert run['results'][0]['verdict'] is None
    assert run['results'][0]['execution_status'] == 'failed'
    assert 'secret' not in json.dumps(run)


def test_persistence_and_samples(setup):
    client, _, criteria_path, samples, tmp_path = setup
    version = confirm(client)
    listed = client.get('/api/samples').json()
    assert len(listed) == 1
    asset = client.post('/api/assets/from-sample', json={'sample_id': listed[0]['id']}).json()
    run = client.post('/api/runs', json={'asset_id': asset['id'], 'criteria_version': version, 'idempotency_key': 'persist'}).json()
    with TestClient(create_app(tmp_path / 'runtime', criteria_path, samples), base_url='http://localhost') as restarted:
        assert restarted.get('/api/criteria').json()['confirmed']
        assert restarted.get('/api/runs/' + run['id']).json()['id'] == run['id']
        assert restarted.get(asset['url']).status_code == 200


def test_partial_execution_and_out_of_bounds_evidence(setup, monkeypatch):
    client, _, criteria_path, _, _ = setup
    rules = [{'id': key, 'name': key, 'expected': '조건', 'check': '확인'} for key in ['C01', 'C02']]
    criteria_path.write_text(json.dumps({'rules': rules}))
    invalid_box = False
    def inspect(*_):
        return {'results': [
            {'rule_id': 'C01', 'name': 'C01', 'expected': '조건', 'verdict': '충족',
             'execution_status': 'succeeded', 'observed': '관찰', 'reason': '일치', 'method': 'test-only',
             'evidence': [{'text': '문구 위치', 'bbox': [0.9, 0.2, 0.5, 0.1] if invalid_box else [0.1, 0.2, 0.2, 0.1]}]},
            {'rule_id': 'C02', 'name': 'C02', 'expected': '조건', 'verdict': '확인 필요',
             'execution_status': 'not_run', 'observed': None, 'reason': '미지원 검사', 'method': 'test-only', 'evidence': []}
        ]}
    monkeypatch.setitem(sys.modules, 'app.mcp_client', SimpleNamespace(inspect_via_mcp=inspect))
    asset = upload(client)
    version = confirm(client)
    request = {'asset_id': asset['id'], 'criteria_version': version, 'idempotency_key': 'partial'}
    assert client.post('/api/runs', json=request).json()['status'] == 'partial'
    invalid_box = True
    result = client.post('/api/runs', json={**request, 'idempotency_key': 'invalid-box'}).json()
    assert result['status'] == 'failed'
    assert result['results'][0]['verdict'] is None


@pytest.mark.parametrize('identifier_field', ['id', 'rule_id'])
def test_canonical_and_legacy_criteria_are_accepted_without_changing_confirmation_version(setup, identifier_field):
    import hashlib
    client, _, criteria_path, _, _ = setup
    raw = json.dumps({'rules': [{identifier_field: 'C01', 'name': '상품명', 'expected': '딸기라떼'}]}).encode()
    criteria_path.write_bytes(raw)
    version = hashlib.sha256(raw).hexdigest()[:16]
    current = client.get('/api/criteria').json()
    assert current['rules'][0]['rule_id'] == current['rules'][0]['id'] == 'C01'
    assert current['version'] == version
    assert confirm(client) == version
    asset = upload(client)
    run = client.post('/api/runs', json={
        'asset_id': asset['id'], 'criteria_version': version, 'idempotency_key': identifier_field}).json()
    assert run['status'] == 'completed'
    assert run['results'][0]['rule_id'] == 'C01'
    assert run['results'][0]['execution_status'] == 'succeeded'
    assert client.get('/api/criteria').json()['confirmed'] is True
    assert criteria_path.read_bytes() == raw


@pytest.mark.parametrize('rules', [
    [{'id': 'C01', 'rule_id': 'C02'}],
    [{'id': 'C01'}, {'rule_id': 'C01'}],
])
def test_invalid_criteria_identifiers_are_rejected_before_confirmation(setup, rules):
    client, calls, criteria_path, _, _ = setup
    criteria_path.write_text(json.dumps({'rules': rules}))
    response = client.get('/api/criteria')
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'INVALID_CRITERIA'
    assert calls == []


def test_duplicate_mcp_result_identifiers_do_not_count_as_completed(setup, monkeypatch):
    client, _, criteria_path, _, _ = setup
    criteria_path.write_text(json.dumps({'rules': [
        {'rule_id': key, 'name': key, 'expected': '조건'} for key in ('C01', 'C02')]}))
    def duplicate_results(*_):
        row = {'rule_id': 'C01', 'name': 'C01', 'expected': '조건', 'verdict': '충족',
               'execution_status': 'completed', 'observed': '관찰', 'reason': '테스트', 'evidence': [], 'method': 'test-only'}
        return {'results': [row, dict(row)]}
    monkeypatch.setitem(sys.modules, 'app.mcp_client', SimpleNamespace(inspect_via_mcp=duplicate_results))
    version = confirm(client)
    asset = upload(client)
    run = client.post('/api/runs', json={
        'asset_id': asset['id'], 'criteria_version': version, 'idempotency_key': 'duplicate-results'}).json()
    assert run['status'] == 'failed'
    assert [row['rule_id'] for row in run['results']] == ['C01', 'C02']
    assert all(row['verdict'] is None for row in run['results'])
