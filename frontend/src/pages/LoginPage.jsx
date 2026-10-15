import React, { useEffect, useRef, useState } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router';
import { Brand } from '../components/Brand.jsx';
import { Icon } from '../components/Icon.jsx';
import { ErrorMessage } from '../components/ErrorMessage.jsx';
import { useAuth } from '../auth/AuthProvider.jsx';
import { authErrorMessage, loginDestination } from '../auth/helpers.js';
import '../styles/auth.css';

// 로그인 입력과 세션 확인 결과를 표시하고 성공한 사용자를 검수 화면으로 연결한다.
export function LoginPage() {
  const { status, error: sessionError, login, refreshSession } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const submittingRef = useRef(false);
  const mounted = useRef(false);
  const usernameInput = useRef(null);
  const passwordInput = useRef(null);
  const destination = loginDestination(location.state?.from);
  const unavailable = status === 'unavailable';
  const checking = status === 'checking';

  // 페이지 진입을 알리고 이탈 후 늦게 완료된 요청이 화면을 이동시키지 않도록 한다.
  useEffect(() => {
    mounted.current = true;
    document.getElementById('login-title')?.focus({ preventScroll: true });
    return () => { mounted.current = false; };
  }, []);

  // 공란과 연속 제출을 막고 서버가 세션을 확인한 뒤에만 페이지를 이동한다.
  async function handleLogin(event) {
    event.preventDefault();
    if (submittingRef.current || checking || unavailable) return;
    if (!username.trim()) {
      setError('아이디를 입력해 주세요.');
      usernameInput.current?.focus();
      return;
    }
    if (!password) {
      setError('비밀번호를 입력해 주세요.');
      passwordInput.current?.focus();
      return;
    }
    submittingRef.current = true;
    setSubmitting(true);
    setError('');
    try {
      const user = await login({ username: username.trim(), password });
      if (!mounted.current) return;
      setPassword('');
      if (user) navigate(destination, { replace: true });
    } catch (failure) {
      if (!mounted.current) return;
      setError(authErrorMessage(failure));
      setPassword('');
      passwordInput.current?.focus();
    } finally {
      submittingRef.current = false;
      if (mounted.current) setSubmitting(false);
    }
  }

  // 연결을 다시 확인할 때 이전 입력 오류 안내를 정리한다.
  function retrySession() {
    setError('');
    refreshSession();
  }

  if (status === 'authenticated') return <Navigate to={destination} replace />;

  return <div className="auth-page">
    <header className="auth-header"><Brand onClick={() => navigate('/')} /><Link className="auth-home-link" to="/">서비스 소개로 돌아가기<Icon name="arrow" /></Link></header>
    <main className="auth-main">
      <section className="auth-intro" aria-label="로그인 안내">
        <span className="pill">기준부터 결과까지, 크리에이티브체커</span>
        <h1>검수의 시작을,<br /><em>한곳에서.</em></h1>
        <p>로그인하고 이미지와 요구사항을 준비하세요.<br />확정한 기준으로 검수하고 근거를 확인할 수 있어요.</p>
        <ol className="auth-steps" aria-label="이용 순서">
          <li className="auth-step-current" aria-current="step"><span>01</span><div><strong>로그인</strong><p>내 계정으로 검수를 시작해요.</p></div><Icon name="lock" /></li>
          <li><span>02</span><div><strong>자료 입력 · 기준 확정</strong><p>확인할 이미지와 조건을 준비해요.</p></div></li>
          <li><span>03</span><div><strong>검수 실행 · 결과 확인</strong><p>항목별 결과와 이미지 근거를 살펴봐요.</p></div></li>
        </ol>
      </section>
      <section className="auth-card" aria-labelledby="login-title">
        <div className="auth-card-heading"><span className="auth-symbol"><Icon name="lock" /></span><p className="eyebrow">STEP 01 · 로그인</p><h2 id="login-title" tabIndex="-1">다시 만나 반가워요.</h2><p>아이디와 비밀번호로 로그인해 주세요.</p></div>
        <form className="auth-form" onSubmit={handleLogin} noValidate aria-busy={submitting || checking}>
          <div className="auth-field"><label htmlFor="login-username">아이디</label><input id="login-username" name="username" ref={usernameInput} value={username} onChange={event => { setUsername(event.target.value); setError(''); }} placeholder="아이디를 입력해 주세요" autoComplete="username" autoCapitalize="none" spellCheck={false} required readOnly={submitting} /></div>
          <div className="auth-field"><label htmlFor="login-password">비밀번호</label><div className="auth-password"><input id="login-password" name="password" ref={passwordInput} type={showPassword ? 'text' : 'password'} value={password} onChange={event => { setPassword(event.target.value); setError(''); }} placeholder="비밀번호를 입력해 주세요" autoComplete="current-password" required readOnly={submitting} /><button className="auth-password-toggle" type="button" aria-label={showPassword ? '비밀번호 숨기기' : '비밀번호 표시'} aria-pressed={showPassword} onClick={() => setShowPassword(value => !value)}>{showPassword ? '숨기기' : '표시'}</button></div></div>
          <ErrorMessage message={error} />
          {checking && <p className="auth-notice" role="status"><span className="auth-spinner" aria-hidden="true" />로그인 상태를 확인하고 있어요.</p>}
          {unavailable && <div className="auth-unavailable"><ErrorMessage message={authErrorMessage(sessionError, 'session')} /><button className="text-btn" type="button" onClick={retrySession}>로그인 상태 다시 확인하기</button></div>}
          <button className="btn primary auth-submit" type="submit" disabled={submitting || checking || unavailable}>{submitting ? '로그인 중…' : '로그인'}{!submitting && <Icon name="arrow" />}</button>
          <p className="auth-help"><Icon name="info" />계정이 없다면 프로젝트 담당자에게 문의해 주세요.</p>
        </form>
      </section>
    </main>
    <footer className="auth-footer">OCR 근거 기반 검수 · 최종 승인은 담당자가 결정합니다.</footer>
  </div>;
}

