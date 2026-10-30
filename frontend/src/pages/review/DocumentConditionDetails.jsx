import React from 'react';

// 짧은 검수 문구와 별개로 문서의 수준·예외를 확인할 수 있게 한다.
export default function DocumentConditionDetails({ condition }) {
  if (!condition) return null;
  const level = condition.level === '명시 없음' ? '수준 미지정' : condition.level;
  return <details className="document-condition-details">
    <summary>{level} · 상세 기준</summary>
    <p>{condition.inspection || '문서에 별도 판단 기준이 명시되지 않았습니다.'}</p>
    {condition.sources?.length > 1 && <div>
      <p>병합한 원문 근거 · {condition.sources.length}곳</p>
      {condition.sources.map((source, i) => <p key={`${source.source_id}-${i}`}>
        {source.page}페이지 · {source.source}
      </p>)}
    </div>}
  </details>;
}
