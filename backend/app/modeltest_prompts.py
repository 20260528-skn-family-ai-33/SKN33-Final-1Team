"""Shared review instructions; the SSH bundle injects the same JSON as the UI."""
import json
from pathlib import Path

if 'DEFAULTS' not in globals():
    DEFAULTS = json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))


def spec(stage):
    kind = stage.get('kind', stage.get('id'))
    profile = stage.get('model_profile') or {}
    family = profile.get('family') or DEFAULTS['models'].get(stage.get('model_id'))
    if family in ('qwen_text', 'qwen_vision') and kind in DEFAULTS['generation']:
        return {'type': 'instruction', 'default': DEFAULTS['generation'][kind]}
    if family in ('paddle_vl', 'paddle_gguf') and kind == 'text_extract':
        return {'type': 'ocr', 'default': DEFAULTS['ocr']}
    if family in DEFAULTS['labels'] and kind == 'logo_extract':
        labels = profile.get('options', {}).get('labels', DEFAULTS['labels'][family])
        return {'type': 'labels', 'default': '\n'.join(labels)}
    return None


def text(stage):
    definition = spec(stage)
    override = stage.get('prompt_override')
    for field in ('prompt', 'prompt_override'):
        value = stage.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > 8000):
            raise ValueError('프롬프트는 8,000자 이하의 문자열이어야 합니다.')
    if definition is None:
        if override is not None: raise ValueError('이 모델 단계는 프롬프트 교체를 지원하지 않습니다.')
        return ''
    value = override if override is not None and override.strip() else definition['default']
    # Existing agent instructions were additive; new overrides replace the whole instruction.
    if override is None and stage.get('prompt') and definition['type'] == 'instruction':
        value = stage['prompt'] + '\n\n' + value
    if definition['type'] == 'labels':
        labels = [line.strip() for line in value.splitlines() if line.strip()]
        if not 1 <= len(labels) <= 32 or any(len(label) > 200 for label in labels) or len(set(labels)) != len(labels):
            raise ValueError('탐지 문구는 중복 없이 한 줄에 하나씩 1~32개, 문구당 200자 이하여야 합니다.')
    return value


def compose(stage, evidence=''):
    instruction = text(stage)
    return instruction + ('\n\n' + evidence if evidence else '')


def labels(stage):
    return [line.strip() for line in text(stage).splitlines() if line.strip()]
