import { frameworkJson, frameworkResponse } from './frameworkClient.js';

const wait = (ms, signal) => new Promise((resolve, reject) => {
  signal?.throwIfAborted();
  const abort = () => { clearTimeout(timer); reject(new DOMException('취소됐습니다.', 'AbortError')); };
  const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, ms);
  signal?.addEventListener('abort', abort, { once: true });
});

// 취소는 화면 반영만 중단한다. 접수된 실행·요청 키는 유지해 중복 추론을 막는다.
export async function extractDocumentWithProgress(file, { projectId, attempt, attempts, signal, onProgress,
  pollInterval = 1500, waitFor = wait } = {}) {
  if (!projectId) throw new Error('PDF를 저장할 프로젝트를 선택해 주세요.');
  signal?.throwIfAborted();
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer())))
    .map(byte => byte.toString(16).padStart(2, '0')).join('');
  signal?.throwIfAborted();
  const cacheKey = `${projectId}:${digest}`;
  attempt = attempt || attempts?.get(cacheKey) || {};
  attempts?.set(cacheKey, attempt);
  if (attempt.sha256 && (attempt.sha256 !== digest || attempt.projectId !== projectId)) {
    throw new Error('이전 문서 요청과 파일 또는 프로젝트가 다릅니다. 새 PDF 요청으로 진행해 주세요.');
  }
  Object.assign(attempt, { sha256: digest, projectId,
    uploadKey: attempt.uploadKey || crypto.randomUUID(), extractionKey: attempt.extractionKey || crypto.randomUUID() });
  const project = `/projects/${encodeURIComponent(projectId)}`;
  if (!attempt.documentVersionId) {
    onProgress?.({ stage: 'uploading', percent: 5, message: 'PDF 원문과 버전을 저장하고 있습니다.' });
    const response = await frameworkResponse(project + `/documents?filename=${encodeURIComponent(file.name)}`, {
      method: 'POST', signal, body: file,
      headers: { 'Content-Type': 'application/pdf', 'Idempotency-Key': attempt.uploadKey },
    });
    const record = await response.json();
    if (!record?.document?.document_version_id || record.document.sha256 !== digest) throw new Error('PDF 원문 저장 결과를 확인하지 못했습니다.');
    attempt.documentVersionId = record.document.document_version_id;
  }
  signal?.throwIfAborted();
  if (!attempt.runId) {
    onProgress?.({ stage: 'queued', percent: 15, message: '요구사항 분석을 접수하고 있습니다.' });
    const run = await frameworkJson(project + `/document-versions/${encodeURIComponent(attempt.documentVersionId)}/extractions`, {
      method: 'POST', signal, headers: { 'Idempotency-Key': attempt.extractionKey },
    });
    if (!run?.context?.run_id) throw new Error('문서 분석 접수 결과를 확인하지 못했습니다.');
    attempt.runId = run.context.run_id;
  }
  const runPath = `/runs/${encodeURIComponent(attempt.runId)}`;
  while (true) {
    signal?.throwIfAborted();
    const run = await frameworkJson(runPath, { signal });
    if (['waiting_criteria', 'waiting_asset'].includes(run.execution_status)) break;
    if (!['queued', 'extracting'].includes(run.execution_status)) {
      const error = new Error(`문서 분석을 완료하지 못했습니다. 실행 기록을 확인해 주세요. (${run.error?.code || run.execution_status})`);
      error.runId = attempt.runId;
      throw error;
    }
    onProgress?.({ stage: run.execution_status, percent: run.execution_status === 'queued' ? 20 : 60,
      message: run.execution_status === 'queued' ? '분석을 기다리고 있습니다.' : '문서 전체의 검수 항목과 표시 문단을 정리하고 있습니다.' });
    await waitFor(pollInterval, signal);
  }
  signal?.throwIfAborted();
  onProgress?.({ stage: 'formatting', percent: 95, message: '원문 근거와 문단별 요구사항을 불러오고 있습니다.' });
  const result = await frameworkJson(runPath + '/requirements', { signal });
  signal?.throwIfAborted();
  if (result.framework?.run_id !== attempt.runId || result.framework?.document_version_id !== attempt.documentVersionId) {
    throw new Error('분석 결과의 문서 버전을 확인하지 못했습니다. 기존 입력은 유지됩니다.');
  }
  if (!Array.isArray(result.conditions) || (!result.conditions.length && !(Array.isArray(result.pending) && result.pending.length))) {
    throw new Error('PDF에서 검토할 요구사항을 찾지 못했습니다. 기존 입력은 유지됩니다.');
  }
  return result;
}
