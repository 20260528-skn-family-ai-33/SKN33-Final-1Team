import { frameworkJson, frameworkResponse } from './frameworkClient.js';

export const waitForDocumentPoll = (ms, signal) => new Promise((resolve, reject) => {
  signal?.throwIfAborted();
  const abort = () => { clearTimeout(timer); reject(new DOMException('취소됐습니다.', 'AbortError')); };
  const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, ms);
  signal?.addEventListener('abort', abort, { once: true });
});

export const DOCUMENT_WAIT_TIMEOUT_MS = 15 * 60 * 1000;
export const DOCX_MEDIA_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

export function watchDocumentDeadline(controller) {
  let expired = false;
  let stage;
  const requestStartedAt = Date.now();
  let startedAt = requestStartedAt;
  let inferenceStarted = false;
  const expire = () => { expired = true; controller.abort(); };
  let timer = setTimeout(expire, DOCUMENT_WAIT_TIMEOUT_MS);
  return {
    runId: '',
    get expired() { return expired; },
    update(progress) {
      if (stage !== progress.stage) {
        stage = progress.stage;
        startedAt = Date.now();
      }
      if (stage === 'extracting' && !inferenceStarted) {
        inferenceStarted = true;
        clearTimeout(timer);
        timer = setTimeout(expire, DOCUMENT_WAIT_TIMEOUT_MS + 30_000);
      }
      return { ...progress, startedAt, requestStartedAt,
        lastCheckedAt: progress.runId ? Date.now() : undefined };
    },
    stop() { clearTimeout(timer); },
  };
}

// 확장자가 아니라 실제 첫 바이트로 PDF·DOCX를 가른다. DOCX(ZIP)는 이름도 .docx여야 한다.
export async function requirementDocument(file) {
  const header = String.fromCharCode(...new Uint8Array(await file.slice(0, 5).arrayBuffer()));
  if (header === '%PDF-') return file.type === 'application/pdf' ? file : new File([file], file.name, { type: 'application/pdf' });
  if (header.startsWith('PK\x03\x04') && /\.docx$/i.test(file.name)) {
    return file.type === DOCX_MEDIA_TYPE ? file : new File([file], file.name, { type: DOCX_MEDIA_TYPE });
  }
  throw new Error('PDF 또는 Word(.docx) 파일만 읽을 수 있습니다. 파일 형식을 확인해 주세요.');
}

function uploadPath(file, isText, isDocx) {
  if (isText) return '/text-documents';
  return `/${isDocx ? 'docx-documents' : 'documents'}?filename=${encodeURIComponent(file.name)}`;
}

