import { useEffect, useRef, useState } from 'react';
import { frameworkJson } from './frameworkClient.js';

export function useRunAdvice(path, accepts, { stale, unavailable }) {
  const active = useRef(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => () => active.current?.abort(), []);

  async function request() {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setNote(null);
    setError('');
    try {
      const result = await frameworkJson(path, { method: 'POST', signal: controller.signal });
      if (controller.signal.aborted) return;
      if (accepts(result)) setNote(result);
      else setError(stale);
    } catch (error) {
      if (!controller.signal.aborted) setError(error.status === 409 ? stale : unavailable);
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        active.current = null;
      }
    }
  }
  return { busy, note, error, request };
}
