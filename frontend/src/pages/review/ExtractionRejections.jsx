import React from 'react';
import { Icon } from '../../components/Icon.jsx';
import { rejectionGroups } from './extractionRejections.js';
import '../../styles/review-relations.css';

// 원문 검증에서 제외된 후보 안내. 기준 목록을 밀어내지 않게 안내 한 상자로 접어 두고,
// 펴면 사유별 묶음과 그 사유로 빠진 원문을 한 줄씩 보여 준다. 원문 줄을 누르면 전문이 펴진다.
// 사유 코드(A02_…)는 문구가 있으면 화면에 적지 않고 마우스를 올렸을 때만 보인다.
export default function ExtractionRejections({ items }) {
  const groups = rejectionGroups(items);
  if (!groups.length) return null;
  return <section className="rv-relation-notice rv-rejections" aria-label="원문 검증에서 제외된 후보">
    <div className="rv-relation-notice-head"><Icon name="warning" /><div>
      <strong>원문 검증에서 후보 {items.length}개를 제외했어요.</strong>
      <p>기준 목록에는 넣지 않았습니다. 빠진 요구사항이 없는지 원문을 확인하고 필요한 기준을 보완해 주세요.</p></div></div>
    <details className="rv-rejection-list">
      <summary>제외한 후보 사유별로 보기</summary>
      <ul>{groups.map(group => <li key={group.code}>
        <p className="rv-rejection-reason" title={group.code}><strong>{group.reason}</strong><span>{group.count}개</span>
          {!group.known && <code>{group.code}</code>}</p>
        <ul>{group.sources.map(source => <li key={source.key}>
          {source.quote ? <details className="rv-rejection-source">
            <summary><span className="rv-rejection-page">{source.page}쪽</span>
              <span className="rv-rejection-preview">{source.quote}</span>
              <span className="rv-rejection-candidates">{source.label}</span></summary>
            <p>{source.quote}</p>
          </details> : <p className="rv-rejection-source is-empty"><span className="rv-rejection-page">원문 없음</span>
            <span className="rv-rejection-preview">확인 가능한 원문이 없습니다.</span>
            <span className="rv-rejection-candidates">{source.label}</span></p>}
        </li>)}</ul>
      </li>)}</ul>
    </details>
  </section>;
}
