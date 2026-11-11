import { frameworkJson } from './frameworkClient.js';

export const maxBatchFiles = 10;
const activeStates = new Set(['queued', 'extracting']);
const prefix = userId => 'creative-checker:pdf-batch:' + encodeURIComponent(userId) + ':';
const storageKey = (userId, batchId) => prefix(userId) + batchId;

export function readPdfBatches(userId) {
  return Object.keys(localStorage).filter(key => key.startsWith(prefix(userId))).flatMap(key => {
    try {
      const batch = JSON.parse(localStorage.getItem(key));
      return batch?.id && Array.isArray(batch.items) ? [batch] : [];
    } catch {
      return [];
    }
  }).sort((left, right) => right.createdAt - left.createdAt);
}

export function savePdfBatch(userId, batch) {
  localStorage.setItem(storageKey(userId, batch.id), JSON.stringify(batch));
}

export function updatePdfBatch(userId, batchId, update) {
  const batch = JSON.parse(localStorage.getItem(storageKey(userId, batchId)));
  if (batch) savePdfBatch(userId, update(batch));
}

export function isActivePdf(item) {
  return item.runId && activeStates.has(item.state);
}

export async function checkPdfBatch(userId, batch, signal) {
  const results = await Promise.allSettled(batch.items.filter(isActivePdf).map(async item => {
    const run = await frameworkJson('/runs/' + encodeURIComponent(item.runId), { signal, cache: 'no-store' });
    if (run.context?.run_id !== item.runId) throw new Error('문서 분석 실행이 일치하지 않습니다.');
    const state = run.step_records?.some(step => step.role === 'A02' && step.status === 'completed')
      || ['waiting_criteria', 'waiting_asset'].includes(run.execution_status) ? 'ready'
      : run.error?.code === 'USER_CANCELLED' ? 'cancelled'
      : activeStates.has(run.execution_status) || run.execution_status === 'failed' ? run.execution_status
      : 'needs_check';
    return [item.runId, state];
  }));
  signal.throwIfAborted();
  const states = new Map(results.filter(result => result.status === 'fulfilled').map(result => result.value));
  if (states.size) {
    updatePdfBatch(userId, batch.id, current => ({ ...current, items: current.items.map(item =>
      isActivePdf(item) && states.has(item.runId) ? { ...item, state: states.get(item.runId) } : item) }));
  }
  return results.every(result => result.status === 'fulfilled');
}
