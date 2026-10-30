import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Link, Navigate, Outlet, useLocation, useNavigate } from 'react-router';
import { api, ApiError, post, setCsrfToken } from './api';
import { useAction } from './useApi';
import type { Session, User } from './types';
import { ErrorNotice, Field, Loading, submit } from './ui';

interface Auth { user: User | null; loading: boolean; error: unknown; expired: boolean; refresh: () => void; login: (email: string, password: string) => Promise<void>; logout: () => Promise<void> }
const AuthContext = createContext<Auth | null>(null);
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [expired, setExpired] = useState(false);
  const refresh = useCallback(() => {
    setLoading(true); setError(null);
    api<Session>('/auth/me').then(session => { setUser(session.user); setCsrfToken(session.csrf_token); })
      .catch(failure => { setUser(null); setCsrfToken(''); if (!(failure instanceof ApiError && failure.status === 401)) setError(failure); })
      .finally(() => setLoading(false));
  }, []);
  useEffect(refresh, [refresh]);
  useEffect(() => { const expire = () => { setExpired(Boolean(user)); setUser(null); setCsrfToken(''); }; window.addEventListener('cc:session-expired', expire); return () => window.removeEventListener('cc:session-expired', expire); }, [user]);
  async function login(email: string, password: string) {
    const session = await post<Session>('/auth/login', { email, password });
    setCsrfToken(session.csrf_token); setUser(session.user); setError(null); setExpired(false);
  }
  async function logout() { await post('/auth/logout'); setUser(null); setCsrfToken(''); setExpired(false); }
  return <AuthContext.Provider value={{ user, loading, error, expired, refresh, login, logout }}>{children}</AuthContext.Provider>;
}
export function useAuth() { const context = useContext(AuthContext); if (!context) throw new Error('인증 정보가 없습니다.'); return context; }
export function RequireAuth() {
  const auth = useAuth(); const location = useLocation();
  if (auth.loading) return <main className="session-screen"><Loading label="로그인 세션을 확인하고 있습니다." /></main>;
  if (auth.error) return <main className="session-screen"><h1>서비스에 연결할 수 없습니다</h1><ErrorNotice error={auth.error} retry={auth.refresh} /></main>;
  if (!auth.user) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <Outlet />;
}
export function LoginPage() {
  const auth = useAuth(); const navigate = useNavigate(); const location = useLocation();
  const action = useAction(); const [email, setEmail] = useState(''); const [password, setPassword] = useState('');
  const from = (location.state as { from?: string } | null)?.from;
  const destination = from?.startsWith('/') && !from.startsWith('//') && !from.startsWith('/login') ? from : '/projects';
  if (auth.loading) return <main className="session-screen"><Loading label="로그인 세션을 확인하고 있습니다." /></main>;
  if (auth.user) return <Navigate to={destination} replace />;
  return <main className="login-page"><div className="login-brand"><Brand /></div><div className="login-form-wrap">
    <h1>로그인</h1><p className="muted">발급받은 계정으로 프로젝트와 검수 업무에 접속하세요.</p>
    {auth.expired && <p className="notice caution">로그인 세션이 만료되었습니다. 다시 로그인하면 이전 작업 화면으로 돌아갑니다.</p>}
    <ErrorNotice error={auth.error} retry={auth.refresh} />
    <form className="form-stack" onSubmit={submit(() => action.run(async () => { await auth.login(email.trim(), password); setPassword(''); navigate(destination, { replace: true }); }))}>
      <Field label="이메일"><input type="email" autoComplete="username" required value={email} onChange={event => setEmail(event.target.value)} /></Field>
      <Field label="비밀번호"><input type="password" autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)} /></Field>
      <ErrorNotice error={action.error} /><button className="button primary" disabled={action.pending}>{action.pending ? '로그인 중…' : '로그인'}</button>
    </form><p className="login-help">계정 발급과 로그인 문의는 운영자에게 요청해 주세요.</p>
  </div></main>;
}
export function Brand() {
  return <span className="brand"><svg viewBox="0 0 28 28" width="28" height="28" aria-hidden="true"><path fill="currentColor" d="M3 3c3-3 8 2 11 7 3-5 8-10 11-7s-2 8-7 11c5 3 10 8 7 11s-8-2-11-7c-3 5-8 10-11 7s2-8 7-11C5 11 0 6 3 3Z" /><circle cx="14" cy="14" r="3" fill="#f2f2f1" /></svg>Creative Checker</span>;
}
export function AppFrame() {
  const auth = useAuth(); const action = useAction();
  return <div className="app-frame"><a className="skip-link" href="#main">본문으로 이동</a><header className="app-header">
    <Link to="/projects" className="brand-link" aria-label="크리에이티브체커 프로젝트 목록"><Brand /></Link>
    <nav aria-label="계정 메뉴"><Link to="/projects">프로젝트</Link><Link to="/account">{auth.user?.name || auth.user?.email}</Link>
      {auth.user?.is_admin && <Link to="/admin/users">계정 발급</Link>}<button className="header-button" disabled={action.pending} onClick={() => void action.run(auth.logout)}>로그아웃</button></nav>
  </header>{auth.user?.must_change_password && <p className="notice caution account-reminder">비밀번호를 변경해 주세요. <Link to="/account">비밀번호 변경</Link></p>}
    <ErrorNotice error={action.error} /><Outlet />
  </div>;
}
