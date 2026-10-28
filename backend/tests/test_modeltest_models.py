"""CPU-only adapter checks with fixture outputs; no weights, network or GPU inference."""
import json
import copy
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.app import modeltest_remote as remote


@pytest.mark.parametrize('failure', [None, 'load', 'inference'])
def test_timings_separate_models_and_keep_failed_work(tmp_path, monkeypatch, failure):
    clock = [0.0]; events = []
    monkeypatch.setattr(remote.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(remote.time, 'time', lambda: 1000 + clock[0])
    class Models:
        def load(self, stage):
            clock[0] += 2
            if failure == 'load' and stage['model_id'] == remote.XFEAT: raise ValueError('fixture load failed')
    monkeypatch.setattr(remote, 'Models', Models)
    image = tmp_path / 'poster.png'; Image.new('RGB', (40, 40)).save(image)
    def frames(*args):
        clock[0] += 1
        return [{'index': 0, 'path': image}]
    def detect(*args, **kwargs):
        clock[0] += 3
        return {'logos': [{'bbox': [0, 0, 20, 20]}]}
    def compare(*args, **kwargs):
        clock[0] += 3
        if failure == 'inference': raise ValueError('fixture inference failed')
        return {'decision': 'uncertain'}
    def similarity(*args):
        clock[0] += 4
        return {'image_cosine_similarity': 0.8}
    monkeypatch.setattr(remote, 'frames_of', frames)
    monkeypatch.setitem(remote.MODEL_HANDLERS, remote.OWLV2, detect)
    monkeypatch.setitem(remote.MODEL_HANDLERS, remote.XFEAT, compare)
    monkeypatch.setattr(remote, 'image_similarity', similarity)
    config = {'contract': remote.CONTRACT, 'flow': 'video', 'sampling': {'unit': 'frames', 'interval': 1, 'max_frames': 1},
        'files': {'media': {}, 'documents': [], 'references': [{}]}, 'stages': [
            {'id': 'logo_extract', 'model_id': remote.OWLV2},
            {'id': 'logo_compare', 'model_id': remote.XFEAT, 'image_similarity': True}]}
    remote.execute_run(config, {'media': image, 'documents': [], 'references': [image], 'container': 'mov'}, tmp_path,
        lambda kind, value: events.append(copy.deepcopy(value)) if kind == 'run' else None)
    job = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    assert job['status'] == ('failed' if failure else 'completed')
    assert job['stages'][0]['elapsed_seconds'] == 1
    assert job['stages'][1]['elapsed_seconds'] == 5
    stage = job['stages'][2]; xfeat = stage['model_timings'][0]
    assert xfeat['load_seconds'] == 2
    assert xfeat['inference_seconds'] == (0 if failure == 'load' else 3)
    assert xfeat['calls'] == (0 if failure == 'load' else 1)
    assert stage['elapsed_seconds'] == (2 if failure == 'load' else 5 if failure else 11)
    assert job['elapsed_seconds'] == 6 + stage['elapsed_seconds']
    assert all('active_phase' not in row for row in stage['model_timings'])
    assert any(row.get('active_phase') == 'load' for event in events for item in event['stages'] for row in item.get('model_timings', []))
    if not failure:
        siglip = stage['model_timings'][1]
        assert (siglip['model_id'], siglip['load_seconds'], siglip['inference_seconds'], siglip['calls']) == (remote.SIGLIP2, 2, 4, 1)


def test_interrupted_timing_freezes_active_call_and_marks_estimate():
    timing = {'model_id': remote.XFEAT, 'load_seconds': 2, 'inference_seconds': 3, 'calls': 2,
        'active_phase': 'inference', 'active_started_at': 15}
    stage = {'status': 'running', 'started_at': 10, 'model_timings': [timing]}
    job = {'started_at': 5, 'stages': [stage, {'status': 'queued'}]}
    remote.finish_interrupted_timings(job, 20)
    assert stage['elapsed_seconds'] == 10 and stage['timing_estimated']
    assert timing['inference_seconds'] == 8 and 'active_phase' not in timing
    assert job['elapsed_seconds'] == 15


def test_model_roles_and_candidates_are_validated_before_execution():
    for model, stage in [(remote.OWLV2, 'text_extract'), (remote.SIGLIP2, 'logo_extract'), (remote.XFEAT, 'visual_extract')]:
        with pytest.raises(ValueError, match='지원하지'):
            remote.validate_model_stage({'id': stage, 'model_id': model})
    for labels in (None, [], [' '], ['a', ' a '], ['a' * 201], ['a'] * 33):
        with pytest.raises(ValueError):
            remote.validate_model_stage({'id': 'visual_extract', 'model_id': remote.SIGLIP2, 'labels': labels})
    remote.validate_model_stage({'id': 'visual_extract', 'model_id': remote.SIGLIP2, 'labels': ['red', 'blue']})
    with pytest.raises(ValueError):
        remote.validate_model_stage({'id': 'text_extract', 'model_id': remote.QWEN, 'labels': ['red']})


class Batch(dict):
    def to(self, device):
        assert device == 'cpu'
        return self


@pytest.mark.parametrize('custom', [None, 'sponsor logo\nbrand mark'])
def test_owlv2_uses_image_dimensions_and_returns_pixel_boxes(tmp_path, custom):
    torch = pytest.importorskip('torch')
    path = tmp_path / 'frame.png'; Image.new('RGB', (80, 40)).save(path)
    model = lambda **inputs: 'detector output'
    model.device = 'cpu'
    class Processor:
        def __call__(self, **kwargs):
            assert kwargs['text'] == [custom.splitlines() if custom else ['a brand logo']]
            assert kwargs['images'].size == (80, 40)
            return Batch()
        def post_process_grounded_object_detection(self, output, **kwargs):
            assert output == 'detector output'
            assert kwargs['target_sizes'] == [(40, 80)] and kwargs['threshold'] == 0.2
            return [{'boxes': torch.tensor([[2., 3., 12., 13.]]), 'scores': torch.tensor([0.75])}]
    output = remote.run_model(SimpleNamespace(model=model, processor=Processor()),
        {'id': 'logo_extract', 'model_id': remote.OWLV2, **({'prompt_override': custom} if custom else {})}, images=[path])
    assert output == {'logos': [{'bbox': [2, 3, 12, 13], 'score': 0.75}]}


def test_siglip2_preserves_candidate_score_pairing_and_uses_independent_sigmoid(tmp_path):
    torch = pytest.importorskip('torch')
    path = tmp_path / 'frame.png'; Image.new('RGB', (40, 40)).save(path)
    model = lambda **inputs: SimpleNamespace(logits_per_image=torch.tensor([[0., -2., 2.]]))
    model.device = 'cpu'
    class Processor:
        def __call__(self, **kwargs):
            assert kwargs['text'] == ['red', 'blue', 'green']
            assert kwargs['padding'] == 'max_length' and kwargs['max_length'] == 64 and kwargs['truncation']
            return Batch()
    output = remote.run_model(SimpleNamespace(model=model, processor=Processor()),
        {'id': 'visual_extract', 'model_id': remote.SIGLIP2, 'labels': [' red ', 'blue', 'green']}, images=[path])
    assert [item['label'] for item in output['label_scores']] == ['green', 'red', 'blue']
    assert [item['score'] for item in output['label_scores']] == pytest.approx([0.880797, 0.5, 0.119203])


@pytest.mark.parametrize('count', [0, 3, 8])
def test_xfeat_handles_missing_features_and_returns_original_crop_coordinates(tmp_path, count):
    torch = pytest.importorskip('torch'); pytest.importorskip('cv2')
    crop = tmp_path / 'crop.png'; reference = tmp_path / 'reference.png'
    Image.new('RGB', (16, 8), 'white').save(crop); Image.new('RGB', (64, 64), 'white').save(reference)
    points = torch.tensor([[4., 4.], [12., 4.], [20., 4.], [4., 12.], [12., 12.], [20., 12.], [4., 20.], [20., 20.]])[:count]
    class Features:
        dev = 'cpu'
        calls = 0
        def detectAndCompute(self, tensor):
            self.calls += 1
            assert tensor.shape == ((1, 3, 32, 32) if self.calls == 1 else (1, 3, 64, 64))
            assert tensor.dtype == torch.float32 and float(tensor.max()) == 1.0
            return [{'keypoints': points * self.calls, 'descriptors': torch.ones((count, 64))}]
        def match(self, first, second, min_cossim):
            assert count > 0 and min_cossim == 0.82
            return torch.arange(count), torch.arange(count)
    output = remote.run_model(SimpleNamespace(model=Features()),
        {'id': 'logo_compare', 'model_id': remote.XFEAT}, images=[crop, reference])
    assert output['matches'] == count
    assert output['descriptor_similarity'] == (1.0 if count else None)
    assert output['decision'] == ('candidate_match' if count == 8 else 'uncertain')
    if count == 8:
        assert output['inliers'] == 8 and output['inlier_ratio'] == 1.0
        assert output['matched_points'][0] == {'crop': [2., 1.], 'reference': [8., 8.]}


def test_xfeat_loader_pins_code_uses_only_tensor_weights_and_reuses_model(monkeypatch):
    torch = pytest.importorskip('torch'); calls = []
    class Model:
        def __init__(self): self.net = self
        def load_state_dict(self, state): assert state == {'fixture': True}
        def to(self, device): assert device == 'cuda'; return self
        def eval(self): return self
    def download(repo, filename, revision):
        calls.append(('weights', repo, filename, revision)); return 'fixture.pt'
    def load_weights(path, **kwargs):
        assert path == 'fixture.pt' and kwargs == {'map_location': 'cpu', 'weights_only': True}
        return {'fixture': True}
    def load_code(source, name, **kwargs):
        assert source.endswith(':e92685f57f8318b18725c5c8c0bd28c7fe188d9a') and name == 'XFeat'
        assert kwargs['pretrained'] is False and kwargs['top_k'] == 1024
        return Model()
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(hf_hub_download=download))
    monkeypatch.setattr(torch.hub, 'load', load_code); monkeypatch.setattr(torch, 'load', load_weights)
    monkeypatch.setattr(torch.cuda, 'empty_cache', lambda: None)
    models = remote.Models(); stage = {'id': 'logo_compare', 'model_id': remote.XFEAT, 'revision': 'fixture'}
    models.load(stage); first = models.model; models.load(stage)
    assert models.model is first and len(calls) == 1
    models.load({**stage, 'revision': 'other'})
    assert models.model is not first and calls[-1] == ('weights', remote.XFEAT, 'xfeat.pt', 'other')


