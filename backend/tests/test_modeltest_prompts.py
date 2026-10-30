"""Prompt transport checks with fake models only; no downloads or Pod connection."""
from types import SimpleNamespace

import pytest

from backend.app import modeltest_remote as remote, modeltest_prompts as prompts, modeltest_ssh as bridge
from backend.app.modeltest_adapters import Adapter


@pytest.mark.parametrize('kind', ['conditions', 'text_extract', 'visual_extract', 'logo_compare', 'text_compare', 'visual_compare', 'text_generate'])
def test_generation_override_replaces_instruction_and_preserves_evidence(kind):
    seen = []
    models = SimpleNamespace(generate=lambda text, images, vision: seen.append((text, images, vision)) or 'result')
    stage = {'id': kind, 'model_id': remote.QWEN}
    remote.run_generated(models, stage, evidence='source document', images=['image'], require_vision=True)
    assert seen[-1] == (prompts.DEFAULTS['generation'][kind] + '\n\nsource document', ['image'], True)
    remote.run_generated(models, {**stage, 'prompt': 'legacy', 'prompt_override': 'Only custom instruction'}, evidence='source document', images=['image'], require_vision=True)
    assert seen[-1] == ('Only custom instruction\n\nsource document', ['image'], True)
    remote.run_generated(models, {**stage, 'prompt_override': '  '}, evidence='source document')
    assert seen[-1][0] == prompts.DEFAULTS['generation'][kind] + '\n\nsource document'


def test_legacy_agent_instructions_remain_additive_until_edited():
    stage = {'id': 'text_generate', 'model_id': remote.QWEN, 'prompt': '기존 지시'}
    assert prompts.text(stage) == '기존 지시\n\n' + prompts.DEFAULTS['generation']['text_generate']


def test_generated_logo_parser_receives_only_the_replacement_instruction(monkeypatch):
    pytest.importorskip('torch')
    monkeypatch.setattr(remote, 'image_of', lambda path: SimpleNamespace(size=(200, 100)))
    models = remote.Models(); models.kind = 'vision'; seen = []
    models.generate = lambda text, images, vision: seen.append(text) or '[{"bbox": [0, 0, 500, 500]}]'
    result = remote.run_generated(models, {'id': 'logo_extract', 'model_id': remote.QWEN, 'prompt_override': 'Find sponsor marks as bbox JSON.'}, images=['image'])
    assert seen == ['Find sponsor marks as bbox JSON.']
    assert result['logos'][0]['bbox'] == [0, 0, 100, 50]


@pytest.mark.parametrize('value', [[], 3, 'x' * 8001], ids=['list', 'number', 'too-long'])
def test_invalid_override_rejected_before_model_load(value):
    with pytest.raises(ValueError, match='8,000'):
        remote.validate_model_stage({'id': 'text_extract', 'model_id': remote.QWEN, 'prompt_override': value})


def test_non_prompt_models_reject_overrides_and_detection_keeps_label_limits():
    with pytest.raises(ValueError, match='교체를 지원하지'):
        remote.validate_model_stage({'id': 'logo_compare', 'model_id': remote.XFEAT, 'prompt_override': 'ignored?'})
    stage = {'id': 'logo_extract', 'model_id': remote.OWLV2, 'prompt_override': 'sponsor logo\nbrand mark'}
    assert prompts.labels(stage) == ['sponsor logo', 'brand mark']
    for value in ['same\nsame', 'x' * 201, '\n'.join(str(i) for i in range(33))]:
        with pytest.raises(ValueError, match='탐지 문구'): remote.validate_model_stage({**stage, 'prompt_override': value})


def test_registered_ocr_uses_custom_instruction_without_modifying_profile():
    adapter = Adapter.__new__(Adapter); adapter.family = 'paddle_vl'; adapter.remote = remote
    seen = []; adapter.generate = lambda prompt, images, vision: seen.append(prompt) or 'recognized'
    assert adapter.ocr('image', prompt='Read only the headline')['text'] == 'recognized'
    assert seen == ['Read only the headline']
    adapter.ocr('image'); assert seen[-1] == prompts.DEFAULTS['ocr']


def test_ssh_bundle_contains_prompt_defaults_without_local_json():
    space = {'__name__': 'offline_prompt_runner'}
    exec(compile(bridge.runner_source(), 'bundle', 'exec'), space)
    assert space['prompts'].DEFAULTS == prompts.DEFAULTS
    assert space['prompts'].text({'id': 'text_extract', 'model_id': remote.QWEN, 'prompt_override': 'custom'}) == 'custom'
