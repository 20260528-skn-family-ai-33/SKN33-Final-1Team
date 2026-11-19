import React from 'react';
import { Link } from 'react-router';
import { extractionFailureItems } from './extractionRejections.js';

export default function ExtractionFailure({ failure, runId }) {
  const items = extractionFailureItems(failure);
  if (!items.length) return null;
  return <section className="rv-extraction-failure rv-rejections" aria-label="분석하지 못한 항목과 원인">
    <p>다음 항목을 검수 기준으로 반영하지 못했습니다. 원문과 원인을 확인해 주세요.</p>
    <ol>{items.map((item, index) => <li key={`${item.code}:${index}`}>
      <p><strong>{item.candidate}</strong> · {item.reason}</p>
      {item.fields.length > 0 && <p className="rv-extraction-failure-fields">확인할 내용: {item.fields.join(' · ')}</p>}
      {item.quote ? <details open><summary>{item.source}</summary><blockquote>{item.quote}</blockquote></details>
        : <p>이 항목의 원문 범위를 확인하지 못했습니다.</p>}
      <small>오류 코드: {item.code}</small>
    </li>)}</ol>
    {runId && <Link className="text-btn" to={'/review/new?framework=' + encodeURIComponent(runId)}>이 분석의 실패 기록 보기</Link>}
  </section>;
}
