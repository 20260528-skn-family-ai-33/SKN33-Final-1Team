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
    def generate(models, stage, evidence, **kwargs):
        if stage['id'] in ('step-a', 'step-b'):
            owners[stage['id']] = id(models); barrier.wait(timeout=5); finished.append(stage['id'])
        else:
            assert sorted(finished) == ['step-a', 'step-b']
            assert 'step-a result' in evidence and 'step-b result' in evidence
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
    result = remote.run_generated(models, {'id': 'custom-logo', 'kind': 'logo_extract', 'model_id': remote.QWEN, 'prompt_override': '행사 후원 로고를 찾아주세요.'}, images=['frame.png'])
    assert result == {'logos': []} and seen == [('frame.png', '행사 후원 로고를 찾아주세요.')]


@pytest.mark.parametrize('scope,expected', [
    ('references_per_crop', [(0, 0, 0), (0, 1, 0), (1, 0, 0), (1, 1, 1)]),
    ('crops_per_reference_frame', [(0, 0, 0), (0, 1, 1), (1, 0, 0), (1, 1, 1)]),
    ('crops_per_reference_video', [(0, 0, 0), (1, 1, 1)]),
])
def test_candidate_scopes_rank_across_crops_frames_and_keep_exclusions(scope, expected):
    scores = [[[.95, .3], [.9, .8]], [[.95, .2], [.4, .99]]]
    rows = [{'index': frame * 30, 'matches': [{'logo_index': crop, 'reference_index': ref, 'image_cosine_similarity': score,
             'similarity_model': remote.SIGLIP2_SO400M, 'similarity_revision': 'fixed', 'space_id': 'same'}
             for crop, values in enumerate(frame_scores) for ref, score in enumerate(values)]} for frame, frame_scores in enumerate(scores)]
    result = remote.filter_candidates(rows, {'scope': scope, 'top_k': 1, 'threshold': .25})
    assert [(frame, match['logo_index'], match['reference_index']) for frame, row in enumerate(result) for match in row['matches']] == expected
    assert all(row['filter']['total_candidates'] == 8 and row['filter']['total_selected'] == len(expected) for row in result)
    assert sum(len(row['excluded_matches']) for row in result) == 8 - len(expected)
    assert any(match['exclusion_reason'] == 'below_threshold' for row in result for match in row['excluded_matches'])
    assert all(match['candidate_rank'] >= 1 for row in result for match in row['matches'] + row['excluded_matches'])
    for key in ('similarity_model', 'similarity_revision', 'space_id'):
        broken = copy.deepcopy(rows); broken[1]['matches'][0][key] = 'different'
        with pytest.raises(ValueError, match='다른 유사도'): remote.filter_candidates(broken, {'scope': scope, 'top_k': 1})


def test_separate_similarity_node_rejects_additional_siglip_calculation(tmp_path):
    config, _ = fixture(tmp_path)
    config['stages'][-1].update(image_similarity=True, image_similarity_model=remote.SIGLIP2_SO400M)
    with pytest.raises(ValueError, match='함께 선택'): remote.validate_config(config)


@pytest.mark.parametrize('scope,expected_calls', [('crops_per_reference_frame', 6), ('crops_per_reference_video', 3)])
def test_reference_crop_selection_limits_xfeat_calls_across_video(tmp_path, monkeypatch, scope, expected_calls):
    config, paths = fixture(tmp_path); calls = []
    config['stages'][0]['model_id'] = remote.OWLV2_ENSEMBLE
    config['stages'][1]['model_id'] = remote.SIGLIP2_SO400M
    config['stages'][2] = {'id': 'candidate_filter', 'scope': scope, 'top_k': 1}
    class Models:
        def load(self, stage): self.key = stage['model_id']
    monkeypatch.setattr(remote, 'Models', Models)
    monkeypatch.setattr(remote, 'frames_of', lambda *args: [{'index': i * 30, 'path': paths['media']} for i in range(2)])
    monkeypatch.setitem(remote.MODEL_HANDLERS, remote.OWLV2_ENSEMBLE, lambda *args, **kwargs: {'logos': [{'bbox': [0, 0, 20, 20]}, {'bbox': [20, 0, 40, 20]}]})
    def similarity(models, images):
        assert models.key == remote.SIGLIP2_SO400M
        frame, logo = map(int, images[0].stem.rsplit('-', 2)[1:]); ref = int(images[1].stem[-1])
        return {'image_cosine_similarity': .6 + frame / 10 + (logo == ref % 2) / 10, 'similarity_model': models.key}
    def compare(models, stage, images, **kwargs):
        assert models.key == remote.XFEAT
        calls.append(images[0].name); return {'matches': 12, 'inliers': 10}
    monkeypatch.setattr(remote, 'image_similarity', similarity)
    monkeypatch.setitem(remote.MODEL_HANDLERS, remote.XFEAT, compare)
    remote.execute_run(config, paths, tmp_path, lambda *_: None)
    job = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    assert job['status'] == 'completed', job
    assert len(calls) == expected_calls
    rows = job['stages'][-1]['output']['items']
    assert sum(len(row['excluded_matches']) for row in rows) == 12 - expected_calls
    assert all(row['filter']['scope'] == scope for row in rows)
    if scope == 'crops_per_reference_video': assert not rows[0]['matches'] and len(rows[1]['matches']) == 3
