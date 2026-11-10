import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { useOptionalAuth } from '../auth/AuthProvider.jsx';
import { Brand } from '../components/Brand.jsx';
import { Icon } from '../components/Icon.jsx';
import { ErrorMessage } from '../components/ErrorMessage.jsx';
import { labels } from '../pages/review/reviewConstants.js';
import { useWorkspaceReset } from '../workspace/WorkspaceSession.jsx';
import '../styles/review-v2.css';
import '../styles/review-interactions.css';

const tools = [['/review/new', '포스터 검수'], ['/video', '동영상 검수'], ['/review/tasks', '내 작업']];
const posterSteps = { ...labels, report: '보고서' };

// 포스터·영상·내 작업 화면이 같은 상단 바와 이동·로그아웃 동작을 공유한다.
export function WorkspaceTopbar({ current, locked = false, lockReason, children }) {
  const auth = useOptionalAuth();
  const user = auth?.user;
  const navigate = useNavigate();
  const resetWorkspace = useWorkspaceReset();
  const [logoutPending, setLogoutPending] = useState(false);
  const [logoutError, setLogoutError] = useState('');
  const lock = { disabled: locked, title: locked ? lockReason : undefined, 'aria-describedby': locked && lockReason ? 'mt-lock-reason' : undefined };
  const displayName = user?.display_name || user?.username || '검수 담당자';

  // 서버 세션이 종료된 경우에만 로그인 화면으로 이동한다.
  async function handleLogout() {
    if (locked || logoutPending) return;
    setLogoutPending(true);
    setLogoutError('');
    try {
      await auth.logout();
      navigate('/login', { replace: true });
    } catch (error) {
      setLogoutError(error.message || '로그아웃하지 못했습니다. 다시 시도해 주세요.');
    } finally {
      setLogoutPending(false);
    }
  }

  return <>
    <header className="rv-topbar"><div className="rv-topbar-brand"><Brand onClick={() => { resetWorkspace(); navigate('/'); }} {...lock} /><span className="rv-edition">REVIEW STUDIO</span></div>
      <div className="rv-topbar-actions">
        {auth && <nav className="rv-tool-nav" aria-label="검수 도구">{tools.map(([to, label]) => locked ? <span key={to}
          className="rv-tool-link" role="link" aria-disabled="true" tabIndex={-1}
          aria-current={to === current ? 'page' : undefined} title={lockReason}>{label}</span> : <Link key={to} className="rv-tool-link" to={to}
          aria-current={to === current ? 'page' : undefined} aria-disabled={locked || undefined} tabIndex={locked ? -1 : undefined}
          title={lock.title} aria-describedby={lock['aria-describedby']} onClick={event => {
            if (locked) { event.preventDefault(); return; }
            // 새 탭으로 여는 동작은 현재 탭의 작업을 초기화하지 않는다.
            if (!event.defaultPrevented && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) resetWorkspace();
          }}>{label}</Link>)}</nav>}
        {children}
        {user && <><span className="rv-topbar-divider" /><span className="rv-user" title={displayName}>{displayName}</span>
          <button className="rv-icon-button" onClick={handleLogout} disabled={locked || logoutPending} title={locked ? lockReason : undefined} aria-label={logoutPending ? '로그아웃 중…' : '로그아웃'}><Icon name="logout" /></button></>}
      </div>
    </header>
    {logoutError && <div className="rv-topbar-error"><ErrorMessage message={logoutError} /></div>}
  </>;
}

export function WorkspaceFooter() {
  return <footer className="rv-footer"><span><i />Creative Checker</span><p>자동 판정과 담당자의 최종 승인은 구분됩니다.</p><span>기준 · 이미지 · 근거</span></footer>;
}

export function WorkspaceProjectBar({ name, version, step, steps, canNavigate, navigate, locked, projectEditor, children }) {
  const go = useNavigate();
  const resetWorkspace = useWorkspaceReset();
  const currentIndex = Object.keys(steps).indexOf(step);
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
    </div>
    <div className="rv-project-actions">
      <nav className="rv-step-nav" aria-label="검수 단계">{Object.entries(steps).map(([key, label], index) => <button type="button" key={key}
        disabled={locked || !canNavigate(key)} className={`${key === step ? 'is-active' : ''} ${index < currentIndex ? 'is-done' : ''}`}
        onClick={() => navigate(key)} aria-current={key === step ? 'step' : undefined}>
        <span>{index < currentIndex ? <Icon name="check" /> : String(index + 1).padStart(2, '0')}</span>{label}</button>)}</nav>
      {children}
    </div>
  </div>;
}

export default function ReviewLayout({ state, busy, health, healthBusy, checkHealth, navigate, children, previewMode = false,
  canNavigate, versionLabel, showConnection = true, mainClassName = '', workspaceStep, topbarLocked = false, projectEditor, projectActions }) {
  const navigationLocked = topbarLocked || busy;
  const connection = health?.status === 'offline' ? 'offline' : health?.ready ? 'online' : 'pending';
  const enabled = canNavigate || (key => key === 'input' || key === 'criteria' && state.criteria.length > 0
    || key === 'running' && state.review && state.review.status !== 'completed' || key === 'results' && state.review?.status === 'completed');
  return <div className={`rv-workspace rv-workspace--${workspaceStep || state.step}`}>
    <a href="#review-content" className="rv-skip">작업 영역으로 이동</a>
    <WorkspaceTopbar current="/review/new" locked={navigationLocked} lockReason="처리 중에는 화면을 이동할 수 없습니다.">{showConnection && <button className={`rv-connection ${connection}`} onClick={checkHealth} disabled={navigationLocked || previewMode || healthBusy} title="검수 구성 다시 확인"><i />{previewMode ? '화면 미리보기' : healthBusy || !health ? '구성 확인 중' : health.status === 'offline' ? '서버 연결 필요' : health.ready ? '검수 구성 준비됨' : '검수 구성 확인 필요'}</button>}</WorkspaceTopbar>
    {navigationLocked && <span id="mt-lock-reason" className="sr-only">처리 중에는 화면을 이동할 수 없습니다.</span>}
    <WorkspaceProjectBar name={state.projectName || ''} step={state.step} steps={posterSteps} projectEditor={projectEditor} locked={navigationLocked}
      version={versionLabel || (state.snapshot ? `기준 v${state.snapshot.version}` : '작업 중')} canNavigate={enabled} navigate={navigate}>
      {projectActions}
    </WorkspaceProjectBar>
    <main id="review-content" className={`rv-main ${mainClassName}`.trim()} tabIndex={-1} aria-busy={busy || undefined}>{children}</main>
    <WorkspaceFooter />
  </div>;
}
