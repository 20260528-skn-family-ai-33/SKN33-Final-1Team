import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { getSession, loginSession, logoutSession } from '../api/auth.js';

const AuthContext = createContext(null);

// 서버 세션과 확인 상태를 공유하고 늦게 도착한 이전 요청은 반영하지 않는다.
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [status, setStatus] = useState('checking');
  const [error, setError] = useState(null);
  const operation = useRef(0);

  // 새로고침과 재확인 시 서버 응답으로 로그인 여부를 복원한다.
  const refreshSession = useCallback(async () => {
    const current = ++operation.current;
    setStatus('checking');
    setError(null);
    try {
      const session = await getSession();
      if (current !== operation.current) return;
      setUser(session);
      setStatus(session ? 'authenticated' : 'anonymous');
    } catch (failure) {
      if (current !== operation.current) return;
      setUser(null);
      setStatus('unavailable');
      setError(failure);
    }
  }, []);

  // 서버가 확인한 사용자만 저장하며 비밀번호나 인증 토큰은 보관하지 않는다.
  const login = useCallback(async (credentials) => {
    const current = ++operation.current;
    setError(null);
    try {
      const session = await loginSession(credentials);
      if (current !== operation.current) return;
      setUser(session);
      setStatus('authenticated');
      return session;
    } catch (failure) {
      if (current === operation.current) setError(failure);
      throw failure;
    }
  }, []);

  // 로그아웃 실패 시 현재 세션을 유지하고 재시도할 수 있도록 오류를 전달한다.
  const logout = useCallback(async () => {
    const current = ++operation.current;
    setError(null);
    try {
      await logoutSession();
      if (current !== operation.current) return;
      setUser(null);
      setStatus('anonymous');
    } catch (failure) {
      if (current === operation.current) setError(failure);
      throw failure;
    }
  }, []);

  // StrictMode 재실행과 언마운트 뒤에는 이전 세션 확인 결과를 무시한다.
  useEffect(() => {
    // 명시적인 세션 만료만 처리하고 권한 부족이나 CSRF 오류는 세션을 유지한다.
    function handleSessionExpired() {
      operation.current += 1;
      setUser(null);
      setStatus('anonymous');
      setError(null);
    }
    window.addEventListener('baro:session-expired', handleSessionExpired);
    refreshSession();
    return () => {
      operation.current += 1;
      window.removeEventListener('baro:session-expired', handleSessionExpired);
    };
  }, [refreshSession]);

  const value = useMemo(() => ({ user, status, error, login, logout, refreshSession }), [user, status, error, login, logout, refreshSession]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// 인증 컨텍스트를 사용하는 화면에 일관된 세션 인터페이스를 제공한다.
export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth는 AuthProvider 안에서 사용해야 합니다.');
  return value;
}
