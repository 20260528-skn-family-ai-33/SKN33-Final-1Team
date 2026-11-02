import React from 'react';
import { Link, Route, Routes } from 'react-router';
import { useAuth } from './auth/AuthProvider.jsx';
import { RequireAuth } from './auth/RequireAuth.jsx';
import LandingPage from './pages/LandingPage.jsx';
import { LoginPage } from './pages/LoginPage.jsx';
import ReviewPage from './pages/review/ReviewPage.jsx';
import { ReviewProvider } from './pages/review/ReviewProvider.jsx';
import VideoReviewPage from './pages/video/VideoReviewPage.jsx';
const ModelTestPage = React.lazy(() => import('./pages/modeltest/ModelTestPage.jsx'));
const LogoConfirmPage = React.lazy(() => import('./pages/video/LogoConfirmPage.jsx'));

// 소개·로그인·검수를 URL로 구분하고 검수 진입 전에 서버 세션을 확인한다.
function AppRoutes() {
  return <Routes>
    <Route path="/" element={<LandingPage />} />
    <Route path="/login" element={<LoginPage />} />
    <Route path="/review/new" element={<RequireAuth><ReviewPage /></RequireAuth>} />
    <Route path="/video" element={<RequireAuth><VideoReviewPage /></RequireAuth>} />
    <Route path="/video/logo" element={<RequireAuth><React.Suspense fallback={<p role="status">로고 확정 화면을 준비하고 있어요.</p>}><LogoConfirmPage /></React.Suspense></RequireAuth>} />
    <Route path="/modeltest" element={<React.Suspense fallback={<p role="status">모델 테스트를 준비하고 있어요.</p>}><ModelTestPage /></React.Suspense>} />
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
