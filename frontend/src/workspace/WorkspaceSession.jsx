import React, { createContext, useCallback, useContext, useLayoutEffect, useMemo, useRef, useState } from 'react';

const WorkspaceContext = createContext(null);
const noop = () => {};

// 인증은 유지하고, 상단 도구 이동 때만 입력·파일·진행 상태를 새로 만든다.
export function WorkspaceSession({ children }) {
  const [epoch, setEpoch] = useState(0);
  const listeners = useRef(new Set());
  const register = useCallback(listener => {
    listeners.current.add(listener);
    return () => listeners.current.delete(listener);
  }, []);
  const reset = useCallback(() => {
    // 라우트가 바뀌기 전에 기존 요청을 중단해 늦은 응답의 화면 이동을 막는다.
    for (const listener of listeners.current) listener();
    setEpoch(value => value + 1);
  }, []);
  const value = useMemo(() => ({ epoch, register, reset }), [epoch, register, reset]);
  return <WorkspaceContext.Provider value={value}><React.Fragment key={epoch}>{children}</React.Fragment></WorkspaceContext.Provider>;
}

export function useWorkspaceReset(onReset) {
  const context = useContext(WorkspaceContext);
  const callback = useRef(onReset);
  callback.current = onReset;
  const enabled = typeof onReset === 'function';
  useLayoutEffect(() => {
    if (enabled && context) return context.register(() => callback.current());
  }, [context?.register, enabled]);
  return context?.reset || noop;
}

export function useFreshWorkspace() {
  return (useContext(WorkspaceContext)?.epoch || 0) > 0;
}
