import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';
import App from './rebuild/App.tsx';
import { AuthProvider } from './rebuild/auth.tsx';
import './rebuild/app.css';

const root = document.getElementById('root');
if (!root) throw new Error('화면 진입 요소를 찾을 수 없습니다.');
createRoot(root).render(<StrictMode><BrowserRouter><AuthProvider><App /></AuthProvider></BrowserRouter></StrictMode>);