// DOCX는 서버가 본문 글자만 읽어 텍스트 원문으로 저장한다(원본 DOCX는 해시만 돌려받아 대조).
// modelRoute: 자료 입력 첫 화면의 보안 여부(일반 자료 openai, 보안 자료 runpod). 비우면 서버 기본 경로다.
export async function submitDocument(file, { projectId, attempt, attempts, signal, onProgress, modelRoute } = {}) {
  const route = modelRoute && modelRoute !== 'default' ? modelRoute : '';
  const isText = file.type === 'text/plain';
  const isDocx = file.type === DOCX_MEDIA_TYPE;
  const source = isText ? '입력문' : isDocx ? 'DOCX' : 'PDF';
  if (!projectId) throw new Error('요구사항을 저장할 프로젝트를 선택해 주세요.');
  signal?.throwIfAborted();
  if (!globalThis.crypto?.subtle?.digest || !globalThis.crypto?.randomUUID) {
    throw new Error('문서 분석에는 HTTPS 연결이 필요합니다. 개발 PC에서는 http://localhost:5173으로 접속해 주세요.');
  }
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer())))
    .map(byte => byte.toString(16).padStart(2, '0')).join('');
  signal?.throwIfAborted();
  // 같은 원문이라도 보안 여부가 다르면 다른 실행이다(서버가 실행 입력에 경로를 고정한다).
  const cacheKey = `${projectId}:${file.type}:${digest}${route ? `:${route}` : ''}`;
  attempt = attempt || attempts?.get(cacheKey) || {};
  attempts?.set(cacheKey, attempt);
  if (attempt.sha256 && (attempt.sha256 !== digest || attempt.projectId !== projectId
    || (attempt.mediaType && attempt.mediaType !== file.type) || (attempt.modelRoute || '') !== route)) {
    throw new Error('이전 문서 요청과 원문 또는 프로젝트가 다릅니다. 새 요청으로 진행해 주세요.');
  }
  Object.assign(attempt, { sha256: digest, projectId, mediaType: file.type, modelRoute: route,
    uploadKey: attempt.uploadKey || crypto.randomUUID(), extractionKey: attempt.extractionKey || crypto.randomUUID() });
  const project = `/projects/${encodeURIComponent(projectId)}`;
  if (!attempt.documentVersionId) {
    onProgress?.({ stage: 'uploading', percent: 5, message: isDocx ? 'DOCX 본문 글자를 읽어 원문과 버전을 저장하고 있습니다.' : `${source} 원문과 버전을 저장하고 있습니다.` });
    const response = await frameworkResponse(project + uploadPath(file, isText, isDocx), {
      method: 'POST', signal, body: isText ? JSON.stringify({ text: await file.text() }) : file,
      headers: { 'Content-Type': isText ? 'application/json' : isDocx ? DOCX_MEDIA_TYPE : 'application/pdf', 'Idempotency-Key': attempt.uploadKey },
    });
    const record = await response.json();
    // DOCX는 변환된 텍스트가 저장 원문이므로, 올린 DOCX와의 대조는 돌려받은 source 해시로 한다.
    const saved = isDocx ? record?.document?.media_type === 'text/plain' && record?.source?.sha256 : record?.document?.sha256;
    if (!record?.document?.document_version_id || saved !== digest) throw new Error(`${source} 원문 저장 결과를 확인하지 못했습니다.`);
    attempt.documentVersionId = record.document.document_version_id;
  }
  signal?.throwIfAborted();
  if (!attempt.runId) {
    onProgress?.({ stage: 'queued', percent: 15, message: '요구사항 분석을 접수하고 있습니다.' });
    const run = await frameworkJson(project + `/document-versions/${encodeURIComponent(attempt.documentVersionId)}/extractions`
      + (route ? `?model_route=${encodeURIComponent(route)}` : ''), {
      method: 'POST', signal, headers: { 'Idempotency-Key': attempt.extractionKey },
    });
    if (!run?.context?.run_id) throw new Error('문서 분석 접수 결과를 확인하지 못했습니다.');
    attempt.runId = run.context.run_id;
  }
  return attempt;
}

// 취소는 화면 반영만 중단한다. 접수된 실행·요청 키는 유지해 중복 추론을 막는다.
export async function extractDocumentWithProgress(file, { signal, onProgress, onAccepted, pollInterval = 1500, waitFor = waitForDocumentPoll, ...submission } = {}) {
  const attempt = await submitDocument(file, { ...submission, signal, onProgress });
  onAccepted?.(attempt.runId);
  const source = file.type === 'text/plain' ? '입력문' : file.type === DOCX_MEDIA_TYPE ? 'DOCX' : 'PDF';
  const runPath = `/runs/${encodeURIComponent(attempt.runId)}`;
  while (true) {
    signal?.throwIfAborted();
    const run = await frameworkJson(runPath, { signal });
    if (['waiting_criteria', 'waiting_asset'].includes(run.execution_status)) break;
    if (!['queued', 'extracting'].includes(run.execution_status)) {
      const error = new Error(`문서 분석을 완료하지 못했습니다. 실행 기록을 확인해 주세요. (${run.error?.code || run.execution_status})`);
      error.runId = attempt.runId;
      if (run.error?.code === 'USER_CANCELLED') {
        error.message = '대기 작업이 취소되었습니다. 원본과 기록은 보존됩니다.';
        // Only a later explicit upload starts a new extraction; never retry here.
        delete attempt.runId; delete attempt.extractionKey;
      }
      throw error;
    }
    onProgress?.({ runId: attempt.runId, stage: run.execution_status, percent: run.execution_status === 'queued' ? 20 : 60,
      message: run.execution_status === 'queued' ? '문서 분석 차례를 기다리고 있습니다.' : '문서에서 검수 요구사항과 원문 근거를 추출하고 있습니다.' });
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
    throw new Error(`${source}에서 검토할 요구사항을 찾지 못했습니다. 기존 입력은 유지됩니다.`);
  }
  return result;
}

export function extractTextWithProgress(text, options) {
  if (!text.trim() || text.length > 60000) throw new Error('요구사항은 공백을 제외한 내용이 있어야 하며 60,000자 이하여야 합니다.');
  return extractDocumentWithProgress(new File([text], 'requirements.txt', { type: 'text/plain' }), options);
}
