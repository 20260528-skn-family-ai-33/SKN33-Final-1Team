import React from 'react';

// 프로젝트와 검수 항목 수를 공통 요약 카드로 표시한다.
export function Summary({ projectName, title, count, countLabel = '선택한 검수 항목', rows, children }) { return <div className="summary-card"><p className="summary-overline">{projectName || '새로운 검수'}</p><h2>{title}</h2><div className="summary-total"><span>{countLabel}</span><strong>{count}<span>개</span></strong></div><div className="summary-rows">{rows.map(([label, value]) => <div className="summary-row" key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>{children}</div>; }
