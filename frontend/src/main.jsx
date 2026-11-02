import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';
import App from './App.jsx';
const RebuildRoot = React.lazy(() => import('./rebuild/Root.tsx'));
const V1App = React.lazy(() => import('./v1/App.jsx'));
import { AuthProvider } from './auth/AuthProvider.jsx';
import './styles.css';
import './styles/fairweather.css';

// 모든 페이지에 URL 라우팅과 서버 세션 상태를 공유한다.
createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {import.meta.env.VITE_API_PROFILE === 'rebuild' ? <React.Suspense fallback={<p role="status">프로젝트를 준비하고 있어요.</p>}><RebuildRoot /></React.Suspense> : import.meta.env.VITE_API_PROFILE === 'v1' ? <React.Suspense fallback={<p role="status">화면을 준비하고 있어요.</p>}><V1App /></React.Suspense> : <BrowserRouter>
      <AuthProvider><App /></AuthProvider>
    </BrowserRouter>}
  </React.StrictMode>,
);
