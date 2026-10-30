import { ApiError, fetchResponse } from './api/client.js';

// UTF-8 문자·JSON 줄이 네트워크 청크 사이에서 나뉘어도 완성된 이벤트만 반영한다.
export async function extractDocumentWithProgress(file, { signal, onProgress }) {
  const form = new FormData();
  form.append('document', file);
  const response = await fetchResponse('/api/requirements/extract/stream', { method: 'POST', body: form, signal });
  if (!response.body || !response.headers.get('content-type')?.includes('application/x-ndjson')) {
    throw new ApiError('진행률을 받을 수 없습니다. 검수 서버를 다시 실행해 주세요.');
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = '';
  let previous = 0;
  const invalid = () => new ApiError('문서 진행 상태를 확인하지 못했습니다. 기존 입력은 유지됩니다.');
  function consume(line) {
    signal?.throwIfAborted();
    if (!line.trim()) return null;
    let event;
    try { event = JSON.parse(line); } catch { throw invalid(); }
    if (event?.type === 'error') throw new ApiError(event.message || 'PDF 조건 추출에 실패했습니다.', { status: event.status || 502 });
    if (event?.type === 'result' && event.result && Array.isArray(event.result.conditions)) return event.result;
    if (event?.type !== 'progress' || !Number.isInteger(event.percent) || event.percent < 0 || event.percent >= 100
      || typeof event.message !== 'string' || typeof event.stage !== 'string') throw invalid();
    if (event.percent >= previous) {
      previous = event.percent;
      onProgress?.(event);
    }
    return null;
  }
  try {
    while (true) {
      signal?.throwIfAborted();
      const { done, value } = await reader.read();
      signal?.throwIfAborted();
      pending += decoder.decode(value, { stream: !done });
      if (pending.length > 5_000_000) throw invalid();
      let newline;
      while ((newline = pending.indexOf('\n')) >= 0) {
        const line = pending.slice(0, newline);
        pending = pending.slice(newline + 1);
        const result = consume(line);
        if (result) return result;
      }
      if (done) {
        const result = consume(pending);
        if (result) return result;
        throw new ApiError('문서 분석 연결이 중단됐습니다. 기존 입력은 유지됩니다. 잠시 후 다시 시도해 주세요.');
      }
    }
  } catch (error) {
    if (error instanceof ApiError || error.name === 'AbortError') throw error;
    throw new ApiError('문서 분석 연결이 중단됐습니다. 기존 입력은 유지됩니다. 잠시 후 다시 시도해 주세요.');
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
