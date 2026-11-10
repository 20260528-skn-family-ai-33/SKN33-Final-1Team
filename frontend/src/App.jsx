import React from 'react';
import { Route, Routes } from 'react-router';
import { useAuth } from './auth/AuthProvider.jsx';
import { RequireAuth } from './auth/RequireAuth.jsx';
import LandingPage from './pages/LandingPage.jsx';
import NotFoundPage from './pages/NotFoundPage.jsx';
import { LoginPage } from './pages/LoginPage.jsx';
import ReviewPage from './pages/review/ReviewPage.jsx';
import MyTasksPage from './pages/review/MyTasksPage.jsx';
import { ReviewProvider } from './pages/review/ReviewProvider.jsx';
import VideoReviewPage from './pages/video/VideoReviewPage.jsx';
const LogoConfirmPage = React.lazy(() => import('./pages/video/LogoConfirmPage.jsx'));
const LocalReviewPreview = import.meta.env.DEV ? React.lazy(() => import('./pages/review/LocalReviewPreview.jsx')) : null;

// 소개·로그인·검수를 URL로 구분하고 검수 진입 전에 서버 세션을 확인한다.
function AppRoutes() {
  return <Routes>
    <Route path="/" element={<LandingPage />} />
    <Route path="/login" element={<LoginPage />} />
    <Route path="/review/new" element={<RequireAuth><ReviewPage /></RequireAuth>} />
    <Route path="/review/tasks" element={<RequireAuth><MyTasksPage /></RequireAuth>} />
    {import.meta.env.DEV && <Route path="/review/preview" element={<React.Suspense fallback={<p role="status">미리보기 준비 중…</p>}><LocalReviewPreview /></React.Suspense>} />}
    <Route path="/video" element={<RequireAuth><VideoReviewPage /></RequireAuth>} />
    <Route path="/video/logo" element={<RequireAuth><React.Suspense fallback={<p role="status">로고 확정 화면을 준비하고 있어요.</p>}><LogoConfirmPage /></React.Suspense></RequireAuth>} />
    <Route path="*" element={<NotFoundPage />} />
  </Routes>;
}

// 상단 도구 이동의 WorkspaceSession 초기화 또는 로그아웃 때 검수 상태를 해제한다.
export default function App() {
  const { status, user } = useAuth();
  if (status === 'authenticated') {
    return <ReviewProvider key={user.id}><AppRoutes /></ReviewProvider>;
  }
  return <AppRoutes />;
}
