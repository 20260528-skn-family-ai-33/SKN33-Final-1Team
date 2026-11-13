import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';
import App from './App.jsx';
const RebuildRoot = React.lazy(() => import('./rebuild/Root.tsx'));
const V1App = React.lazy(() => import('./v1/App.jsx'));
const LocalReviewPreview = import.meta.env.DEV ? React.lazy(() => import('./pages/review/LocalReviewPreview.jsx')) : null;
const uiOnly = import.meta.env.DEV && import.meta.env.MODE === 'ui';
import { AuthProvider } from './auth/AuthProvider.jsx';
import { HelpChatProvider } from './help-chat/HelpChatProvider.jsx';
import { WorkspaceSession } from './workspace/WorkspaceSession.jsx';
import './styles.css';
import './styles/fairweather.css';
import './styles/optimus-theme.css';

// 모든 페이지에 URL 라우팅과 서버 세션 상태를 공유한다.
createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {uiOnly ? <BrowserRouter><WorkspaceSession><React.Suspense fallback={<p role="status">UI 미리보기 준비 중…</p>}><LocalReviewPreview uiOnly /></React.Suspense></WorkspaceSession></BrowserRouter>
      : import.meta.env.VITE_API_PROFILE === 'rebuild' ? <React.Suspense fallback={<p role="status">프로젝트를 준비하고 있어요.</p>}><RebuildRoot /></React.Suspense> : import.meta.env.VITE_API_PROFILE === 'v1' ? <React.Suspense fallback={<p role="status">화면을 준비하고 있어요.</p>}><V1App /></React.Suspense> : <BrowserRouter>
      <AuthProvider><HelpChatProvider><WorkspaceSession><App /></WorkspaceSession></HelpChatProvider></AuthProvider>
    </BrowserRouter>}
  </React.StrictMode>,
);
