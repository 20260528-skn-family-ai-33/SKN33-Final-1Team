import assert from 'node:assert/strict';
import { test } from 'node:test';
import { extractVideoDocument } from './videoDocumentAnalysis.js';

const file = new File(['자막 확인'], 'brief.txt', { type: 'text/plain' });
const path = '/api/video-reviews/requirements/extractions';

test('영상 문서는 한 번 접수하고 같은 작업을 조회해 결과를 받는다', async () => {
  const calls = [];
  const stages = [];
  const result = { items: [{ include: true, text: '자막 확인' }] };
  const responses = [{ id: 'doc-job', status: 'queued' }, { id: 'doc-job', status: 'running' },
    { id: 'doc-job', status: 'completed', result }];
  assert.deepEqual(await extractVideoDocument(file, {
    request: async (url, options) => {
      calls.push([url, options.method || 'GET']);
      if (options.method === 'POST') assert.equal(options.body.get('document').name, file.name);
      else assert.equal(options.cache, 'no-store');
      return responses.shift();
    },
    waitFor: async () => {},
    onProgress: progress => stages.push(progress.stage),
  }), result);
  assert.deepEqual(calls, [[path, 'POST'], [path + '/doc-job', 'GET'], [path + '/doc-job', 'GET']]);
  assert.deepEqual(stages, ['queued', 'extracting']);
});

test('백그라운드 모델 실패는 서버 오류 코드와 함께 표시된다', async () => {
  await assert.rejects(extractVideoDocument(file, {
    request: async (_url, options) => options.method === 'POST'
      ? { id: 'doc-job', status: 'queued' }
      : { id: 'doc-job', status: 'failed', error: { code: 'QWEN_OUTCOME_UNKNOWN', message: '응답을 확인하지 못했습니다.' } },
    waitFor: async () => {},
  }), error => error.code === 'QWEN_OUTCOME_UNKNOWN' && error.message.includes('QWEN_OUTCOME_UNKNOWN'));
});
