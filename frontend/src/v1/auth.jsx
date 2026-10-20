import React, { createContext, useContext, useEffect, useRef, useState } from 'react';
import { Link, Navigate, Outlet, useLocation, useNavigate } from 'react-router';
import * as api from './api.js';
import { browserPending } from './pending.js';
import { destination } from './contracts.js';
import { Brand } from '../components/Brand.jsx';
import { ErrorMessage } from '../components/ErrorMessage.jsx';
import '../styles/auth.css';
const Context = createContext(null);
export const useAuth = () => useContext(Context);
export function AuthProvider({ children }) {
  const [session, setSession] = useState({ status: 'checking', user: null, error: null });
  const [pending] = useState(browserPending); const generation = useRef(0); const controller = useRef(null);
  async function refresh() {
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort; const epoch = ++generation.current;
    setSession(current => ({ ...current, status: 'checking', error: null }));
    try { const user = await api.me(abort.signal); if (generation.current !== epoch) return; if (user) pending.actor(user.id); setSession({ status: user ? 'authenticated' : 'anonymous', user, error: null }); }
    catch (error) { if (!abort.signal.aborted && generation.current === epoch) setSession({ status: 'unavailable', user: null, error }); }
  }
  useEffect(() => {
    const expired = () => { generation.current += 1; controller.current?.abort(); setSession({ status: 'anonymous', user: null, error: null }); };
    window.addEventListener('cc:v1:expired', expired); refresh();
    return () => { generation.current += 1; controller.current?.abort(); window.removeEventListener('cc:v1:expired', expired); };
  }, []);
  async function login(credentials, signal) {
    const epoch = ++generation.current; controller.current?.abort();
    const user = await api.login(credentials, signal);
    if (epoch !== generation.current || signal?.aborted) return null;
    pending.actor(user.id); setSession({ status: 'authenticated', user, error: null }); return user;
  }
  async function logout(signal) {
    const epoch = ++generation.current; await api.logout(signal);
    if (epoch !== generation.current || signal?.aborted) return;
    pending.clearAll(); setSession({ status: 'anonymous', user: null, error: null });
  }
  return <Context.Provider value={{ ...session, pending, refresh, login, logout }}>{children}</Context.Provider>;
}
export function RequireAuth() {
  const auth = useAuth(); const location = useLocation();
  if (auth.status === 'checking') return <main className="auth-state"><p role="status">로그인 상태를 확인하고 있어요.</p></main>;
  if (auth.status === 'unavailable') return <main className="auth-state"><section className="auth-state-card"><h1>로그인 상태를 확인할 수 없어요.</h1><ErrorMessage message={auth.error?.message} /><button className="btn primary" onClick={auth.refresh}>다시 확인하기</button></section></main>;
  if (auth.status !== 'authenticated') return <Navigate to="/login" replace state={{ from: location }} />;
  return <Outlet key={auth.user.id} />;
}
export function LoginPage() {
  const auth = useAuth(); const navigate = useNavigate(); const location = useLocation(); const [mode, setMode] = useState('email');
  const [available, setAvailable] = useState(false); const [identifier, setIdentifier] = useState(''); const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const guard = useRef(false); const abort = useRef(null); const mounted = useRef(false);
  useEffect(() => { mounted.current = true; const control = new AbortController(); api.csrf(control.signal).then(body => { if (!control.signal.aborted) setAvailable(body.developer_login_available); }).catch(error => { if (!control.signal.aborted) setError(error.message); }); return () => { mounted.current = false; control.abort(); abort.current?.abort(); }; }, []);
  const target = destination(location.state?.from);
  async function submit(event) {
    event.preventDefault(); if (guard.current) return;
    if (!identifier.trim() || !password) { setError('이메일과 비밀번호를 입력해 주세요.'); return; }
    guard.current = true; setBusy(true); setError(''); const control = new AbortController(); abort.current = control;
    try { const user = await auth.login({ [mode]: identifier.trim(), password }, control.signal); if (user && mounted.current) { setPassword(''); navigate(target, { replace: true }); } }
    catch (error) { if (mounted.current && !control.signal.aborted) { setPassword(''); setError(error.message); } }
    finally { guard.current = false; if (mounted.current) setBusy(false); }
  }
  if (auth.status === 'authenticated') return <Navigate to={target} replace />;
  if (auth.status === 'unavailable') return <main className="auth-state"><h1>로그인 상태를 확인할 수 없어요.</h1><button className="btn primary" onClick={auth.refresh}>다시 확인하기</button></main>;
  return <div className="auth-page"><header className="auth-header"><Brand onClick={() => navigate('/')} /><Link to="/">서비스 소개로</Link></header><main className="auth-main">
    <section className="auth-intro"><span className="pill">크리에이티브체커</span><h1>검수의 시작을,<br /><em>한곳에서.</em></h1><p>프로젝트와 기준, 검수 이력을 이어서 확인하세요.</p></section>
    <section className="auth-card"><div className="auth-card-heading"><p className="eyebrow">로그인</p><h2>{mode === 'email' ? '이메일로 로그인하세요.' : '개발 계정 로그인'}</h2><p>서버에 등록된 계정으로 접속해 주세요.</p></div>
      <form className="auth-form" onSubmit={submit} aria-busy={busy}>
        <div className="auth-field"><label htmlFor="v1-identifier">{mode === 'email' ? '이메일' : '개발 계정 아이디'}</label><input id="v1-identifier" type={mode === 'email' ? 'email' : 'text'} autoComplete="username" autoCapitalize="none" required value={identifier} onChange={event => setIdentifier(event.target.value)} disabled={busy} /></div>
        <div className="auth-field"><label htmlFor="v1-password">비밀번호</label><input id="v1-password" type="password" autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)} disabled={busy} /></div>
        <ErrorMessage message={error} /><button className="btn primary" disabled={busy || auth.status === 'checking'}>{busy ? '로그인 중…' : '로그인'}</button>
      </form>{available && <button className="text-btn v1-spaced" disabled={busy} onClick={() => { setMode(mode === 'email' ? 'username' : 'email'); setIdentifier(''); setPassword(''); setError(''); }}>{mode === 'email' ? '개발 계정으로 로그인' : '이메일 로그인으로 돌아가기'}</button>}
    </section></main></div>;
}
