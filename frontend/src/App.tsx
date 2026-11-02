import { Navigate, Route, Routes, Link } from 'react-router';
import { AppFrame, LoginPage, RequireAuth } from './auth';
import { Account, AdminUsers, ProjectList, ProjectSettings, Workspace } from './Projects';
import { DocumentsCriteria } from './DocumentsCriteria';
import { Assets, Logos } from './Assets';
import { Runs, RunDetail } from './Runs';
import { Approvals } from './Approvals';
import { History, Relations } from './Relations';
import { Report } from './Report';
import LandingPage from './pages/LandingPage.jsx';

export default function App() {
  return <Routes><Route path="/" element={<LandingPage profile="rebuild" />} /><Route path="/login" element={<LoginPage />} /><Route element={<RequireAuth />}><Route element={<AppFrame />}>
    <Route path="/projects" element={<ProjectList />} />
    <Route path="/account" element={<Account />} /><Route path="/admin/users" element={<AdminUsers />} />
    <Route path="/projects/:projectId" element={<Workspace />}><Route index element={<Navigate to="documents" replace />} />
      <Route path="documents" element={<DocumentsCriteria />} /><Route path="criteria" element={<DocumentsCriteria />} />
      <Route path="assets" element={<Assets />} /><Route path="logos" element={<Logos />} /><Route path="runs" element={<Runs />} />
      <Route path="runs/:runId" element={<RunDetail />} /><Route path="approvals" element={<Approvals />} />
      <Route path="relations" element={<Relations />} /><Route path="history" element={<History />} /><Route path="settings" element={<ProjectSettings />} />
    </Route><Route path="/projects/:projectId/runs/:runId/report" element={<Report />} />
    <Route path="*" element={<main id="main" className="page"><h1>페이지를 찾을 수 없습니다</h1><Link className="text-link" to="/projects">프로젝트 목록으로 이동</Link></main>} />
  </Route></Route></Routes>;
}
