import React from 'react';
import { Icon } from '../../components/Icon.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';

// The live framework, saved reviews and local preview share this workspace.
// Callers provide their own criteria data and approval commands.
export default function CriteriaWorkspace({ canvas, children, actions, versionText, locked, error }) {
  return <section className="rv-criteria-page" aria-labelledby="page-title">
    <div className="rv-workspace-intro"><div><span className="rv-eyebrow">02 / 기준 확인</span>
      <h1 id="page-title" tabIndex={-1}>검수할 항목을 확정해 주세요.</h1>
      <p>포스터와 요구사항 원문을 함께 보며 검수 기준을 확인하세요.</p>
    </div><span className={`rv-criteria-version${locked ? ' is-locked' : ''}`}><Icon name={locked ? 'lock' : 'list'} />{versionText}</span></div>
    <div className="rv-editor-grid">
      <div className="rv-criteria-canvas-column">{canvas}</div>
      <aside className="rv-panel rv-criteria-inspector" aria-label="검수 기준 검토">{children}</aside>
    </div>
    {error && <div className="rv-criteria-error"><ErrorMessage message={error} /></div>}
    {actions}
  </section>;
}
