import React, { useId, useLayoutEffect, useRef } from 'react';
import { Icon } from '../components/Icon.jsx';
import { SiteHeader } from '../components/SiteHeader.jsx';
import '../styles/review-v2.css';
import '../styles/review-interactions.css';

// 검수 유형마다 상단에 그 유형의 처리 단계만 보여 준다. 자막·둘 다는 문구 검수 단계, 로고는 로고 대조 단계.
export const posterSteps = { input: '자료 입력', running: '검수 진행', results: '결과 확인', report: '보고서' };
export const logoSteps = { input: '자료 입력', candidates: '후보 찾기', confirm: '위치 확정', compare: '정밀 대조' };

// 검수 이름 칸: 비어 있으면 흐린 안내 글자로 보이고(값처럼 보이지 않게), 연필을 누르면 바로 입력한다.
// 입력 중에는 글자 수를 보이고, Enter·Esc로 입력을 끝낸다. editor.onEnter가 있으면 Enter로 이 이름의 프로젝트를
// 만들고 선택한다(같은 이름이 있으면 그 프로젝트를 고른다). 한글 조합 중 Enter는 글자 확정이라 무시한다.
function ProjectNameField({ editor, value = '', locked }) {
  const input = useRef(null);
  const off = locked || editor.disabled;
  function keyDown(event) {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      editor.onEnter?.(event.currentTarget.value.trim());
      event.currentTarget.blur();
    } else if (event.key === 'Escape') event.currentTarget.blur();
  }
  return <div className="rv-project-name-field">
    <input ref={input} type="text" id={editor.id} aria-label="검수 이름" autoComplete="off" maxLength={editor.maxLength}
      value={value} placeholder="이름 없는 검수 (선택)" disabled={off} onChange={event => editor.onChange(event.target.value)}
      title={editor.onEnter ? 'Enter를 누르면 이 이름으로 프로젝트를 만들고 선택합니다.' : undefined} onKeyDown={keyDown} />
    {editor.maxLength && <span className="rv-project-name-count" aria-hidden="true">
      {value.length}/{editor.maxLength}{editor.onEnter && value.trim() ? ' · Enter로 프로젝트 만들기' : ''}</span>}
    <button type="button" aria-label="검수 이름 고치기" title="검수 이름 고치기" disabled={off}
      onClick={() => { input.current?.focus(); input.current?.select(); }}><Icon name="pencil" /></button>
  </div>;
}

// 서비스 메인 이동은 위쪽 사이트 머리글(로고)로 하므로 이 줄에는 홈 버튼을 두지 않는다.
// 상태(작업 중·기준 vN) 표시는 두지 않는다. 호출하는 화면이 넘기는 version은 쓰지 않는다.
export function WorkspaceProjectBar({ name, step, steps, canNavigate, navigate, locked, projectEditor, projectTools, children }) {
  return <div className="rv-projectbar">
    <div className="rv-project-identity">
      <div className="rv-project-field"><span className="rv-project-field-label">검수 이름</span>{projectEditor
        ? <ProjectNameField editor={projectEditor} value={name || ''} locked={locked} />
        : <strong title={name || undefined}>{name || '이름 없는 검수'}</strong>}</div>
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

function ReviewStartOverlay({ message, children }) {
  const dialog = useRef(null);
  const title = useId();
  useLayoutEffect(() => {
    const element = dialog.current;
    element.showModal();
    return () => element.close();
  }, []);

  return <dialog ref={dialog} className="rv-start-overlay" aria-labelledby={title}
    onCancel={event => event.preventDefault()}
    onKeyDown={event => event.stopPropagation()} onPaste={event => event.stopPropagation()}>
    <span className="rv-start-spinner" aria-hidden="true" />
    <h2 id={title}>검수 준비 중</h2>
    <p role="status">{message}</p>
    {children}
  </dialog>;
}

// steps를 null로 주면 단계 표시를 숨긴다(검수 유형을 고르기 전).
export default function ReviewLayout({ state, busy, navigate, children, steps = posterSteps,
  canNavigate, versionLabel, mainClassName = '', workspaceStep, topbarLocked = false, projectEditor, projectTools, projectActions,
  showWorkspaceBar = true, startMessage, startActions, headerCurrent = '/review/new' }) {
  const navigationLocked = topbarLocked || busy;
  const enabled = canNavigate || (key => key === 'input' || key === 'criteria' && state.criteria.length > 0
    || key === 'running' && state.review && state.review.status !== 'completed' || key === 'results' && state.review?.status === 'completed');
  return <div className={`rv-workspace rv-workspace--${workspaceStep || state.step}`}>
    <a href="#review-content" className="rv-skip">작업 영역으로 이동</a>
    <SiteHeader current={headerCurrent} locked={navigationLocked} lockReason="처리 중에는 화면을 이동할 수 없습니다." />
    {showWorkspaceBar && <>
      {navigationLocked && <span id="mt-lock-reason" className="sr-only">처리 중에는 화면을 이동할 수 없습니다.</span>}
      <WorkspaceProjectBar name={state.projectName || ''} step={state.step === 'criteria' ? 'input' : state.step} steps={steps}
        projectEditor={projectEditor} projectTools={projectTools} locked={navigationLocked}
        version={versionLabel || (state.snapshot ? `기준 v${state.snapshot.version}` : '작업 중')} canNavigate={enabled} navigate={navigate}>
        {projectActions}
      </WorkspaceProjectBar>
    </>}
    <main id="review-content" className={`rv-main ${mainClassName}`.trim()} tabIndex={-1} aria-busy={busy || undefined}>{children}</main>
    {startMessage && <ReviewStartOverlay message={startMessage}>{startActions}</ReviewStartOverlay>}
  </div>;
}
