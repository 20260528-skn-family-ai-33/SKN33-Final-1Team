"""Offline workflow execution and GGUF protocol checks; no GPU, downloads or real SSH."""
import copy
import json
import threading
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.app import modeltest_remote as remote


def fixture(tmp_path):
    image = tmp_path / 'frame.png'; Image.new('RGB', (80, 40), 'red').save(image)
    references = []
    for index in range(3):
        path = tmp_path / f'ref{index}.png'; Image.new('RGB', (20, 20), 'blue').save(path); references.append(path)
    config = {'contract': remote.CONTRACT, 'flow': 'video', 'files': {'media': {}, 'documents': [], 'references': [{}, {}, {}]},
        'sampling': {'unit': 'frames', 'interval': 1, 'max_frames': 1}, 'stages': [
            {'id': 'logo_extract', 'model_id': remote.OWLV2}, {'id': 'logo_similarity', 'model_id': remote.SIGLIP2},
            {'id': 'candidate_filter', 'threshold': 0.85, 'top_k': 2}, {'id': 'logo_compare', 'model_id': remote.XFEAT}],
        'workflow': {'max_parallel': 1, 'edges': [{'source': a, 'target': b} for a, b in zip(
            ['frames', 'logo_extract', 'logo_similarity', 'candidate_filter'], ['logo_extract', 'logo_similarity', 'candidate_filter', 'logo_compare'])]}}
    return config, {'media': image, 'documents': [], 'references': references, 'container': 'mov'}


@pytest.mark.parametrize('empty', [False, True])
def test_filter_reduces_actual_xfeat_calls_per_logo_and_skips_loading_when_empty(tmp_path, monkeypatch, empty):
    config, paths = fixture(tmp_path); calls = []; loaded = []
    if empty: config['stages'][2]['threshold'] = 1
    class Models:
        def load(self, stage): loaded.append(stage['model_id'])
    monkeypatch.setattr(remote, 'Models', Models)
    monkeypatch.setattr(remote, 'frames_of', lambda *args: [{'index': 0, 'path': paths['media']}])
    monkeypatch.setitem(remote.MODEL_HANDLERS, remote.OWLV2, lambda *args, **kwargs: {'logos': [{'bbox': [0, 0, 20, 20]}, {'bbox': [20, 0, 40, 20]}]})
    def similarity(models, images):
        logo = int(images[0].stem.rsplit('-', 1)[1]); ref = int(images[1].stem[-1])
        return {'image_cosine_similarity': [[0.95, 0.8, 0.3], [0.1, 0.92, 0.88]][logo][ref]}
    def compare(models, stage, images, **kwargs):
        calls.append((int(images[0].stem.rsplit('-', 1)[1]), int(images[1].stem[-1])))
        return {'decision': 'uncertain', 'matches': 4}
    monkeypatch.setattr(remote, 'image_similarity', similarity)
    monkeypatch.setitem(remote.MODEL_HANDLERS, remote.XFEAT, compare)
    remote.execute_run(config, paths, tmp_path, lambda *_: None)
    job = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    assert job['status'] == 'completed', job
    assert calls == ([] if empty else [(0, 0), (1, 1), (1, 2)])
    assert (remote.XFEAT in loaded) is not empty
    row = job['stages'][-1]['output']['items'][0]
    assert row['candidate_count'] == 6 and row['selected_count'] == len(calls)
    if not empty: assert row['matches'][0]['image_cosine_similarity'] == 0.95


def test_graph_rejects_cycles_missing_sources_and_invalid_filters(tmp_path):
    config, _ = fixture(tmp_path)
    remote.validate_config(config)
    broken = copy.deepcopy(config); broken['workflow']['edges'].append({'source': 'logo_compare', 'target': 'logo_extract'})
    with pytest.raises(ValueError, match='순환'): remote.validate_config(broken)
    broken = copy.deepcopy(config); broken['workflow']['edges'].pop(0)
    with pytest.raises(ValueError, match='프레임'): remote.validate_config(broken)
    for value in (-2, float('nan'), True):
        broken = copy.deepcopy(config); broken['stages'][2]['threshold'] = value
        with pytest.raises(ValueError): remote.validate_config(broken)