def test_specialized_outputs_reach_crops_reference_comparison_and_condition_judge(tmp_path, monkeypatch):
    calls = []; prompts = []
    class Models:
        kind = 'vision'
        def load(self, stage): calls.append(stage['model_id'])
        def generate(self, prompt, images, require_vision): prompts.append(prompt); return '판정 결과'
    monkeypatch.setattr(remote, 'Models', Models)
    poster = tmp_path / 'frame.png'; Image.new('RGB', (80, 40)).save(poster)
    reference = tmp_path / 'ref.png'; Image.new('RGB', (20, 20)).save(reference)
    monkeypatch.setattr(remote, 'frames_of', lambda *args: [{'index': 0, 'path': poster}])
    monkeypatch.setitem(remote.MODEL_HANDLERS, remote.OWLV2, lambda *args, **kwargs: {'logos': [{'bbox': [2, 3, 12, 13], 'score': 0.75}]})
    monkeypatch.setitem(remote.MODEL_HANDLERS, remote.SIGLIP2, lambda *args, **kwargs: {'label_scores': [{'label': 'red', 'score': 0.5}]})
    def compare(*args, images, **kwargs):
        assert Image.open(images[0]).size == (10, 10) and images[1] == reference
        return {'decision': 'uncertain', 'matches': 0}
    monkeypatch.setitem(remote.MODEL_HANDLERS, remote.XFEAT, compare)
    config = {'contract': remote.CONTRACT, 'flow': 'video', 'requirements': 'red required',
        'sampling': {'unit': 'frames', 'interval': 1, 'max_frames': 1},
        'files': {'media': {}, 'documents': [], 'references': [{}]},
        'stages': [{'id': 'logo_extract', 'model_id': remote.OWLV2},
                   {'id': 'visual_extract', 'model_id': remote.SIGLIP2, 'labels': ['red']},
                   {'id': 'logo_compare', 'model_id': remote.XFEAT},
                   {'id': 'visual_compare', 'model_id': remote.QWEN}]}
    remote.execute_run(config, {'media': poster, 'documents': [], 'references': [reference], 'container': 'mov'}, tmp_path, lambda *_: None)
    job = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    assert job['status'] == 'completed', job
    assert calls == [remote.OWLV2, remote.SIGLIP2, remote.XFEAT, remote.QWEN]
    assert 'label_scores' in prompts[0] and 'red required' in prompts[0]
    match = next(stage for stage in job['stages'] if stage['id'] == 'logo_compare')['output']['items'][0]['matches'][0]
    assert match == {'logo_index': 0, 'reference_index': 0, 'decision': 'uncertain', 'matches': 0}


def test_optional_image_cosine_uses_siglip_pooler_features(tmp_path):
    torch = pytest.importorskip('torch')
    path = tmp_path / 'image.png'; Image.new('RGB', (40, 40)).save(path)
    class Model:
        device = 'cpu'
        def get_image_features(self, **kwargs):
            assert kwargs['return_dict'] is True
            return SimpleNamespace(pooler_output=torch.tensor([[3., 0.], [4., 3.]]))
    def processor(**kwargs):
        assert len(kwargs['images']) == 2
        return Batch()
    result = remote.image_similarity(SimpleNamespace(model=Model(), processor=processor), [path, path])
    assert result == {'image_cosine_similarity': 0.8, 'similarity_model': remote.SIGLIP2}
    remote.validate_model_stage({'id': 'logo_compare', 'model_id': remote.XFEAT, 'image_similarity': True})
    with pytest.raises(ValueError):
        remote.validate_model_stage({'id': 'visual_extract', 'model_id': remote.SIGLIP2, 'labels': ['a'], 'image_similarity': True})
