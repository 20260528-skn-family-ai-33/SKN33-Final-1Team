import React from 'react';
import { Icon } from '../../components/Icon.jsx';
import { changeNotice } from './reviewRelations.js';
import '../../styles/review-relations.css';

const day = value => value ? new Date(value).toLocaleDateString('ko-KR') : '';
// 달라진 항목이 많으면 기준 목록을 밀어내므로 앞의 LEAD개만 보이고 나머지는 접어 둔다.
const LEAD = 3;
const changeRow = change => <li key={change.key}>
  <span className="rv-relation-tag">{change.tag}</span><span className="rv-relation-label">{change.label}</span>
  <span className="rv-relation-values">{change.before && <del>{change.before}</del>}
    {change.before && change.after && <span aria-hidden="true"> → </span>}{change.after && <ins>{change.after}</ins>}</span>
</li>;

// 새 문서의 기준이 이전 승인 기준과 달라졌을 때, 달라진 항목과 이전 기준으로 한 검수를 알려 준다.
export default function ChangeImpactNotice({ impact }) {
  const notice = changeNotice(impact);
  if (!notice) return null;
  const rest = notice.changes.slice(LEAD);
  return <section className="rv-relation-notice" aria-label="이전 기준과 달라진 항목">
    <div className="rv-relation-notice-head"><Icon name="info" /><div>
      <strong>{notice.headline}</strong><p>{notice.detail}</p></div></div>
    <ul className="rv-relation-changes">{notice.changes.slice(0, LEAD).map(changeRow)}</ul>
    {rest.length > 0 && <details className="rv-relation-more"><summary>나머지 {rest.length}개 항목 보기</summary>
      <ul className="rv-relation-changes">{rest.map(changeRow)}</ul>
    </details>}
    {notice.action && <details className="rv-relation-runs"><summary>{notice.action}</summary>
      <ul>{notice.runs.map(run => <li key={run.runId}>
        <a href={'?framework=' + encodeURIComponent(run.runId)}>{day(run.createdAt)} 포스터 검수</a>
        <span className={`rv-relation-status${run.approved ? ' is-approved' : ''}`}>{run.status}</span>
      </li>)}</ul>
      {notice.truncated && <p>검수가 많아 일부만 표시했어요.</p>}
    </details>}
    <p className="rv-relation-basis">{notice.basis} 이전 검수의 판정과 승인 기록은 바뀌지 않아요.</p>
  </section>;
}
