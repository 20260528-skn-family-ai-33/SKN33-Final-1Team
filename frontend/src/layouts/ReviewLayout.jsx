import React from 'react';
import { useNavigate } from 'react-router';
import { Icon } from '../components/Icon.jsx';
import { SiteHeader } from '../components/SiteHeader.jsx';
import { useWorkspaceReset } from '../workspace/WorkspaceSession.jsx';
import '../styles/review-v2.css';
import '../styles/review-interactions.css';

const posterSteps = { input: '자료 입력', running: '검수 진행', results: '결과 확인', report: '보고서' };

export function WorkspaceProjectBar({ name, version, step, steps, canNavigate, navigate, locked, projectEditor, projectTools, children }) {
  const go = useNavigate();
  const resetWorkspace = useWorkspaceReset();
  return <div className="rv-projectbar">
    <div className="rv-project-identity">
      <button type="button" className="rv-icon-button" aria-label="서비스 메인으로" disabled={locked}
        onClick={() => { resetWorkspace(); go('/'); }}><Icon name="grid" /></button>
      <div><span className="rv-eyebrow">내 검수 프로젝트</span>{projectEditor
        ? <div className="rv-project-name-field"><input type="text" id={projectEditor.id} aria-label="프로젝트 이름" autoComplete="off"
          maxLength={projectEditor.maxLength} value={name} placeholder="새로운 검수" disabled={locked || projectEditor.disabled}
          onChange={event => projectEditor.onChange(event.target.value)} /><Icon name="pencil" /></div>
        : <strong>{name || '새로운 검수'}</strong>}</div>
      <span className="rv-version">{version}</span>
      {projectTools}
    </div>
    <div className="rv-project-actions">
      <nav className="rv-step-nav" aria-label="검수 단계">{Object.entries(steps).map(([key, label], index) => <button type="button" key={key}
        disabled={locked || !canNavigate(key)} className={key === step ? 'is-active' : ''}
        onClick={() => navigate(key)} aria-current={key === step ? 'step' : undefined}>
        <span>{String(index + 1).padStart(2, '0')}</span>{label}</button>)}</nav>
      {children}
    </div>
  </div>;
}

export default function ReviewLayout({ state, busy, navigate, children,
  canNavigate, versionLabel, mainClassName = '', workspaceStep, topbarLocked = false, projectEditor, projectTools, projectActions }) {
  const navigationLocked = topbarLocked || busy;
  const enabled = canNavigate || (key => key === 'input' || key === 'criteria' && state.criteria.length > 0
    || key === 'running' && state.review && state.review.status !== 'completed' || key === 'results' && state.review?.status === 'completed');
  return <div className={`rv-workspace rv-workspace--${workspaceStep || state.step}`}>
    <a href="#review-content" className="rv-skip">작업 영역으로 이동</a>
    <SiteHeader current="/review/new" locked={navigationLocked} lockReason="처리 중에는 화면을 이동할 수 없습니다." />
    {navigationLocked && <span id="mt-lock-reason" className="sr-only">처리 중에는 화면을 이동할 수 없습니다.</span>}
    <WorkspaceProjectBar name={state.projectName || ''} step={state.step === 'criteria' ? 'input' : state.step} steps={posterSteps}
      projectEditor={projectEditor} projectTools={projectTools} locked={navigationLocked}
      version={versionLabel || (state.snapshot ? `기준 v${state.snapshot.version}` : '작업 중')} canNavigate={enabled} navigate={navigate}>
      {projectActions}
    </WorkspaceProjectBar>
    <main id="review-content" className={`rv-main ${mainClassName}`.trim()} tabIndex={-1} aria-busy={busy || undefined}>{children}</main>
  </div>;
}
