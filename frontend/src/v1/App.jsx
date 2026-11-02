import React from 'react';
import { createBrowserRouter, Link, Navigate, RouterProvider, useLocation, useParams } from 'react-router';
import { AuthProvider, LoginPage, RequireAuth } from './auth.jsx';
import { Workspace } from './components.jsx';
import { ProjectsPage, NewDraftPage, DraftListPage } from './projectPages.jsx';
import { DraftPage, CriteriaPage } from './criteriaPages.jsx';
import { HistoryPage, RunPage } from './reviewPages.jsx';
import { UUID } from './contracts.js';
import LandingPage from '../pages/LandingPage.jsx';
const ModelTestPage = React.lazy(() => import('../pages/modeltest/ModelTestPage.jsx'));
import './v1.css';
import './workspace-v2.css';
function NotFound() { return <main className="auth-state"><section className="auth-state-card"><h1>페이지를 찾을 수 없습니다.</h1><Link className="btn primary" to="/projects">프로젝트 목록</Link></section></main>; }
function ValidRoute({ children }) {
  const params = useParams(); const location = useLocation();
  return Object.values(params).every(value => UUID.test(value)) ? <React.Fragment key={location.pathname}>{children}</React.Fragment> : <NotFound />;
}
function RouteFailure() { return <main className="auth-state"><section className="auth-state-card"><h1>화면을 표시할 수 없습니다.</h1><p>다시 조회해 저장된 상태를 확인해 주세요.</p><button className="btn primary" onClick={() => window.location.reload()}>화면 다시 조회</button></section></main>; }
const protectedPage = element => <ValidRoute>{element}</ValidRoute>;
const router = createBrowserRouter([{ errorElement: <RouteFailure />, children: [
  { path: '/modeltest', element: <React.Suspense fallback={<p role="status">모델 테스트를 준비하고 있어요.</p>}><ModelTestPage /></React.Suspense> },
  { path: '/', element: <LandingPage profile="v1" /> }, { path: '/login', element: <LoginPage /> },
  { element: <RequireAuth />, children: [{ element: <Workspace />, children: [
    { path: '/projects', element: <ProjectsPage /> }, { path: '/review/new', element: <Navigate to="/projects" replace /> },
    { path: '/projects/:projectId/review/new', element: protectedPage(<NewDraftPage />) },
    { path: '/projects/:projectId/drafts', element: protectedPage(<DraftListPage />) },
    { path: '/projects/:projectId/drafts/:draftId', element: protectedPage(<DraftPage />) },
    { path: '/projects/:projectId/criteria/:versionId', element: protectedPage(<CriteriaPage />) },
    { path: '/projects/:projectId/reviews', element: protectedPage(<HistoryPage />) },
    { path: '/projects/:projectId/reviews/:runId', element: protectedPage(<RunPage />) },
  ] }] }, { path: '*', element: <NotFound /> },
] }]);
export default function V1App() { return <AuthProvider><RouterProvider router={router} /></AuthProvider>; }
