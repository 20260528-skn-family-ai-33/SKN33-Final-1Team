"""CPU-only adapter checks with fixture outputs; no weights, network or GPU inference."""
import json
import copy
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.app import modeltest_remote as remote


@pytest.mark.parametrize('similarity_model', remote.SIGLIP2_MODELS)
@pytest.mark.parametrize('failure', [None, 'load', 'inference'])
def test_timings_separate_models_and_keep_failed_work(tmp_path, monkeypatch, matching_detection_embeddings, failure, similarity_model):
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
            {'id': 'logo_compare', 'model_id': remote.XFEAT, 'image_similarity': True, 'image_similarity_model': similarity_model}]}
    remote.execute_run(config, {'media': image, 'documents': [], 'references': [image], 'container': 'mov'}, tmp_path,
        lambda kind, value: events.append(copy.deepcopy(value)) if kind == 'run' else None)
    job = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    assert job['status'] == ('failed' if failure else 'completed')
    assert job['stages'][0]['elapsed_seconds'] == 1
    extraction = job['stages'][1]
    assert extraction['elapsed_seconds'] == 7
    detector, detection_filter = extraction['model_timings']
    assert (detector['model_id'], detector['load_seconds'], detector['inference_seconds'], detector['calls']) == (remote.OWLV2, 2, 3, 1)
    assert (detection_filter['model_id'], detection_filter['load_seconds'], detection_filter['inference_seconds'], detection_filter['calls']) == (remote.DETECTION_FILTER_MODEL, 2, 0, 1)
    assert extraction['detection_filter']['dropped'] == 0
    assert len(matching_detection_embeddings) == 2
    stage = job['stages'][2]; xfeat = stage['model_timings'][0]
    assert xfeat['load_seconds'] == 2
    assert xfeat['inference_seconds'] == (0 if failure == 'load' else 3)
    assert xfeat['calls'] == (0 if failure == 'load' else 1)
    assert stage['elapsed_seconds'] == (2 if failure == 'load' else 5 if failure else 11)
    assert job['elapsed_seconds'] == 8 + stage['elapsed_seconds']
    assert all('active_phase' not in row for row in stage['model_timings'])
    assert any(row.get('active_phase') == 'load' for event in events for item in event['stages'] for row in item.get('model_timings', []))
    if not failure:
        siglip = stage['model_timings'][1]
        assert (siglip['model_id'], siglip['load_seconds'], siglip['inference_seconds'], siglip['calls']) == (similarity_model, 2, 4, 1)


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
def test_owlv2_detects_frame_and_tiles_and_returns_pixel_boxes(tmp_path, custom):
    torch = pytest.importorskip('torch')
    path = tmp_path / 'frame.png'; Image.new('RGB', (80, 40)).save(path)
    model = lambda **inputs: 'detector output'
    model.device = 'cpu'
    labels = custom.splitlines() if custom else ['a brand logo', 'a logo', 'an emblem', 'a brand symbol', 'a watermark', 'a logo mark']
    regions = [(0, 0, 80, 40)] + remote.owlv2_tiles(80, 40)
    class Processor:
        def __call__(self, **kwargs):
            assert kwargs['text'] == [labels] * 11 and len(kwargs['images']) == 11
            assert kwargs['images'][0].size == (80, 40)
            return Batch()
        def post_process_grounded_object_detection(self, output, **kwargs):
            assert output == 'detector output'
            assert kwargs['target_sizes'][0] == (40, 80) and kwargs['threshold'] == 0.1
            empty = {'boxes': torch.zeros((0, 4)), 'scores': torch.zeros(0)}
            # The same logo found in the frame and in the top-left tile collapses to one box; a scene-sized box is dropped.
            return [{'boxes': torch.tensor([[2., 3., 12., 13.], [0., 0., 80., 40.]]), 'scores': torch.tensor([0.75, 0.9])},
                    {'boxes': torch.tensor([[2., 3., 12., 13.]]), 'scores': torch.tensor([0.5])}] + [empty] * 9
    output = remote.run_model(SimpleNamespace(model=model, processor=Processor()),
        {'id': 'logo_extract', 'model_id': remote.OWLV2, **({'prompt_override': custom} if custom else {})}, images=[path])
    assert regions[1][:2] == (0, 0)
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


