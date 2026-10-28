"""Image pair tests use local fixtures and fake models, never SSH or inference."""
import copy
import json

import pytest
from PIL import Image

from backend.app import modeltest_remote as remote


def config(count=2, resize=None):
    return {'contract': remote.CONTRACT, 'flow': 'similarity', 'resize': resize,
            'files': {'media': {}, 'documents': [], 'references': [{} for _ in range(count)]},
            'stages': [{'id': 'logo_similarity', 'model_id': remote.SIGLIP2}, {'id': 'logo_compare', 'model_id': remote.XFEAT}]}


def test_similarity_validates_counts_resize_and_exact_model_pair():
    remote.validate_config(config(100, {'width': 100, 'height': 50}))
    remote.validate_config(config(1))
    for count in (0, 101):
        with pytest.raises(ValueError): remote.validate_config(config(count))
    for resize in ({'width': 100}, {'width': 0, 'height': 50}, {'width': True, 'height': 50}, {'width': 10.5, 'height': 50}, {'width': 8193, 'height': 50}):
        with pytest.raises(ValueError): remote.validate_config(config(resize=resize))
    state = config(); state['stages'][1]['model_id'] = remote.QWEN
    with pytest.raises(ValueError): remote.validate_config(state)
    state = config(); state['workflow'] = {'edges': [], 'max_parallel': 2}
    with pytest.raises(ValueError): remote.validate_config(state)
    state = config(); state['files']['documents'] = [{}]
    with pytest.raises(ValueError): remote.validate_config(state)


@pytest.mark.parametrize('similarity_model', remote.SIGLIP2_MODELS)
@pytest.mark.parametrize('resize,expected', [(None, (200, 100)), ({'width': 100, 'height': 50}, (100, 50))])
def test_both_models_receive_actual_resized_base_and_keep_candidate_order(tmp_path, monkeypatch, resize, expected, similarity_model):
    base = tmp_path / 'base.png'; Image.new('RGB', (200, 100), 'red').save(base); original = base.read_bytes()
    candidates = []
    for i, size in enumerate([(60, 40), (80, 70)]):
        path = tmp_path / f'candidate-{i}.png'; Image.new('RGB', size, 'blue').save(path); candidates.append(path)
    calls = []; events = []
    class Models:
        def load(self, stage): calls.append(('load', stage['model_id']))
    monkeypatch.setattr(remote, 'Models', Models)
    monkeypatch.setattr(remote, 'process_start', lambda _pid: 'fixture')
    def inspect(images):
        assert remote.image_of(images[0]).size == expected
        assert images[0] != base
        assert remote.image_of(images[1]).size == ((60, 40) if images[1] == candidates[0] else (80, 70))
        return candidates.index(images[1])
    def similarity(_models, images):
        index = inspect(images); calls.append(('cosine', index)); return {'image_cosine_similarity': 0.8 + index / 10}
    def compare(_models, _stage, *, images, **_):
        index = inspect(images); calls.append(('xfeat', index))
        return {'matches': 12 + index, 'inliers': 10, 'inlier_ratio': 0.8, 'descriptor_similarity': 0.9, 'keypoints': [100, 90], 'decision': 'candidate_match'}
    monkeypatch.setattr(remote, 'image_similarity', similarity)
    monkeypatch.setitem(remote.MODEL_HANDLERS, remote.XFEAT, compare)
    state = config(resize=resize); state['stages'][0]['model_id'] = similarity_model
    remote.execute_run(state, {'media': base, 'references': candidates, 'documents': []}, tmp_path,
                       lambda kind, data: events.append(copy.deepcopy(data)) if kind == 'run' else None)
    job = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    assert job['status'] == 'completed', job.get('error')
    assert calls == [('load', similarity_model), ('cosine', 0), ('cosine', 1), ('load', remote.XFEAT), ('xfeat', 0), ('xfeat', 1)]
    assert (job['media']['width'], job['media']['height']) == expected
    assert (job['media']['original_width'], job['media']['original_height']) == (200, 100)
    assert base.read_bytes() == original
    assert [row['reference_index'] for row in job['stages'][1]['output']['items']] == [0, 1]
    assert all(stage['model_timings'][0]['calls'] == 2 for stage in job['stages'])
    assert any(len(event['stages'][0].get('output', {}).get('items', [])) == 1 for event in events)


def test_upsize_fails_before_loading_any_model(tmp_path, monkeypatch):
    base = tmp_path / 'base.png'; Image.new('RGB', (80, 40)).save(base)
    monkeypatch.setattr(remote, 'process_start', lambda _pid: 'fixture')
    def forbidden(): pytest.fail('Invalid resize must not load a model')
    monkeypatch.setattr(remote, 'Models', forbidden)
    remote.execute_run(config(1, {'width': 100, 'height': 50}), {'media': base, 'references': [base], 'documents': []}, tmp_path, lambda *_: None)
    job = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    assert job['status'] == 'failed' and '80 × 40' in job['error']
    assert all(stage['status'] == 'skipped' for stage in job['stages'])
