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
const PosterLogoPage = React.lazy(() => import('./pages/review/PosterLogoPage.jsx'));
const SampleDemoPage = React.lazy(() => import('./pages/sample/SampleDemoPage.jsx'));
const LocalReviewPreview = import.meta.env.DEV ? React.lazy(() => import('./pages/review/LocalReviewPreview.jsx')) : null;
const LocalImprovementPreview = import.meta.env.DEV ? React.lazy(() => import('./pages/review/LocalImprovementPreview.jsx')) : null;
// 개발 서버 전용: 소개·로그인 화면의 장면을 모아 촬영 도구로 점검하는 화면
const StageRehearsal = import.meta.env.DEV ? React.lazy(() => import('./stageshow/Rehearsal.tsx')) : null;

// 소개·로그인·검수를 URL로 구분하고 검수 진입 전에 서버 세션을 확인한다.
function AppRoutes() {
  return <Routes>
    <Route path="/" element={<LandingPage />} />
    <Route path="/login" element={<LoginPage />} />
    {/* 로그인 없이 둘러보는 샘플 체험. 서버로 아무것도 보내지 않고 미리 만든 예시 결과를 보여 준다. */}
    <Route path="/sample" element={<React.Suspense fallback={<p role="status">샘플 체험을 준비하고 있어요.</p>}><SampleDemoPage /></React.Suspense>} />
    <Route path="/review/new" element={<RequireAuth><ReviewPage /></RequireAuth>} />
    <Route path="/review/tasks" element={<RequireAuth><MyTasksPage /></RequireAuth>} />
    <Route path="/review/logo" element={<RequireAuth><React.Suspense fallback={<p role="status">포스터 로고 확정 화면을 준비하고 있어요.</p>}><PosterLogoPage /></React.Suspense></RequireAuth>} />
    {import.meta.env.DEV && <Route path="/review/preview" element={<React.Suspense fallback={<p role="status">미리보기 준비 중…</p>}><LocalReviewPreview /></React.Suspense>} />}
    {import.meta.env.DEV && <Route path="/review/improvements" element={<React.Suspense fallback={<p role="status">개선 화면 미리보기 준비 중…</p>}><LocalImprovementPreview /></React.Suspense>} />}
    {import.meta.env.DEV && <Route path="/stageshow" element={<React.Suspense fallback={<p role="status">장면 준비 중…</p>}><StageRehearsal /></React.Suspense>} />}
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