def test_xfeat_loader_pins_code_uses_only_tensor_weights_and_reuses_model(tmp_path, monkeypatch):
    torch = pytest.importorskip('torch'); calls = []
    weights = tmp_path / ('a' * 40) / 'xfeat.pt'; weights.parent.mkdir(); weights.write_bytes(b'fixture weights')
    class Model:
        def __init__(self): self.net = self
        def load_state_dict(self, state): assert state == {'fixture': True}
        def to(self, device): assert device == 'cuda'; return self
        def eval(self): return self
    def download(repo, filename, revision):
        calls.append(('weights', repo, filename, revision)); return str(weights)
    def load_weights(path, **kwargs):
        assert path == str(weights) and kwargs == {'map_location': 'cpu', 'weights_only': True}
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
    assert models.execution['revision'] == 'a' * 40
    assert models.execution['weights_sha256'] == remote.hashlib.sha256(weights.read_bytes()).hexdigest()
    models.load({**stage, 'revision': 'other'})
    assert models.model is not first and calls[-1] == ('weights', remote.XFEAT, 'xfeat.pt', 'other')


def test_specialized_outputs_reach_crops_reference_comparison_and_condition_judge(tmp_path, monkeypatch, matching_detection_embeddings):
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
    assert calls == [remote.OWLV2, remote.DETECTION_FILTER_MODEL, remote.SIGLIP2, remote.XFEAT, remote.QWEN]
    assert 'label_scores' in prompts[0] and 'red required' in prompts[0]
    match = next(stage for stage in job['stages'] if stage['id'] == 'logo_compare')['output']['items'][0]['matches'][0]
    assert match == {'logo_index': 0, 'reference_index': 0, 'decision': 'uncertain', 'matches': 0}


@pytest.mark.parametrize('reference_index', [0, 1])
@pytest.mark.parametrize('side,score,status', [
    (48, 0.75, 'accepted'), (48, 0.7499, None),
    (47, 0.80, 'accepted'), (47, 0.7999, 'review'),
    (47, 0.75, 'review'), (47, 0.7499, None),
])
def test_detection_filter_uses_reference_resolution_and_keeps_review_boundary(tmp_path, monkeypatch, reference_index, side, score, status):
    import numpy as np

    frame = tmp_path / 'frame.png'; Image.new('RGB', (100, 100)).save(frame)
    references = [tmp_path / f'reference-{index}.png' for index in range(2)]
    for path in references: Image.new('RGB', (100, 80)).save(path)
    calls = []
    def embed(models, images):
        index = len(calls); calls.append([image.size for image in images])
        if index < 2:
            assert calls[-1] == [(100, 80), (25, 20), (35, 28), (50, 40), (70, 56)]
            if index == reference_index:
                # Small logos can match the degraded reference even when the full image does not.
                return np.array([[1., 0., 0., 0.]] + [[0., 1., 0., 0.]] * 4)
            return np.array([[0., 0., 1., 0.]] * 5)
        assert len(images) == 1
        vector = np.zeros(4); vector[1 if side < remote.LOGO_MIN_SIDE else 0] = score
        vector[3] = np.sqrt(1 - score ** 2)
        return vector[None, :]
    monkeypatch.setattr(remote, 'siglip_embed', embed)
    logo = {'bbox': [0, 0, side, side], 'score': 0.9}
    detections = [[logo], []]
    kept, dropped = remote.filter_detections(object(), [{'path': frame}, {'path': frame}], detections, references)

    assert len(calls) == 3 and kept[1] == [] and dropped[1] == 0
    assert detections == [[logo], []] and 'detection_status' not in logo
    if status is None:
        assert kept[0] == [] and dropped[0] == 1
    else:
        assert dropped[0] == 0
        assert kept[0] == [{**logo, 'detection_status': status, 'detection_similarity': score,
                            'detection_reference_index': reference_index,
                            'detection_basis': 'low_resolution_reference' if side < remote.LOGO_MIN_SIDE else 'reference'}]


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
    assert result['image_cosine_similarity'] == 0.8 and result['similarity_model'] == remote.SIGLIP2
    assert result['metric'] == 'cosine_similarity' and len(result['space_id']) == 64
    remote.validate_model_stage({'id': 'logo_compare', 'model_id': remote.XFEAT, 'image_similarity': True})
    with pytest.raises(ValueError):
        remote.validate_model_stage({'id': 'visual_extract', 'model_id': remote.SIGLIP2, 'labels': ['a'], 'image_similarity': True})


