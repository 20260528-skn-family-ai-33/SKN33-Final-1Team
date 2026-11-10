import React, { useEffect, useRef, useState } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router';
import { Brand } from '../components/Brand.jsx';
import { Icon } from '../components/Icon.jsx';
import { ErrorMessage } from '../components/ErrorMessage.jsx';
import { useAuth } from '../auth/AuthProvider.jsx';
import { authErrorMessage, loginDestination } from '../auth/helpers.js';
import { registerAccount } from '../api/auth.js';
import { registrationErrorMessage, registrationIssue } from '../auth/registration.js';
import posterReviewExample from '../assets/poster-review-example.png';
import '../styles/auth.css';

// 로그인 입력과 세션 확인 결과를 표시하고 성공한 사용자를 검수 화면으로 연결한다.
export function LoginPage() {
  const { status, error: sessionError, login, refreshSession } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [signup, setSignup] = useState(false);
  const [displayName, setDisplayName] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [success, setSuccess] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const submittingRef = useRef(false);
  const mounted = useRef(false);
  const formTitle = useRef(null);
  const usernameInput = useRef(null);
  const passwordInput = useRef(null);
  const nameInput = useRef(null);
  const confirmationInput = useRef(null);
  const from = location.state?.from;
  const destination = from?.pathname === '/review/tasks' ? '/review/tasks' : loginDestination(from);
  const unavailable = status === 'unavailable';
  const checking = status === 'checking';

  // 페이지 진입을 알리고 이탈 후 늦게 완료된 요청이 화면을 이동시키지 않도록 한다.
  useEffect(() => {
    mounted.current = true;
    window.scrollTo({ top: 0, behavior: 'instant' });
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    formTitle.current?.focus({ preventScroll: true });
  }, [signup]);

  // 공란과 연속 제출을 막고 서버가 세션을 확인한 뒤에만 페이지를 이동한다.
  async function handleLogin(event) {
    event.preventDefault();
    if (submittingRef.current || checking || unavailable) return;
    if (signup) {
      const issue = registrationIssue({ displayName, email: username, password, confirmation });
      if (issue) {
        setError(issue.message);
        ({ name: nameInput, email: usernameInput, password: passwordInput, confirmation: confirmationInput })[issue.field].current?.focus();
        return;
      }
    } else if (!username.trim()) {
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
    setSuccess('');
    try {
      if (signup) {
        await registerAccount({ email: username, displayName, password });
        if (!mounted.current) return;
        setSignup(false);
        setPassword('');
        setConfirmation('');
        setShowPassword(false);
        setSuccess('회원가입이 완료됐습니다. 이메일과 비밀번호로 로그인해 주세요.');
        return;
      }
      const user = await login({ username: username.trim(), password });
      if (!mounted.current) return;
      setPassword('');
      if (user) navigate(destination, { replace: true });
    } catch (failure) {
      if (!mounted.current) return;
      setError(signup ? registrationErrorMessage(failure) : authErrorMessage(failure));
      setPassword('');
      setConfirmation('');
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

  function switchMode() {
    if (submittingRef.current) return;
    setSignup(value => !value);
    setPassword('');
    setConfirmation('');
    setShowPassword(false);
    setError('');
    setSuccess('');
  }

  if (status === 'authenticated') return <Navigate to={destination} replace />;

  return <div className="auth-page auth-page--public">
    <header className="auth-header"><Brand onClick={() => navigate('/')} /><Link className="auth-home-link" to="/">서비스 소개로 돌아가기<Icon name="arrow" /></Link></header>
    <main className="auth-main" aria-labelledby="login-intro-title">
      <section className="auth-intro" aria-labelledby="login-intro-title">
        <span className="pill">기준부터 결과까지, 크리에이티브체커</span>
        <h1 id="login-intro-title">검수의 시작을,<br /><em>한곳에서.</em></h1>
        <p>로그인하고 이미지와 요구사항을 준비하세요.<br />확정한 기준으로 검수하고 근거를 확인할 수 있어요.</p>
        <figure className="auth-example"><img src={posterReviewExample} alt="포스터와 항목별 판정 근거를 나란히 표시한 예시" width="921" height="543" />
          <figcaption>포스터 검수 결과 예시</figcaption></figure>
      </section>
      <section className="auth-card" aria-labelledby="login-title">
        <div className="auth-card-heading"><span className="auth-symbol"><Icon name="lock" /></span><h2 id="login-title" ref={formTitle} tabIndex={-1}>{signup ? '처음 오셨나요?' : '다시 만나 반가워요.'}</h2><p>{signup ? '계정을 만들고 첫 검수를 준비하세요.' : '아이디와 비밀번호로 로그인해 주세요.'}</p></div>
        <form className="auth-form" onSubmit={handleLogin} noValidate aria-busy={submitting || checking}>
          {signup && <div className="auth-field"><label htmlFor="signup-name">이름</label><input id="signup-name" name="display_name" ref={nameInput} value={displayName} onChange={event => { setDisplayName(event.target.value); setError(''); }} placeholder="이름을 입력해 주세요" autoComplete="name" maxLength={100} required readOnly={submitting} /></div>}
          <div className="auth-field"><label htmlFor="login-username">{signup ? '이메일' : '아이디'}</label><input id="login-username" name={signup ? 'email' : 'username'} type={signup ? 'email' : 'text'} ref={usernameInput} value={username} onChange={event => { setUsername(event.target.value); setError(''); }} placeholder={signup ? 'name@example.com' : '이메일 또는 아이디를 입력해 주세요'} autoComplete={signup ? 'email' : 'username'} autoCapitalize="none" spellCheck={false} required readOnly={submitting} /></div>
          <div className="auth-field"><label htmlFor="login-password">비밀번호</label><div className="auth-password"><input id="login-password" name="password" ref={passwordInput} type={showPassword ? 'text' : 'password'} value={password} onChange={event => { setPassword(event.target.value); setError(''); }} placeholder={signup ? '8~128자로 입력해 주세요' : '비밀번호를 입력해 주세요'} autoComplete={signup ? 'new-password' : 'current-password'} aria-describedby={signup ? 'signup-password-hint' : undefined} required readOnly={submitting} /><button className="auth-password-toggle" type="button" aria-label={showPassword ? '비밀번호 숨기기' : '비밀번호 표시'} aria-pressed={showPassword} onClick={() => setShowPassword(value => !value)}>{showPassword ? '숨기기' : '표시'}</button></div>{signup && <p id="signup-password-hint" className="auth-field-hint">8~128자, 숫자와 특수문자를 각각 하나 이상 포함해 주세요. 공백도 사용할 수 있어요.</p>}</div>
          {signup && <div className="auth-field"><label htmlFor="signup-password-confirm">비밀번호 확인</label><input id="signup-password-confirm" name="password_confirmation" ref={confirmationInput} type={showPassword ? 'text' : 'password'} value={confirmation} onChange={event => { setConfirmation(event.target.value); setError(''); }} placeholder="비밀번호를 다시 입력해 주세요" autoComplete="new-password" required readOnly={submitting} /></div>}
          <ErrorMessage message={error} />
          {success && <p className="auth-success" role="status">{success}</p>}
          {checking && <p className="auth-notice" role="status"><span className="auth-spinner" aria-hidden="true" />로그인 상태를 확인하고 있어요.</p>}
          {unavailable && <div className="auth-unavailable"><ErrorMessage message={authErrorMessage(sessionError, 'session')} /><button className="text-btn" type="button" onClick={retrySession}>로그인 상태 다시 확인하기</button></div>}
          <button className="auth-submit" type="submit" disabled={submitting || checking || unavailable}>{submitting ? (signup ? '가입 중…' : '로그인 중…') : (signup ? '가입하기' : '로그인')}{!submitting && <Icon name="arrow" />}</button>
          <div className="auth-switch"><span>{signup ? '이미 계정이 있나요?' : '아직 계정이 없나요?'}</span><button className="text-btn" type="button" onClick={switchMode} disabled={submitting || checking}>{signup ? '로그인으로 돌아가기' : '회원가입'}</button></div>
          <p className="auth-note">자동 판정과 담당자의 최종 승인은 구분됩니다.</p>
        </form>
      </section>
    </main>
  </div>;
}

