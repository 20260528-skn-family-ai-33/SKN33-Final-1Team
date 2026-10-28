import React from 'react';
import { createBrowserRouter, Link, Navigate, RouterProvider, useLocation, useParams } from 'react-router';
import { AuthProvider, LoginPage, RequireAuth } from './auth.jsx';
import { Workspace } from './components.jsx';
import { ProjectsPage, NewDraftPage, DraftListPage } from './projectPages.jsx';
import { DraftPage, CriteriaPage } from './criteriaPages.jsx';
import { HistoryPage, RunPage } from './reviewPages.jsx';
import { UUID } from './contracts.js';
import './v1.css';
const ModelTestPage = React.lazy(() => import('../pages/modeltest/ModelTestPage.jsx'));
function NotFound() { return <main className="auth-state"><section className="auth-state-card"><h1>페이지를 찾을 수 없습니다.</h1><Link className="btn primary" to="/projects">프로젝트 목록</Link></section></main>; }
function ValidRoute({ children }) {
  const params = useParams(); const location = useLocation();
  return Object.values(params).every(value => UUID.test(value)) ? <React.Fragment key={location.pathname}>{children}</React.Fragment> : <NotFound />;
}
function RouteFailure() { return <main className="auth-state"><section className="auth-state-card"><h1>화면을 표시할 수 없습니다.</h1><p>다시 조회해 저장된 상태를 확인해 주세요.</p><button className="btn primary" onClick={() => window.location.reload()}>화면 다시 조회</button></section></main>; }
function Welcome() { return <main className="v1-welcome"><span className="pill">CREATIVE CHECKER</span><h1>검수의 기준부터<br />근거까지, 한곳에서.</h1><p>이미지와 요구사항을 등록하고, 확정한 기준으로 검수하세요.<br />프로젝트별로 저장된 초안과 실행 이력을 이어서 확인할 수 있습니다.</p><div className="v1-actions"><Link className="btn primary" to="/projects">내 프로젝트 시작하기</Link><Link className="btn secondary" to="/login">로그인</Link><a className="btn secondary" href="/modeltest">모델 테스트</a></div></main>; }
const protectedPage = element => <ValidRoute>{element}</ValidRoute>;
const router = createBrowserRouter([{ errorElement: <RouteFailure />, children: [
  { path: '/', element: <Welcome /> }, { path: '/login', element: <LoginPage /> },
  { path: '/modeltest', element: <React.Suspense fallback={<p role="status">모델 테스트를 준비하고 있어요.</p>}><ModelTestPage /></React.Suspense> },
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
