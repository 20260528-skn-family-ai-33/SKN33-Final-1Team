import React from 'react';
import { useRunAdvice } from './useRunAdvice.js';

export default function RevisionComparison({ path, runId, parentId, titleFor, verdictFor }) {
  const { busy, note, error, request } = useRunAdvice(path + '/revision-comparison',
    result => result.compared === true && result.child_run_id === runId && result.parent_run_id === parentId, {
      stale: '수정 전후 결과를 아직 비교할 수 없습니다. 현재 검수 상태를 확인해 주세요.',
      unavailable: '수정 전후 비교를 불러오지 못했습니다. 저장된 검수 결과는 이 화면에서 볼 수 있습니다.',
    });
  const groups = note && [
    ['해결', note.resolved],
    ['미해결', note.unresolved],
    ['새 위반·확인 필요', note.new_violations],
  ];

  return <section className="rv-panel rv-framework-actions" aria-label="수정 전후 비교">
    <button type="button" className="rv-button secondary" disabled={busy} aria-busy={busy} onClick={request}>
      {busy ? '수정 전후를 비교하고 있어요' : '수정 전후 비교'}
    </button>
    {error && <p role="alert">{error}</p>}
    {groups && <div role="status">
      <dl>{groups.map(([label, ids]) => <div key={label}>
        <dt>{label} {ids.length}건</dt>
        {ids.length > 0 && <dd><ul>{ids.map(id => <li key={id}>
          {titleFor(id)}{verdictFor(id) === 'review' ? ' · 확인 필요' : ''}
        </li>)}</ul></dd>}
      </div>)}</dl>
      <details className="rv-framework-record">
        <summary>AI 설명</summary>
        <p>{note.model_summary}</p>
      </details>
    </div>}
  </section>;
}
