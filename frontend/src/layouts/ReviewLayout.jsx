import React from 'react';
import { Brand } from '../components/Brand.jsx';
import { Icon } from '../components/Icon.jsx';
import { ErrorMessage } from '../components/ErrorMessage.jsx';
import { labels } from '../reviewConstants.js';
import '../styles/review-v2.css';

export default function ReviewLayout({ state, busy, health, healthBusy, checkHealth, navigate,
  user, onLogout, logoutPending, logoutError, onHome, children }) {
  const displayName = user?.display_name || user?.username || '검수 담당자';
  const connection = health?.status === 'offline' ? 'offline' : health?.ready ? 'online' : 'pending';
  const currentIndex = Object.keys(labels).indexOf(state.step);
  return <div className={`rv-workspace rv-workspace--${state.step}`}>
    <a href="#review-content" className="rv-skip">작업 영역으로 이동</a>
    <header className="rv-topbar"><div className="rv-topbar-brand"><Brand onClick={onHome} /><span className="rv-edition">REVIEW STUDIO</span></div>
      <div className="rv-topbar-actions"><button className={`rv-connection ${connection}`} onClick={checkHealth} disabled={healthBusy} title="연결 상태 새로 확인"><i />{healthBusy || !health ? '연결 확인 중' : health.status === 'offline' ? '서버 연결 필요' : health.ready ? '검수 서버 연결됨' : '서버 연결 · 준비 필요'}</button><span className="rv-topbar-divider" /><span className="rv-user" title={displayName}>{displayName}</span><button className="rv-icon-button" onClick={onLogout} disabled={logoutPending} aria-label={logoutPending ? '로그아웃 중…' : '로그아웃'}><Icon name="logout" /></button></div>
    </header>
    <div className="rv-projectbar"><div className="rv-project-identity"><button className="rv-icon-button" aria-label="서비스 메인으로" onClick={onHome}><Icon name="grid" /></button><div><span className="rv-eyebrow">내 검수 프로젝트</span><strong>{state.projectName || '새로운 검수'}</strong></div><span className="rv-version">{state.snapshot ? `기준 v${state.snapshot.version}` : '작업 중'}</span></div>
      <nav className="rv-step-nav" aria-label="검수 단계">{Object.entries(labels).map(([key,label],index) => {
        const enabled = key === 'input' || key === 'criteria' && state.criteria.length > 0 || key === 'running' && state.review && state.review.status !== 'completed' || key === 'results' && state.review?.status === 'completed';
        return <button key={key} disabled={!enabled || busy && key !== state.step} className={`${key === state.step ? 'is-active' : ''} ${index < currentIndex ? 'is-done' : ''}`} onClick={() => navigate(key)} aria-current={key === state.step ? 'step' : undefined}><span>{index < currentIndex ? <Icon name="check" /> : String(index + 1).padStart(2,'0')}</span>{label}</button>;
      })}</nav>
    </div>
    <main id="review-content" className="rv-main" tabIndex={-1}><ErrorMessage message={logoutError} />{children}</main>
    <footer className="rv-footer"><span><i />Creative Checker</span><p>자동 판정과 담당자의 최종 승인은 구분됩니다.</p><span>기준 · 이미지 · 근거</span></footer>
  </div>;
}
