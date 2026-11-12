import { ApiError, fetchJson } from '../../api/client.js';
import { waitForDocumentPoll } from '../review/frameworkDocument.js';

const EXTRACTIONS = '/api/video-reviews/requirements/extractions';

export async function extractVideoDocument(file, {
  signal, onProgress, request = fetchJson, waitFor = waitForDocumentPoll,
} = {}) {
  signal?.throwIfAborted();
  const body = new FormData();
  body.append('document', file);
  let job = await request(EXTRACTIONS, { method: 'POST', body, signal });
  if (typeof job?.id !== 'string' || !job.id || job.status !== 'queued') {
    throw new Error('영상 문서 분석 접수 결과를 확인하지 못했습니다.');
  }
  const id = job.id;
  const path = EXTRACTIONS + '/' + encodeURIComponent(id);
  while (['queued', 'running'].includes(job.status)) {
    const running = job.status === 'running';
    onProgress?.({ stage: running ? 'extracting' : 'queued', percent: running ? 60 : 20,
      message: running ? '영상 계약서에서 기준과 제외 사유를 찾고 있습니다.' : '영상 문서 분석을 접수했습니다.' });
    await waitFor(1500, signal);
    signal?.throwIfAborted();
    job = await request(path, { signal, cache: 'no-store' });
    if (job?.id !== id) throw new Error('접수한 영상 문서의 분석 결과를 확인하지 못했습니다.');
  }
  signal?.throwIfAborted();
  if (job.status === 'failed') {
    const code = job.error?.code || 'VIDEO_DOCUMENT_ANALYSIS_FAILED';
    throw new ApiError(`${job.error?.message || '영상 문서 분석을 완료하지 못했습니다.'} (${code})`, { code });
  }
  if (job.status !== 'completed' || !Array.isArray(job.result?.items)) {
    throw new Error('영상 문서 분석 결과를 확인하지 못했습니다.');
  }
  return job.result;
}