def test_parallel_agents_wait_for_both_inputs_and_keep_distinct_model_instances(tmp_path, monkeypatch):
    config, paths = fixture(tmp_path); barrier = threading.Barrier(2); finished = []; owners = {}
    class Models:
        def load(self, stage): pass
    monkeypatch.setattr(remote, 'Models', Models)
    monkeypatch.setattr(remote, 'frames_of', lambda *args: [{'index': 0, 'path': paths['media']}])
    def generate(models, stage, prompt, **kwargs):
        if stage['id'] in ('step-a', 'step-b'):
            owners[stage['id']] = id(models); barrier.wait(timeout=5); finished.append(stage['id'])
        else:
            assert sorted(finished) == ['step-a', 'step-b']
            assert 'step-a result' in prompt and 'step-b result' in prompt
        return {'text': stage['id'] + ' result'}
    monkeypatch.setitem(remote.MODEL_HANDLERS, remote.QWEN, generate)
    config['stages'] = [{'id': f'step-{name}', 'kind': 'text_generate', 'name': name, 'agent_id': 'agent-test', 'model_id': remote.QWEN} for name in ('a', 'b', 'c')]
    config['workflow'] = {'max_parallel': 2, 'agents': [{'id': 'agent-test', 'name': '테스트 에이전트'}],
        'edges': [{'source': 'step-a', 'target': 'step-c'}, {'source': 'step-b', 'target': 'step-c'}]}
    remote.execute_run(config, paths, tmp_path, lambda *_: None)
    job = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    assert job['status'] == 'completed', job
    assert owners['step-a'] != owners['step-b']
    assert job['stages'][-1]['output']['items'][0]['text'] == 'step-c result'


def test_duplicate_detector_roles_use_explicit_source_and_unique_crop_files(tmp_path, monkeypatch):
    config, paths = fixture(tmp_path); seen = []
    class Models:
        def load(self, stage): pass
    monkeypatch.setattr(remote, 'Models', Models)
    monkeypatch.setattr(remote, 'frames_of', lambda *args: [{'index': 0, 'path': paths['media']}])
    def detect(models, stage, **kwargs): return {'logos': [{'bbox': [0, 0, 10 if stage['id'] == 'detect-a' else 20, 10]}]}
    def compare(models, stage, images, **kwargs):
        seen.append((stage['id'], images[0].name, Image.open(images[0]).size)); return {'decision': 'uncertain'}
    monkeypatch.setitem(remote.MODEL_HANDLERS, remote.OWLV2, detect); monkeypatch.setitem(remote.MODEL_HANDLERS, remote.XFEAT, compare)
    config['stages'] = [
        {'id': 'detect-a', 'kind': 'logo_extract', 'model_id': remote.OWLV2}, {'id': 'detect-b', 'kind': 'logo_extract', 'model_id': remote.OWLV2},
        {'id': 'compare-a', 'kind': 'logo_compare', 'model_id': remote.XFEAT, 'inputs': {'logo_extract': 'detect-a'}},
        {'id': 'compare-b', 'kind': 'logo_compare', 'model_id': remote.XFEAT, 'inputs': {'logo_extract': 'detect-b'}}]
    config['workflow'] = {'max_parallel': 2, 'edges': [{'source': a, 'target': b} for a, b in [('frames', 'detect-a'), ('frames', 'detect-b'), ('detect-a', 'compare-a'), ('detect-b', 'compare-b')]]}
    remote.execute_run(config, paths, tmp_path, lambda *_: None)
    job = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    assert job['status'] == 'completed', job
    assert len(seen) == 6
    assert all(name.startswith('logo-detect-a-') and size == (10, 10) if node == 'compare-a' else name.startswith('logo-detect-b-') and size == (20, 10) for node, name, size in seen)
    assert remote.read_preview(tmp_path, seen[0][1])['mime'] == 'image/jpeg'


def test_invalid_optional_graph_values_fail_validation_before_execution(tmp_path):
    config, _ = fixture(tmp_path)
    invalid = copy.deepcopy(config); invalid['workflow'] = None
    with pytest.raises(ValueError, match='워크플로우'): remote.validate_config(invalid)
    invalid = copy.deepcopy(config); invalid['stages'][0]['inputs'] = None
    with pytest.raises(ValueError, match='입력 연결'): remote.validate_config(invalid)


def test_generated_logo_stage_passes_custom_instruction_to_detector():
    seen = []
    models = SimpleNamespace(logos=lambda path, instruction: seen.append((path, instruction)) or [])
    result = remote.run_generated(models, {'id': 'custom-logo', 'kind': 'logo_extract', 'prompt': '행사 후원 로고를 찾아주세요.'}, images=['frame.png'])
    assert result == {'logos': []} and seen == [('frame.png', '행사 후원 로고를 찾아주세요.')]
