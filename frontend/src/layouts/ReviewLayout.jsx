import React, { useRef } from 'react';
import { useNavigate } from 'react-router';
import { Icon } from '../components/Icon.jsx';
import { SiteHeader } from '../components/SiteHeader.jsx';
import { useWorkspaceReset } from '../workspace/WorkspaceSession.jsx';
import '../styles/review-v2.css';
import '../styles/review-interactions.css';

// 검수 유형마다 상단에 그 유형의 처리 단계만 보여 준다. 자막·둘 다는 문구 검수 단계, 로고는 로고 대조 단계.
export const posterSteps = { input: '자료 입력', running: '검수 진행', results: '결과 확인', report: '보고서' };
export const logoSteps = { input: '자료 입력', candidates: '후보 찾기', confirm: '위치 확정', compare: '정밀 대조' };

// 검수 이름 칸: 비어 있으면 흐린 안내 글자로 보이고(값처럼 보이지 않게), 연필을 누르면 바로 입력한다.
// 입력 중에는 글자 수를 보이고, Enter·Esc로 입력을 끝낸다.
function ProjectNameField({ editor, value = '', locked }) {
  const input = useRef(null);
  const off = locked || editor.disabled;
  return <div className="rv-project-name-field">
    <input ref={input} type="text" id={editor.id} aria-label="검수 이름" autoComplete="off" maxLength={editor.maxLength}
      value={value} placeholder="이름 없는 검수 (선택)" disabled={off} onChange={event => editor.onChange(event.target.value)}
      onKeyDown={event => { if (event.key === 'Enter' || event.key === 'Escape') event.currentTarget.blur(); }} />
    {editor.maxLength && <span className="rv-project-name-count" aria-hidden="true">{value.length}/{editor.maxLength}</span>}
    <button type="button" aria-label="검수 이름 고치기" title="검수 이름 고치기" disabled={off}
      onClick={() => { input.current?.focus(); input.current?.select(); }}><Icon name="pencil" /></button>
  </div>;
}

function versionHint(version) {
  if (version === '작업 중') return '검수 기준을 확정하기 전입니다. 확정하면 ‘기준 v1’처럼 바뀝니다.';
  if (/^기준 v\d+/.test(version || '')) return '확정한 검수 기준의 버전입니다.';
  return undefined;
}

export function WorkspaceProjectBar({ name, version, step, steps, canNavigate, navigate, locked, projectEditor, projectTools, children }) {
  const go = useNavigate();
  const resetWorkspace = useWorkspaceReset();
  return <div className="rv-projectbar">
    <div className="rv-project-identity">
      <button type="button" className="rv-icon-button" aria-label="서비스 메인으로" title="서비스 메인으로" disabled={locked}
        onClick={() => { resetWorkspace(); go('/'); }}><Icon name="grid" /></button>
      <div className="rv-project-field"><span className="rv-project-field-label">검수 이름</span>{projectEditor
        ? <ProjectNameField editor={projectEditor} value={name || ''} locked={locked} />
        : <strong title={name || undefined}>{name || '이름 없는 검수'}</strong>}</div>
      <div className="rv-project-field rv-project-field--status"><span className="rv-project-field-label">상태</span>
        <span className="rv-version" title={versionHint(version)}>{version}</span></div>
      {projectTools}
    </div>
    <div className="rv-project-actions">
      {steps && <nav className="rv-step-nav" aria-label="검수 단계">{Object.entries(steps).map(([key, label], index) => <button type="button" key={key}
        disabled={locked || !canNavigate(key)} className={key === step ? 'is-active' : ''}
        onClick={() => navigate(key)} aria-current={key === step ? 'step' : undefined}>
        <span>{String(index + 1).padStart(2, '0')}</span>{label}</button>)}</nav>}
      {children}
    </div>
  </div>;
}

// steps를 null로 주면 단계 표시를 숨긴다(검수 유형을 고르기 전).
export default function ReviewLayout({ state, busy, navigate, children, steps = posterSteps,
  canNavigate, versionLabel, mainClassName = '', workspaceStep, topbarLocked = false, projectEditor, projectTools, projectActions }) {
  const navigationLocked = topbarLocked || busy;
  const enabled = canNavigate || (key => key === 'input' || key === 'criteria' && state.criteria.length > 0
    || key === 'running' && state.review && state.review.status !== 'completed' || key === 'results' && state.review?.status === 'completed');
  return <div className={`rv-workspace rv-workspace--${workspaceStep || state.step}`}>
    <a href="#review-content" className="rv-skip">작업 영역으로 이동</a>
    <SiteHeader current="/review/new" locked={navigationLocked} lockReason="처리 중에는 화면을 이동할 수 없습니다." />
    {navigationLocked && <span id="mt-lock-reason" className="sr-only">처리 중에는 화면을 이동할 수 없습니다.</span>}
    <WorkspaceProjectBar name={state.projectName || ''} step={state.step === 'criteria' ? 'input' : state.step} steps={steps}
      projectEditor={projectEditor} projectTools={projectTools} locked={navigationLocked}
      version={versionLabel || (state.snapshot ? `기준 v${state.snapshot.version}` : '작업 중')} canNavigate={enabled} navigate={navigate}>
      {projectActions}
    </WorkspaceProjectBar>
    <main id="review-content" className={`rv-main ${mainClassName}`.trim()} tabIndex={-1} aria-busy={busy || undefined}>{children}</main>
  </div>;
}
