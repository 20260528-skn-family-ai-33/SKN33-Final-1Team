import React from 'react';
import { Link, NavLink, Outlet, useMatch, useNavigate } from 'react-router';
import { useAuth } from './auth.jsx';
import { useAction, useResource } from './hooks.jsx';
import * as api from './api.js';
import { Brand } from '../components/Brand.jsx';
import { ErrorMessage } from '../components/ErrorMessage.jsx';
export function Title({ title, children }) { return <div className="page-heading"><p className="eyebrow">CREATIVE CHECKER</p><h1 id="page-title" tabIndex={-1}>{title}</h1><p>{children}</p></div>; }
export function Resource({ state, children }) {
  if (state.loading) return <p role="status" className="form-card">서버에 저장된 자료를 불러오고 있어요.</p>;
  if (state.error) return <section className="form-card"><h2>{state.error.status === 404 ? '자료를 찾을 수 없습니다.' : '자료를 불러오지 못했습니다.'}</h2><ErrorMessage message={state.error.message} /><button className="btn secondary" onClick={state.reload}>다시 조회</button><Link className="btn secondary" to="/projects">프로젝트 목록</Link></section>;
  return children(state.data);
}
export function Pager({ page, path, filters = {} }) {
  if (!page.has_more) return <p className="help">마지막 페이지입니다.</p>;
  const search = new URLSearchParams({ ...filters, cursor: page.next_cursor });
  return <Link className="btn secondary" to={`${path}?${search}`}>다음 페이지</Link>;
}
export function DirtyDialog({ blocker }) {
  return blocker.state === 'blocked' && <div className="v1-modal"><section className="form-card" role="alertdialog" aria-modal="true" aria-labelledby="unsaved-title"><h2 id="unsaved-title">저장하지 않은 편집이 있습니다.</h2><p>이동하면 현재 화면의 편집 내용은 사라집니다.</p><div className="v1-actions"><button className="btn primary" onClick={() => blocker.reset()}>계속 편집</button><button className="btn secondary" onClick={() => blocker.proceed()}>저장하지 않고 이동</button></div></section></div>;
}
export function PendingPanel({ mutation }) {
  return <>{!mutation.persistent && <p className="v1-notice" role="status">요청 기록을 브라우저에 보관할 수 없습니다. 새로고침 전 이력에서 저장 여부를 확인해 주세요.</p>}
    <ErrorMessage message={mutation.error} />{mutation.entries.length > 0 && <section className="v1-notice" aria-label="결과 미확인 요청"><h3>보낸 요청의 결과를 확인해 주세요.</h3><p>요청을 다시 확인해도 같은 키와 본문을 보냅니다. 새 실행을 자동 생성하지 않습니다.</p>
      {mutation.entries.map(entry => <div className="v1-pending" key={`${entry.operation}:${entry.key}`}><span>{({ confirm_criteria: '기준 확정', create_review: '검수 접수', retry_review: '재검수 접수' })[entry.operation]}{entry.body.expected_revision ? ` · 초안 revision ${entry.body.expected_revision}` : ''}</span><button className="btn secondary small" disabled={mutation.busy} onClick={() => mutation.resume(entry)}>같은 요청으로 결과 확인</button><button className="text-btn" disabled={mutation.busy} onClick={() => { if (window.confirm('서버 이력에서 저장 여부를 확인했나요? 이 기록을 지워도 서버 작업은 취소되지 않습니다.')) mutation.discard(entry); }}>확인한 요청 기록 지우기</button></div>)}
    </section>}</>;
}
export function ServerState() {
  const state = useResource('health', signal => api.health(signal));
  return <div className="v1-health"><span>{state.loading ? '서버 확인 중' : state.error ? '서버 상태 확인 필요' : state.data.checks.db?.state === 'ready' ? '서버 연결됨' : 'DB 준비 확인 필요'}</span><button className="text-btn" onClick={state.reload} disabled={state.loading}>다시 확인</button></div>;
}
export function Workspace() {
  const auth = useAuth(); const navigate = useNavigate(); const action = useAction(`logout:${auth.user.id}`); const match = useMatch('/projects/:projectId/*'); const pid = match?.params.projectId;
  return <div className="app-shell v1-shell"><aside className="sidebar"><Brand onClick={() => navigate('/projects')} /><p className="workspace-name">WORKSPACE</p><nav className="v1-nav" aria-label="업무 메뉴"><NavLink to="/projects" end>내 프로젝트</NavLink>{pid && <><NavLink to={`/projects/${pid}/review/new`}>이미지·요구사항 입력</NavLink><NavLink to={`/projects/${pid}/drafts`}>저장된 초안·기준</NavLink><NavLink to={`/projects/${pid}/reviews`}>검수 이력</NavLink></>}</nav><div className="sidebar-bottom"><p>확정한 기준과 검수 이력을 보존합니다.<br />자동 판정은 담당자의 최종 승인이 아닙니다.</p></div></aside>
    <div className="app-area"><header className="app-topbar"><Link to="/projects">내 검수 프로젝트</Link><div className="topbar-right"><ServerState /><span className="user-label">{auth.user.display_name}</span><button className="text-btn" disabled={action.busy} onClick={async () => { if (!window.dispatchEvent(new Event('cc:v1:before-logout', { cancelable: true }))) return; const done = await action.run(async signal => { await auth.logout(signal); return true; }); if (done) navigate('/login', { replace: true }); }}>{action.busy ? '로그아웃 중…' : '로그아웃'}</button></div></header><main className="app-main"><ErrorMessage message={action.error} /><Outlet key={auth.user.id} /></main></div></div>;
}
