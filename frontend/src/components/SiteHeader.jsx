import React, { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router';
import { useOptionalAuth } from '../auth/AuthProvider.jsx';
import { useWorkspaceReset } from '../workspace/WorkspaceSession.jsx';
import { useHelpChatAnchor } from '../help-chat/HelpChatProvider.jsx';
import { mediaChoicePath, mediaChoiceState } from '../workspace/mediaChoice.js';
import { Brand } from './Brand.jsx';
import { ErrorMessage } from './ErrorMessage.jsx';
import { Icon } from './Icon.jsx';
import '../styles/site-header.css';

const tools = [['/review/new', '포스터 검수'], ['/video', '동영상 검수'], ['/review/tasks', '내 작업']];
const guides = [['lp-features', '서비스 소개'], ['lp-demo', '검수 예시']];
const plainClick = event => !event.defaultPrevented && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;

// 소개·로그인·검수·내 작업·안내 화면이 같은 상단 바를 쓴다.
// 로그인 전에는 서비스 안내 메뉴를, 로그인 뒤에는 검수 도구와 계정을 같은 자리에 보여 준다.
export function SiteHeader({ current, locked = false, lockReason, onHome, onStart, loginRef, afterLogout, onLogoutPending }) {
  const auth = useOptionalAuth();
  const user = auth?.status === 'authenticated' ? auth.user : null;
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const resetWorkspace = useWorkspaceReset();
  const helpChatAnchor = useHelpChatAnchor();
  const [logoutPending, setLogoutPending] = useState(false);
  const [logoutError, setLogoutError] = useState('');
  const lock = { disabled: locked, title: locked ? lockReason : undefined, 'aria-describedby': locked && lockReason ? 'mt-lock-reason' : undefined };
  const displayName = user?.display_name || user?.username || '검수 담당자';
  const onLogin = pathname === '/login';
  // 작업 화면(current 지정)은 세션 확인 전에도 검수 도구를 유지하고, 본문의 실행 버튼과 겹치는 시작 버튼을 두지 않는다.
  const workspace = current !== undefined;
  const showTools = user || workspace && auth;

  function pending(value) {
    setLogoutPending(value);
    onLogoutPending?.(value);
  }

  // 서버 세션이 종료된 경우에만 로그인 화면으로 이동한다.
  async function handleLogout() {
    if (locked || logoutPending) return;
    pending(true);
    setLogoutError('');
    try {
      await auth.logout();
      if (afterLogout) afterLogout();
      else navigate('/login', { replace: true });
    } catch (error) {
      setLogoutError(error.message || '로그아웃하지 못했습니다. 다시 시도해 주세요.');
    } finally {
      pending(false);
    }
  }

  function home() {
    if (onHome) { onHome(); return; }
    resetWorkspace();
    navigate('/');
  }
  function start() {
    if (onStart) onStart();
    else if (auth?.status === 'anonymous') navigate('/login');
    // 포스터인지 동영상인지 정하지 않고 시작하므로 자료 종류부터 고른다.
    else navigate(mediaChoicePath, { state: mediaChoiceState });
  }

  return <>
    <header className="cc-header"><div className="cc-header-inner">
      <div className="cc-header-brand">
        <Brand onClick={home} {...lock} />
        {user && helpChatAnchor && <div className="cc-header-help" ref={helpChatAnchor} />}
      </div>
      <div className="cc-header-actions">
        {showTools ? <nav className="cc-header-nav" aria-label="검수 도구">{tools.map(([to, label]) => locked ? <span key={to}
          className="cc-header-link" role="link" aria-disabled="true" tabIndex={-1}
          aria-current={to === current ? 'page' : undefined} title={lockReason}>{label}</span> : <Link key={to} className="cc-header-link" to={to}
          aria-current={to === current ? 'page' : undefined} onClick={event => {
            // 새 탭으로 여는 동작은 현재 탭의 작업을 초기화하지 않는다.
            if (plainClick(event)) resetWorkspace();
          }}>{label}</Link>)}</nav>
          : !workspace && <nav className="cc-header-nav" aria-label="메인 메뉴">{guides.map(([hash, label]) => <Link key={hash}
            className="cc-header-link" to={{ pathname: '/', hash }}>{label}</Link>)}</nav>}
        {user ? <div className="cc-header-account"><span className="cc-header-user" title={displayName}>{displayName}</span>
          <button type="button" className="cc-header-icon" onClick={handleLogout} disabled={locked || logoutPending}
            title={locked ? lockReason : '로그아웃'} aria-label={logoutPending ? '로그아웃 중…' : '로그아웃'}><Icon name="logout" /></button></div>
          : !workspace && !onLogin && <div className="cc-header-account">
            <Link ref={loginRef} className="cc-header-link" to="/login">로그인</Link>
            <button type="button" className="cc-header-start" onClick={start}>검수 시작하기<Icon name="arrow" /></button></div>}
      </div>
    </div></header>
    {logoutError && <div className="cc-header-error"><ErrorMessage message={logoutError} /></div>}
  </>;
}
