"""Offline registry, typed workflow and family dispatch checks; no weights or SSH."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import modeltest_catalog as catalog, modeltest_adapters as adapters, modeltest_remote as remote, modeltest_ssh as bridge
from backend.app.modeltest_registry import Registry

SHA = 'a' * 40


def meta(repo='Qwen/Qwen3.5-4B', model_type='qwen3_5', **config):
    return {'model_id': repo, 'revision': 'main', 'resolved_revision': SHA, 'files': ['config.json', 'model.safetensors'], 'configs': {'config.json': {'model_type': model_type, **config}}}


def profile(repo='Qwen/Qwen3.5-4B', model_type='qwen3_5', **settings):
    result = catalog.build_profile(meta(repo, model_type), settings); result['id'] = 'fixture-model'; return result


def stage(name, kind, model=None, inputs=None, params=None):
    result = {'id': name, 'kind': kind, 'inputs': inputs or {}, 'params': params or {}}
    if model: result.update(model_id=model['model_id'], revision=model['resolved_revision'], model_profile=model)
    return result


def plan(stages, edges=()):
    return {'contract': remote.CONTRACT, 'flow': 'image', 'files': {'media': {}, 'documents': [{}], 'references': []}, 'stages': stages,
            'workflow': {'max_parallel': 1, 'edges': [{'source': a, 'target': b} for a, b in edges]}}


def test_inspection_fetches_only_bounded_json_and_pins_revision(monkeypatch):
    calls = []
    def fetch(url, limit=1024**2):
        calls.append(url)
        if '/api/' in url: return {'sha': SHA, 'siblings': [{'rfilename': name} for name in ['config.json', 'model.safetensors', 'README.md', 'custom.py']]}
        return {'model_type': 'qwen3_5'}
    monkeypatch.setattr(catalog, 'json_url', fetch)
    result = catalog.inspect_repo('https://huggingface.co/Qwen/Qwen3.5-4B/tree/main')
    assert result['resolved_revision'] == SHA
    assert len(calls) == 2 and calls[1].endswith(f'/{SHA}/config.json')
    for value in ['Qwen3.5-4B', 'https://evil.example/x/y', 'https://huggingface.co/x/y/blob/main/script.py', 'owner/../model']:
        with pytest.raises(ValueError): catalog.parse_repo(value)


@pytest.mark.parametrize(('repo', 'model_type', 'family'), [('Qwen/Qwen3-8B', 'qwen3', 'qwen_text'), ('Qwen/Qwen3-VL-4B-Instruct', 'qwen3_vl', 'qwen_vision'), ('Qwen/Qwen3.5-27B', 'qwen3_5', 'qwen_vision'), ('google/siglip2-so400m-patch14-384', 'siglip2', 'clip'), ('openai/clip-vit-base-patch32', 'clip', 'clip'), ('facebook/dinov3-vitb16-pretrain-lvd1689m', 'dinov3_vit', 'dino'), ('google/owlv2-large-patch14', 'owlv2', 'owlv2'), ('IDEA-Research/grounding-dino-base', 'grounding-dino', 'grounding_dino'), ('PaddlePaddle/PaddleOCR-VL-1.6', 'paddleocr_vl', 'paddle_vl')])
def test_family_selection_is_config_based_and_variants_share_path(repo, model_type, family):
    result = profile(repo, model_type)
    assert result['family'] == family and result['status'] == 'unverified'
    catalog.validate_profile(result, result['tasks'][0])


def test_st_modules_preserve_pooling_and_reject_remote_code_and_wrong_task():
    data = meta('Qwen/Qwen3-VL-Embedding-2B', 'qwen3_vl')
    data['files'].append('modules.json')
    data['configs']['modules.json'] = [{'path': '', 'type': 'sentence_transformers.base.modules.transformer.Transformer'}, {'path': '1_Pooling', 'type': 'sentence_transformers.sentence_transformer.modules.pooling.Pooling'}]
    result = catalog.build_profile(data)
    assert result['family'] == 'sentence_multimodal' and result['status'] == 'unverified'
    assert 'text_generate' not in result['tasks'] and result['options']['max_length'] == 0
    data['configs']['modules.json'][0]['type'] = 'custom.module.Transformer'
    assert catalog.build_profile(data)['status'] == 'implementation_required'
    data = meta('Qwen/Qwen3-Reranker-0.6B', 'qwen3')
    assert catalog.build_profile(data)['status'] == 'needs_preparation'
    data['configs']['modules.json'] = [{'path': '1_LogitScore', 'type': 'sentence_transformers.cross_encoder.modules.logit_score.LogitScore'}]
    assert catalog.build_profile(data)['tasks'] == ['rerank']


def test_missing_training_artifacts_and_unsupported_formats_are_never_executable():
    for data, settings in [(meta('monologg/koelectra-base-v3-discriminator', 'electra'), {}), (meta('microsoft/layoutxlm-base', 'layoutlmv2'), {}), (meta('owner/yolo11', ''), {}), (meta('QuanSun/EVA-CLIP', ''), {}), (meta('owner/base', 'siglip2'), {'family': 'linear_head'})]:
        result = catalog.build_profile(data, settings)
        assert result['status'] in ('needs_preparation', 'implementation_required') and result['reasons']
        with pytest.raises(ValueError): catalog.validate_profile(result, 'model_prepare')
    result = profile('owner/unknown', 'new_architecture')
    assert result['tasks'] == [] and result['status'] == 'implementation_required'
    with pytest.raises(ValueError, match='미등록'): remote.validate_model_stage({'id': 'text_generate', 'model_id': 'owner/unknown'})
    classifier = catalog.build_profile(meta('owner/classifier', 'electra', architectures=['ElectraForSequenceClassification'], id2label={'0': '허용', '1': '확인'}))
    assert classifier['status'] == 'unverified' and classifier['training']['labels'] == ['허용', '확인']


def test_registry_persists_rejects_browser_profiles_and_invalidates_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, 'inspect_repo', lambda *args: meta())
    registry = Registry(tmp_path / 'models.json'); item = registry.save({'value': 'Qwen/Qwen3.5-4B'})
    restored = Registry(registry.path).read()[0]; assert restored == item
    node = {'id': 'text_generate', 'registered_model': item['id'], 'model_fingerprint': item['fingerprint'], 'model_profile': {'requirements': {'malicious': ''}}}
    assert registry.hydrate({'stages': [node]})['stages'][0]['model_profile']['requirements'] == catalog.TF
    proof = {'registration_id': item['id'], 'fingerprint': item['fingerprint'], 'versions': {'transformers': '5.21.0'}}
    registry.observe({'id': 'sample-run', 'status': 'completed', 'stages': [{'id': 'text_generate', 'status': 'completed', 'model_execution': proof, 'model_timings': [{'calls': 1}]}]})
    assert registry.read()[0]['status'] == 'verified'
    changed = registry.save({**item, 'options': {**item['options'], 'max_tokens': 64}}, item['id'])
    assert changed['status'] == 'unverified' and 'verification' not in changed
    with pytest.raises(ValueError, match='설정이 바뀌'): registry.hydrate({'stages': [node]})
    registry.delete(item['id'])
    with pytest.raises(ValueError, match='삭제'): registry.hydrate({'stages': [node]})


def test_text_only_conditions_reject_pdf_before_execution():
    config = plan([stage('conditions', 'conditions', profile('Qwen/Qwen3-8B', 'qwen3'))])
    config['files']['documents'] = [{'name': 'contract.pdf'}]
    with pytest.raises(ValueError, match='페이지 이미지'): remote.validate_config(config)
    config['files']['documents'] = [{'name': 'contract.txt'}]
    assert remote.validate_config(config) is config


def test_vector_space_not_just_dimensions_and_typed_edges():
    left = [{'record_id': 'query', 'vector': [1, 0], 'space_id': 'same'}]
    right = [{'record_id': 'candidate', 'text': 'evidence', 'vector': [1, 1], 'space_id': 'same'}]
    result = adapters.compare_vectors(left, right)[0]
    assert result['cosine_similarity'] == pytest.approx(2**-.5) and result['text'] == 'evidence'
    with pytest.raises(ValueError, match='다른 벡터'): adapters.compare_vectors(left, [{**right[0], 'space_id': 'different'}])
    model = profile('google/siglip2-base-patch16-224', 'siglip2')
    config = plan([stage('a', 'image_embed', model), stage('b', 'image_embed', model), stage('c', 'vector_compare', inputs={'left': 'a', 'right': 'b'})], [('a', 'c'), ('b', 'c')])
    remote.validate_config(config)
    config['stages'][1] = stage('b', 'text_input', params={'documents': ['x']})
    with pytest.raises(ValueError, match='형식'): remote.validate_config(config)
    config['stages'][1] = stage('b', 'image_embed', profile('openai/clip-vit-base-patch32', 'clip'))
    with pytest.raises(ValueError, match='같은 모델'): remote.validate_config(config)


def test_registered_embedding_reaches_adapter_and_retains_metrics(tmp_path, monkeypatch):
    image = tmp_path / 'sample.png'; Image.new('RGB', (32, 32)).save(image)
    calls = []
    class FakeModels:
        def load(self, node):
            calls.append(node['model_profile']['family'])
            self.registered = SimpleNamespace(profile=node['model_profile'], embed=lambda values, modality: np.array([[1., 0.] for _ in values]))
        def close(self): pass
    monkeypatch.setattr(remote, 'Models', FakeModels)
    model = profile('google/siglip2-base-patch16-224', 'siglip2')
    config = plan([stage('embedding', 'image_embed', model), stage('cosine', 'vector_compare', inputs={'left': 'embedding', 'right': 'embedding'})], [('embedding', 'cosine')])
    remote.execute_run(config, {'media': image, 'documents': [], 'references': []}, tmp_path, lambda *args: None)
    job = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    assert job['status'] == 'completed', job.get('error')
    assert calls == ['clip'] and job['stages'][0]['model_timings'][0]['calls'] == 1
    assert job['stages'][1]['output']['items'][0]['cosine_similarity'] == 1
    assert job['stages'][0]['model_execution']['fingerprint'] == model['fingerprint']


def test_prepare_has_no_media_or_inference_and_exports_modules(tmp_path, monkeypatch):
    monkeypatch.setattr(adapters, 'prepare', lambda value, runner: {'text': 'prepared'})
    model = profile()
    config = {'contract': remote.CONTRACT, 'flow': 'video', 'purpose': 'prepare', 'files': {'media': None, 'documents': [], 'references': []}, 'stages': [{'id': 'model_prepare', 'model_id': model['model_id'], 'revision': SHA, 'model_profile': model}]}
    remote.execute_run(config, {'documents': [], 'references': []}, tmp_path, lambda *args: None)
    job = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    assert job['status'] == 'completed' and len(job['stages']) == 1
    assert job['stages'][0]['model_timings'][0]['calls'] == 0
    space = {'__name__': 'offline_runner'}; exec(compile(bridge.runner_source(), 'bundle', 'exec'), space)
    assert 'image_embed' in space['STAGES'] and space['catalog'].FAMILIES['paddle_gguf']['engine'] == 'llama.cpp'


def test_local_registry_api_enforces_origin_and_never_connects_to_pod(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, 'inspect_repo', lambda *args: meta())
    monkeypatch.setattr(bridge, 'exchange', lambda *args, **kwargs: pytest.fail('SSH must not run during registration'))
    app = FastAPI(); app.include_router(bridge.modeltest_ssh_router(tmp_path))
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as client:
        path = '/api/modeltest/ssh/models'; headers = {'X-Modeltest-Local': '1'}
        assert client.get(path).status_code == 403
        assert client.post(path + '/inspect', json={'value': 'Qwen/Qwen3.5-4B'}, headers=headers).json()['status'] == 'unverified'
        response = client.post(path, json={'value': 'Qwen/Qwen3.5-4B'}, headers=headers); assert response.status_code == 200
        item = response.json(); assert client.get(path, headers=headers).json()['models'][0]['id'] == item['id']
        assert client.put(path + '/' + item['id'], json={**item, 'name': '새 이름'}, headers=headers).json()['name'] == '새 이름'
        assert client.delete(path + '/' + item['id'], headers=headers).status_code == 200


def test_general_metrics_and_geometry_remain_separate(tmp_path):
    image = tmp_path / 'image.png'; Image.new('RGB', (32, 32), color='red').save(image)
    result = adapters.classical_pair('phash', [image, image], remote)
    assert result['hash_distance'] == 0 and 'cosine_similarity' not in result
    assert not adapters.geometry([], [])['geometry_verified']
    docs = [{'record_id': 'a', 'text': 'red logo'}, {'record_id': 'b', 'text': 'blue background'}]
    ranking = adapters.lexical_rank('red logo', docs)
    assert ranking[0]['record_id'] == 'a' and ranking[0]['metric'] == 'bm25'
    assert adapters.fuse_rankings(ranking, ranking)[0]['ranking_score'] == pytest.approx(2 / 61)


def test_sentence_adapter_keeps_repository_defaults_and_explicit_input_modalities(tmp_path, monkeypatch):
    torch = pytest.importorskip('torch'); calls = []
    (tmp_path / 'config.json').write_text('{"model_type":"qwen3"}')
    class FakeSentenceModel:
        max_seq_length = 32768
        def __init__(self, source, **kwargs): calls.append(kwargs)
        def encode(self, values, **kwargs):
            assert values == [{'text': 'https://example.com/text-is-not-a-download'}]
            assert kwargs['normalize_embeddings'] is True and kwargs['prompt_name'] == 'query'
            return np.asarray([[1., 0.]])
        def predict(self, pairs, **kwargs):
            assert pairs == [({'text': 'query'}, {'text': 'document'})]
            assert isinstance(kwargs['activation_fn'], torch.nn.Identity)
            return np.asarray([-2.0])
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=FakeSentenceModel, CrossEncoder=FakeSentenceModel))
    model = adapters.Adapter.__new__(adapters.Adapter)
    model.family = 'sentence_text'; model.options = {**catalog.FAMILIES['sentence_text']['defaults'], 'prompt_name': 'query'}; model.source = tmp_path
    model.load(); assert model.model.max_seq_length == 32768
    assert calls[0]['local_files_only'] is True and calls[0]['trust_remote_code'] is False and calls[0]['model_kwargs']['use_safetensors'] is True
    assert model.embed(['https://example.com/text-is-not-a-download'], 'text').shape == (1, 2)
    (tmp_path / 'modules.json').write_text(json.dumps([{'path': '1_LogitScore', 'type': 'sentence_transformers.cross_encoder.modules.logit_score.LogitScore'}]))
    model.family = 'cross_encoder'; model.options = {**catalog.FAMILIES['cross_encoder']['defaults'], 'instruction': 'custom instruction'}
    model.load(); assert calls[-1]['prompts'] == {'classification': 'custom instruction'}
    assert model.rerank('query', [{'text': 'document'}]) == [-2.0]


def test_unprepared_profile_fails_before_snapshot_download(tmp_path, monkeypatch):
    monkeypatch.setattr(adapters, 'cache_root', lambda value: tmp_path)
    monkeypatch.setattr(catalog, 'environment_errors', lambda *args: [])
    monkeypatch.setattr(adapters, 'snapshot', lambda *args, **kwargs: pytest.fail('must not download or inspect cache'))
    with pytest.raises(ValueError, match='먼저 준비'): adapters.Adapter(profile(), None, remote)


def test_training_artifact_metadata_is_required_and_thresholds_do_not_carry(tmp_path, monkeypatch):
    def inspect(repo, revision=None):
        if repo == 'owner/head':
            result = meta(repo, ''); result['files'] = ['head.safetensors']; return result
        return meta('google/siglip2-base-patch16-224', 'siglip2')
    monkeypatch.setattr(catalog, 'inspect_repo', inspect)
    registry = Registry(tmp_path / 'models.json')
    data = {'value': 'google/siglip2-base-patch16-224', 'family': 'linear_head', 'options': {'encoder_family': 'clip', 'pooling': 'projection'}, 'training': {'base_model_id': 'google/siglip2-base-patch16-224', 'base_revision': SHA, 'trained': True, 'head': {'model_id': 'owner/head', 'revision': SHA, 'filename': 'missing.safetensors', 'sha256': 'b' * 64}, 'labels': ['yes', 'no'], 'preprocessing': {'source': 'repository'}, 'thresholds': {'yes': .8, 'no': .8}}}
    missing = registry.save(data); assert missing['status'] == 'needs_preparation' and any('파일이' in reason for reason in missing['reasons'])
    data['training']['head']['filename'] = 'head.safetensors'
    item = registry.save(data); assert item['status'] == 'unverified', item['reasons']
    changed = registry.save({**item, 'options': {**item['options'], 'normalize': False}}, item['id'])
    assert changed['training']['thresholds'] == {} and changed['status'] == 'needs_preparation'
