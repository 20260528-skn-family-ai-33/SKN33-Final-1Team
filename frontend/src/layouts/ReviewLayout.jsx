import React from 'react';
import { Link } from 'react-router';
import { Brand } from '../components/Brand.jsx';
import { Icon } from '../components/Icon.jsx';
import { ErrorMessage } from '../components/ErrorMessage.jsx';
import { labels } from '../reviewConstants.js';

// 검수 단계 탐색, 서버 상태, 로그인한 사용자 정보를 공통 화면 틀에 표시한다.
export default function ReviewLayout({ state, busy, health, healthBusy, checkHealth, navigate,
  user, onLogout, logoutPending, logoutError, onHome, children }) {
  const displayName = user?.display_name || user?.username || '검수 담당자';
  const connection = health?.status === 'offline' ? 'offline' : health?.ready ? 'online' : 'pending';

  return <div className="app-shell">
    <aside className="sidebar">
      <Brand onClick={onHome} />
      <div className="workspace-name">WORKSPACE</div>
      <div className="workspace-label"><Icon name="grid" />내 검수 프로젝트</div>
      <div className="side-divider" />
      <p className="side-title">새로운 검수</p>
      <nav className="step-nav" aria-label="검수 단계">
        {Object.entries(labels).map(([key, label], index) => {
          const enabled = key === 'input'
            || key === 'criteria' && state.criteria.length > 0
            || key === 'running' && state.review && state.review.status !== 'completed'
            || key === 'results' && state.review?.status === 'completed';
          const done = index < Object.keys(labels).indexOf(state.step);
          return <button key={key}
            className={`step-button ${key === state.step ? 'current' : done ? 'done' : ''}`}
            disabled={!enabled || busy && key !== state.step}
            onClick={() => navigate(key)} aria-current={key === state.step ? 'step' : undefined}>
            <span className="step-number">{done ? <Icon name="check" /> : index + 1}</span>{label}
          </button>;
        })}
      </nav>
      <div className="sidebar-bottom">
        <Link className="back-home" to="/video"><Icon name="image" />동영상 검수 (시연)</Link>
        <button className="back-home" onClick={onHome}><Icon name="home" />서비스 메인으로</button>
        <p>확정한 기준으로 검수합니다.<br />자료는 연결된 검수 서버로 전송되어 처리됩니다.</p>
      </div>
    </aside>
    <div className="app-area">
      <header className="app-topbar">
        <div className="breadcrumb"><span>내 검수 프로젝트</span><span aria-hidden="true">/</span><span>{labels[state.step]}</span></div>
        <div className="topbar-right">
          <button className={`connection ${connection}`} onClick={checkHealth} disabled={healthBusy} title="연결 상태 새로 확인">
            <span className="connection-dot" />
            {healthBusy || !health ? '연결 확인 중' : health.status === 'offline' ? '서버 연결 필요' : health.ready ? '검수 서버 연결됨' : '서버 연결 · 준비 필요'}
          </button>
          <span className="user-label">{displayName}</span>
          <span className="avatar" aria-label={displayName}>{Array.from(displayName)[0]}</span>
          <button className="text-btn logout-button" onClick={onLogout} disabled={logoutPending}>
            <Icon name="logout" />{logoutPending ? '로그아웃 중…' : '로그아웃'}
          </button>
        </div>
      </header>
      <main className="app-main"><ErrorMessage message={logoutError} />{children}</main>
    </div>
  </div>;
}
