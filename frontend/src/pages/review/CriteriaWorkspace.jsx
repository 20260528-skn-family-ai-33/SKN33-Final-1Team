import React from 'react';
import { Icon } from '../../components/Icon.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';

// 기준만 보는 화면의 틀. 승인한 뒤의 기준 보기(읽기 전용)와 저장된 예전 검수·로컬 미리보기가 함께 쓴다.
// 승인 전 초안은 이 틀을 쓰지 않는다. 자료 입력 한 화면(InputPage)의 검수 항목 칸으로 들어간다.
// Callers provide their own criteria data and approval commands.
export default function CriteriaWorkspace({ canvas, children, actions, versionText, locked, error,
  kicker = '자료 입력 / 기준 확인', title = '검수할 항목을 확정해 주세요.', lead = '포스터와 요구사항 원문을 함께 보며 검수 기준을 확인하세요.' }) {
  return <section className="rv-criteria-page" aria-labelledby="page-title">
    <div className="rv-workspace-intro"><div><span className="rv-eyebrow">{kicker}</span>
      <h1 id="page-title" tabIndex={-1}>{title}</h1>
      <p>{lead}</p>
    </div><span className={`rv-criteria-version${locked ? ' is-locked' : ''}`}><Icon name={locked ? 'lock' : 'list'} />{versionText}</span></div>
    <div className="rv-editor-grid">
      <div className="rv-criteria-canvas-column">{canvas}</div>
      <aside className="rv-panel rv-criteria-inspector" aria-label="검수 기준 검토">{children}</aside>
    </div>
    {error && <div className="rv-criteria-error"><ErrorMessage message={error} /></div>}
    {actions}
  </section>;
}
