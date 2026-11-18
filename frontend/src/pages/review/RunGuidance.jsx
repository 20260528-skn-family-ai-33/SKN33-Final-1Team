import React from 'react';
import { useRunAdvice } from './useRunAdvice.js';

export default function RunGuidance({ path, status }) {
  const { busy, note, error, request } = useRunAdvice(path + '/guidance', result => result.execution_status === status, {
    stale: '검수 상태가 바뀌었습니다. 현재 상태를 확인해 주세요.',
    unavailable: '실행 안내를 불러오지 못했습니다. 검수 상태와 오류는 화면에서 확인할 수 있습니다.',
  });

  return <section className="rv-panel rv-framework-actions" aria-label="실행 안내">
    <button type="button" className="rv-button secondary" disabled={busy} aria-busy={busy} onClick={request}>
      {busy ? '실행 안내를 확인하고 있어요' : '다음 할 일 안내'}
    </button>
    {error && <p role="alert">{error}</p>}
    {note && <div role="status">
      <p>{note.summary}</p>
      <details className="rv-framework-record">
        <summary>AI 설명</summary>
        <p>{note.model_summary}</p>
      </details>
    </div>}
  </section>;
}
