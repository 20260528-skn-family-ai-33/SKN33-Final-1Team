import React from 'react';
import { frameworkPrefix } from './frameworkClient.js';
import { revisionView } from './reviewRelations.js';
import '../../styles/review-relations.css';

const moment = value => value ? new Date(value).toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' }) : '';

// 원본 검수 → 수정본 재검수와 각 검수에 연결된 담당자 기록. 수정본이 없으면 아무것도 그리지 않는다.
export default function RevisionHistory({ history }) {
  const view = revisionView(history);
  if (!view) return null;
  return <details className="rv-revision-history">
    <summary>
      <strong>수정본 검수 이력</strong>
      <span className="rv-revision-fact">재검수 <b>{view.rereviews}회</b></span>
      <span className="rv-revision-fact">최종 승인된 수정본 <b>{view.final || (view.waiting ? '담당자 승인 대기' : '없음')}</b></span>
    </summary>
    <ol className="rv-revision-rows">{view.rows.map(row => <li key={row.runId}>
      <details open={row.current}>
        <summary>
          <span className="rv-revision-title"><strong>{row.title}</strong>
            <small>{moment(row.createdAt)}{row.current ? ' · 지금 보고 있는 검수' : ''}</small></span>
          <span className={`rv-relation-status${row.approved ? ' is-approved' : ''}`}>{row.status}</span>
        </summary>
        <dl className="rv-revision-detail">
          <div><dt>검수한 파일</dt><dd className="rv-revision-file">
            {row.assetVersionId && <img alt={`${row.title} 포스터`} loading="lazy"
              src={frameworkPrefix + '/images/' + encodeURIComponent(row.assetVersionId) + '/content'} />}
            {row.current ? <span>이 화면의 포스터</span>
              : <a href={'?framework=' + encodeURIComponent(row.runId)}>이 검수 결과 열기</a>}
          </dd></div>
          <div><dt>적용 기준</dt><dd>{row.criteria}</dd></div>
          <div><dt>담당자 기록</dt><dd><ul>{row.records.map(line => <li key={line.key}>
            {line.label && <b>{line.label}</b>}{line.label && line.reason ? ' · ' : ''}{line.reason}
            {line.at && <small> {moment(line.at)}</small>}</li>)}</ul></dd></div>
        </dl>
      </details>
    </li>)}</ol>
    {view.truncated && <p className="rv-relation-basis">이력이 길어 일부만 표시했어요.</p>}
  </details>;
}
