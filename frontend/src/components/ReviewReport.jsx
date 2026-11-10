import React from 'react';
import '../styles/framework-report.css';

export default function ReviewReport({ projectName, mode, verdicts, detail, onBack, children }) {
  const counts = [['all', '전체', verdicts.length], ...[['pass', '충족'], ['review', '확인 필요'], ['fail', '위반']]
    .map(([key, label]) => [key, label, verdicts.filter(value => value === key).length])];
  const other = verdicts.length - counts.slice(1).reduce((sum, [, , count]) => sum + count, 0);
  if (other) counts.push(['other', '그 외', other]);
  return <article className="rv-report-print" aria-label="검수 보고서">
    <div className="rv-report-actions">
      {onBack && <button type="button" className="rv-button secondary" onClick={onBack}>결과 확인으로 돌아가기</button>}
      <button type="button" className="rv-button secondary" onClick={() => window.print()}>인쇄 / PDF 저장</button>
    </div>
    <div className="rv-report-paper">
      <header className="rv-report-heading"><div><strong>Creative Checker</strong><span>{mode}</span></div>
        <h2 id={onBack ? 'page-title' : undefined} tabIndex={-1}>검수 보고서</h2><p>{projectName}</p>{detail && <p>{detail}</p>}</header>
      <section aria-labelledby="report-summary"><h3 id="report-summary">판정 요약</h3>
        <div className="rv-report-counts">{counts.map(([key, label, count]) => <span key={key} className={`is-${key}`}><strong>{label}</strong> {count}건</span>)}</div>
      </section>
      {children}
    </div>
  </article>;
}
