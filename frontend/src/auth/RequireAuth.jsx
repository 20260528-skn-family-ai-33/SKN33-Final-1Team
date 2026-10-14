import React from 'react';
import { Link, Navigate, useLocation } from 'react-router';
import { ErrorMessage } from '../components/ErrorMessage.jsx';
import { useAuth } from './AuthProvider.jsx';
import { authErrorMessage } from './helpers.js';
import '../styles/auth.css';

// 세션 확인 후 검수 화면 진입을 안내하며 실제 접근 권한은 서버에서 검증한다.
export function RequireAuth({ children }) {
  const { status, error, refreshSession } = useAuth();
  const location = useLocation();

  if (status === 'checking') {
    return <main className="auth-state"><div className="auth-state-card" role="status"><span className="auth-spinner" aria-hidden="true" /><h1>로그인 상태를 확인하고 있어요.</h1><p>잠시만 기다려 주세요.</p></div></main>;
  }
  if (status === 'unavailable') {
    return <main className="auth-state"><section className="auth-state-card" aria-labelledby="auth-unavailable-title"><h1 id="auth-unavailable-title">로그인 상태를 확인할 수 없어요.</h1><ErrorMessage message={authErrorMessage(error, 'session')} /><div className="auth-state-actions"><button className="btn primary" onClick={refreshSession}>다시 확인하기</button><Link className="btn secondary" to="/">서비스 소개로</Link></div></section></main>;
  }
  if (status === 'anonymous') return <Navigate to="/login" replace state={{ from: location }} />;
  return children;
}
