import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, list } from './api';

export function useApi<T>(path: string | null, asList = false) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [revision, setRevision] = useState(0);
  const previousPath = useRef<string | null>(null);
  const reload = useCallback(() => setRevision(value => value + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    if (previousPath.current !== path) setData(null);
    previousPath.current = path;
    setError(null); setLoading(Boolean(path));
    if (!path) return () => controller.abort();
    (asList ? list<unknown>(path, controller.signal) as Promise<T> : api<T>(path, { signal: controller.signal }))
      .then(value => { if (!controller.signal.aborted) setData(value); })
      .catch(failure => { if (!controller.signal.aborted) setError(failure); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [path, revision, asList]);
  return { data, error, loading, reload, setData };
}

export function useAction() {
  const lock = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [success, setSuccess] = useState('');
  async function run<T>(action: () => Promise<T>, message = ''): Promise<T | undefined> {
    if (lock.current) return undefined;
    lock.current = true; setPending(true); setError(null); setSuccess('');
    try { const result = await action(); setSuccess(message); return result; }
    catch (failure) { setError(failure); return undefined; }
    finally { lock.current = false; setPending(false); }
  }
  return { pending, error, success, run, clear: () => { setError(null); setSuccess(''); } };
}

export function inputError(message: string): never { throw new ApiError(422, 'input_error', message); }