@pytest.mark.parametrize('model_id', [*remote.OWLV2_MODELS, *remote.SIGLIP2_MODELS])
@pytest.mark.parametrize('size_dataclass', [False, True])
def test_model_variants_load_matching_processor_and_pinned_revision(monkeypatch, model_id, size_dataclass):
    from dataclasses import make_dataclass
    calls = []; revision = 'b' * 40
    class Loader:
        @staticmethod
        def from_pretrained(repo, **kwargs):
            calls.append((repo, kwargs))
            if len(calls) == 1: return SimpleNamespace(_commit_hash=revision)
            if len(calls) == 2:
                size = {'width': 384, 'height': 384} if repo == remote.SIGLIP2_SO400M else {'width': 224, 'height': 224}
                if size_dataclass: size = make_dataclass('SizeDict', [('width', int), ('height', int)])(**size)
                return SimpleNamespace(image_processor=SimpleNamespace(size=size))
            return SimpleNamespace(eval=lambda: 'loaded model')
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(empty_cache=lambda: None)))
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(**{name: Loader for name in (
        'AutoConfig', 'AutoModel', 'AutoModelForCausalLM', 'AutoModelForImageTextToText', 'AutoModelForZeroShotObjectDetection', 'AutoProcessor', 'AutoTokenizer', 'Owlv2ForObjectDetection')}))
    role = 'logo_extract' if model_id in remote.OWLV2_MODELS else 'logo_similarity'
    stage = {'id': role, 'model_id': model_id, 'revision': 'main'}
    remote.validate_model_stage(stage)
    models = remote.Models(); models.load(stage)
    assert [repo for repo, _ in calls] == [model_id] * 3
    assert all(options['revision'] == revision for _, options in calls[1:])
    assert models.execution['revision'] == revision
    execution = json.loads(json.dumps(models.execution, allow_nan=False))
    size = 384 if model_id == remote.SIGLIP2_SO400M else 224
    assert execution['preprocessing']['size'] == {'width': size, 'height': size}
    assert remote.MODEL_HANDLERS[model_id] is (remote.run_owlv2 if role == 'logo_extract' else remote.run_siglip2)


def _logo_fixture(tmp_path):
    from PIL import ImageDraw
    image = Image.new('RGB', (320, 120), 'white'); draw = ImageDraw.Draw(image)
    draw.ellipse((10, 10, 100, 100), fill=(20, 150, 60)); draw.rectangle((130, 35, 300, 85), fill=(20, 40, 140))
    path = tmp_path / 'reference.png'; image.save(path)
    return path


def _ocr(text):
    return {'text': text, 'ocr_words': [{'text': text, 'polygon': [[130, 35], [300, 35], [300, 85], [130, 85]], 'score': 0.99}]}


def test_logo_diff_never_rejects_identical_logo(tmp_path):
    pytest.importorskip('cv2')
    from backend.app import modeltest_adapters as adapters
    reference = _logo_fixture(tmp_path)
    result = adapters.logo_diff_pair(reference, reference, [[1, 0, 0], [0, 1, 0], [0, 0, 1]], ocr_query=_ocr('대전광역시'), ocr_reference=_ocr('대전광역시'))
    assert result['decision'] == 'uncertain' and result['reason_codes'] == [] and result['human_review_required']
    assert result['metrics']['safe_reject_policy'] == adapters.LOGO_DIFF_SAFE_REJECT_POLICY['safe_reject_policy']


@pytest.mark.parametrize('candidate, rejected', [('데전광역시', True), ('대전광역', False)])
def test_logo_diff_rejects_only_same_length_hangul_typo(tmp_path, candidate, rejected):
    pytest.importorskip('cv2')
    from backend.app import modeltest_adapters as adapters
    reference = _logo_fixture(tmp_path)
    result = adapters.logo_diff_pair(reference, reference, [[1, 0, 0], [0, 1, 0], [0, 0, 1]], ocr_query=_ocr(candidate), ocr_reference=_ocr('대전광역시'))
    assert ('TEXT' in result['reason_codes']) is rejected
    assert result['decision'] == ('mismatch' if rejected else 'uncertain')
    if not rejected: assert 'TEXT' in result['uncertain_reason_codes']


def test_paddle_model_name_prefers_inference_config(tmp_path):
    from backend.app import modeltest_adapters as adapters
    (tmp_path / 'inference.yml').write_text('Global:\n  model_name: korean_PP-OCRv5_mobile_rec\n', encoding='utf-8')
    assert adapters._paddle_model_name(tmp_path, 'someone/renamed-rec') == 'korean_PP-OCRv5_mobile_rec'
    assert adapters._paddle_model_name(tmp_path / 'missing', 'PaddlePaddle/PP-OCRv5_server_det') == 'PP-OCRv5_server_det'
