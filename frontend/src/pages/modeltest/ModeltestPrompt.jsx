import React, { useId } from 'react';
import { promptSpec, promptText } from './modeltestPrompts.js';
import ModeltestHelp from './ModeltestHelp.jsx';

export default function ModeltestPrompt({ kind, modelId, profile, value, onChange, disabled, label }) {
  const id = useId();
  const spec = promptSpec(kind, modelId, profile);
  if (!spec) return null;
  const text = promptText(value, spec); const changed = !!text.trim() && text !== spec.default;
  return <details className="mt-prompt mt-settings-wide">
    <summary>프롬프트 편집 <span>{changed ? '수정됨' : '기본값 사용'}</span></summary>
    <div className="mt-title-help"><label htmlFor={id}>{spec.type === 'labels' ? '탐지할 문구 · 한 줄에 하나' : '모델에 전달할 지시문'}</label><ModeltestHelp label={label}>
      <p>수정한 내용은 기본 프롬프트를 대체합니다. 비워 두면 기본값을 사용합니다. 첨부 자료와 연결된 입력 결과는 계속 전달됩니다.</p>
      {spec.type === 'labels' && <p>탐지 모델은 지시문 대신 검색 문구를 받습니다. 문구당 최대 200자, 총 32개입니다.</p>}
    </ModeltestHelp></div>
    <textarea id={id} aria-label={label} rows={6} maxLength={8000} disabled={disabled} value={text} spellCheck={false} onChange={event => onChange({ promptOverride: event.target.value })} />
    <div><small>{text.length.toLocaleString()} / 8,000자</small><button type="button" className="text-btn" disabled={disabled} onClick={() => onChange({ promptOverride: null, prompt: '' })}>기본값 복원</button></div>
    {spec.type === 'ocr' && <p>기본 OCR: 지시를 변경하면 인식 품질·출력 형식이 달라질 수 있습니다. 문서 레이아웃 분석 기능이 추가되는 것은 아닙니다.</p>}
    {kind === 'logo_extract' && spec.type === 'instruction' && <p>로고 추출 결과는 bbox 배열의 JSON과 0~1000 좌표 형식을 유지해야 합니다.</p>}
  </details>;
}
