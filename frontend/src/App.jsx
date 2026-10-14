import React, { useState } from 'react';
import { Link, Route, Routes, useNavigate } from 'react-router';
import { useAuth } from './auth/AuthProvider.jsx';
import { RequireAuth } from './auth/RequireAuth.jsx';
import LandingPage from './pages/LandingPage.jsx';
import { LoginPage } from './pages/LoginPage.jsx';
import ReviewPage from './pages/review/ReviewPage.jsx';
import { ReviewProvider } from './pages/review/ReviewProvider.jsx';

// 인증된 사용자와 로그아웃 동작을 기존 검수 화면에 연결한다.
function ReviewRoute() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [logoutPending, setLogoutPending] = useState(false);
  const [logoutError, setLogoutError] = useState('');

  // 서버 세션이 종료된 경우에만 로그인 화면으로 이동한다.
  async function handleLogout() {
    if (logoutPending) return;
    setLogoutPending(true);
    setLogoutError('');
    try {
      await logout();
      navigate('/login', { replace: true });
    } catch (error) {
      setLogoutError(error.message || '로그아웃하지 못했습니다. 다시 시도해 주세요.');
    } finally {
      setLogoutPending(false);
    }
  }

  return <ReviewPage user={user} onLogout={handleLogout} logoutPending={logoutPending}
    logoutError={logoutError} onHome={() => navigate('/')} />;
}

// 소개·로그인·검수를 URL로 구분하고 검수 진입 전에 서버 세션을 확인한다.
function AppRoutes() {
  return <Routes>
    <Route path="/" element={<LandingPage />} />
    <Route path="/login" element={<LoginPage />} />
    <Route path="/review/new" element={<RequireAuth><ReviewRoute /></RequireAuth>} />
    <Route path="*" element={<main className="landing-section"><h1>페이지를 찾을 수 없습니다.</h1>
      <p>주소를 확인하거나 서비스 소개에서 다시 시작해 주세요.</p>
      <Link className="btn primary" to="/">서비스 소개로 이동</Link></main>} />
  </Routes>;
}

// 로그인 세션 동안 초안과 진행 중인 검수를 유지하고 로그아웃하면 해제한다.
export default function App() {
  const { status, user } = useAuth();
  if (status === 'authenticated') {
    return <ReviewProvider key={user.id}><AppRoutes /></ReviewProvider>;
  }
  return <AppRoutes />;
}
