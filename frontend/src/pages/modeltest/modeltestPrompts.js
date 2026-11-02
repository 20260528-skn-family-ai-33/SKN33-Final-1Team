import defaults from '../../../../backend/app/modeltest_prompts.json' with { type: 'json' };

export function promptSpec(kind, modelId, profile) {
  const family = profile?.family || defaults.models[modelId];
  if (['qwen_text', 'qwen_vision'].includes(family) && defaults.generation[kind]) return { type: 'instruction', default: defaults.generation[kind] };
  if (['paddle_vl', 'paddle_gguf'].includes(family) && kind === 'text_extract') return { type: 'ocr', default: defaults.ocr };
  if (defaults.labels[family] && kind === 'logo_extract') return { type: 'labels', default: (profile?.options?.labels || defaults.labels[family]).join('\n') };
  return null;
}

export function promptText(value, spec) {
  return value.promptOverride ?? (value.prompt?.trim() && spec.type === 'instruction' ? `${value.prompt}\n\n${spec.default}` : spec.default);
}

export function promptFields(kind, value, modelId, profile) {
  const spec = promptSpec(kind, modelId, profile);
  if (!spec) return {};
  const text = promptText(value, spec);
  if (typeof text !== 'string' || text.length > 8000) throw new Error('프롬프트는 8,000자 이하로 입력해 주세요.');
  if (spec.type === 'labels' && text.trim()) {
    const labels = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    if (labels.length > 32 || labels.some(label => label.length > 200) || new Set(labels).size !== labels.length) throw new Error('탐지 문구는 중복 없이 1~32개, 문구당 200자 이하여야 합니다.');
  }
  if (value.promptOverride == null) return value.prompt?.trim() && spec.type === 'instruction' ? { prompt: value.prompt } : {};
  return { prompt_override: text };
}
