import { useCallback, useEffect, useRef, useState } from 'react';
import { useBlocker, useLocation } from 'react-router';
import { useAuth } from './auth.jsx';
import { PollLoop } from './polling.js';
import * as api from './api.js';
export function useResource(key, read) {
  const fn = useRef(read); fn.current = read; const [reload, setReload] = useState(0); const [state, setState] = useState({ key: null, data: null, error: null, loading: true });
  useEffect(() => {
    const control = new AbortController(); let current = true; setState({ key, data: null, error: null, loading: true });
    Promise.resolve().then(() => fn.current(control.signal)).then(data => { if (current && !control.signal.aborted) setState({ key, data, error: null, loading: false }); })
      .catch(error => { if (current && !control.signal.aborted) setState({ key, data: null, error, loading: false }); });
    return () => { current = false; control.abort(); };
  }, [key, reload]);
  return { ...(state.key === key ? state : { data: null, error: null, loading: true }), reload: () => setReload(value => value + 1) };
}
export function useAction(contextKey) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const lock = useRef(false); const epoch = useRef(0); const controller = useRef(null);
  useEffect(() => { epoch.current += 1; lock.current = false; setBusy(false); setError(''); return () => { epoch.current += 1; controller.current?.abort(); }; }, [contextKey]);
  const run = async callback => {
    if (lock.current) return undefined; lock.current = true; setBusy(true); setError(''); const generation = epoch.current; const control = new AbortController(); controller.current = control;
    try { const value = await callback(control.signal); return !control.signal.aborted && generation === epoch.current ? value : undefined; }
    catch (failure) { if (!control.signal.aborted && generation === epoch.current) setError(failure.message); return undefined; }
    finally { if (generation === epoch.current) { lock.current = false; setBusy(false); } }
  };
  return { busy, error, setError, run };
}
export function usePending(projectId, onSuccess) {
  const { user, pending } = useAuth(); const location = useLocation(); const action = useAction(`${user.id}:${projectId}:${location.pathname}`); const [refresh, setRefresh] = useState(0); const success = useRef(onSuccess); success.current = onSuccess;
  async function send(entry) {
    const value = await action.run(async signal => { const body = await api.sendPending(entry, signal); if (!signal.aborted) pending.complete(entry); return body; });
    setRefresh(refresh => refresh + 1); if (value) success.current(value, entry);
  }
  async function submit(operation, body, source = null) {
    try { const entry = pending.prepare(user.id, operation, projectId, body, source); setRefresh(value => value + 1); await send(entry); }
    catch (error) { action.setError(error.message); }
  }
  return { ...action, entries: pending.all(user.id, projectId), persistent: pending.persistent, submit, resume: send,
    discard: entry => { pending.complete(entry); setRefresh(value => value + 1); } };
}
export function useDirty(dirty) {
  const bypass = useRef(false);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => !bypass.current && dirty && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => { if (!dirty) return; const warn = event => { event.preventDefault(); event.returnValue = ''; }; window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn); }, [dirty]);
  useEffect(() => {
    const leave = event => { if (dirty && !window.confirm('저장하지 않은 편집이 있습니다. 로그아웃하면 편집 내용이 사라집니다. 계속할까요?')) event.preventDefault(); };
    window.addEventListener('cc:v1:before-logout', leave); return () => window.removeEventListener('cc:v1:before-logout', leave);
  }, [dirty]);
  return { ...blocker, allow: () => { bypass.current = true; } };
}
export function useReview(projectId, runId, actorId) {
  const [state, setState] = useState({ data: null, error: null }); const loop = useRef(null);
  useEffect(() => {
    setState({ data: null, error: null });
    const current = new PollLoop({ read: signal => api.review(projectId, runId, signal), hidden: () => document.hidden,
      value: data => setState({ data, error: null }), error: error => setState(previous => ({ ...previous, error })) });
    loop.current = current; current.start(); const visible = () => { if (!document.hidden) current.refresh(); };
    document.addEventListener('visibilitychange', visible);
    return () => { current.stop(); document.removeEventListener('visibilitychange', visible); };
  }, [projectId, runId, actorId]);
  return { ...state, refresh: () => loop.current?.refresh() };
}
