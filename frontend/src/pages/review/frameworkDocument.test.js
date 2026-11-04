import assert from 'node:assert/strict';
import test from 'node:test';
import { createHash } from 'node:crypto';
import { extractDocumentWithProgress } from './frameworkDocument.js';
import { loadDocumentProjects } from './frameworkClient.js';
import { mergeDocumentRequirements } from './documentRequirements.js';

const file = new File(['%PDF-test'], '전체요구사항.pdf', { type: 'application/pdf' });
const sha256 = createHash('sha256').update('%PDF-test').digest('hex');
const prefix = '/api/framework/v1';
const runPath = '/runs/run-1';
const result = { framework: { run_id: 'run-1', document_version_id: 'doc-1' }, page_count: 2, pending: [], conditions: [
  { text: '필수 고지: 무료 관람', type: 'information', label: '필수 고지', requirement: '무료 관람', inspection: '', level: '필수', page: 1, source: '무료 관람' },
  { text: '문구와 이미지가 잘리지 않아야 합니다.', type: 'visual', label: '잘림', requirement: '문구와 이미지가 잘리지 않아야 합니다.', inspection: '', level: '필수', page: 2, source: '문구와 이미지가 잘리지 않아야 합니다.' },
] };

function setup(t, { statuses = ['queued', 'extracting', 'waiting_criteria'], upload, admit } = {}) {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    calls.push({ url, options });
    if (url === prefix + '/csrf') return Response.json({ csrf_token: 'framework-token' });
    assert.ok(url.startsWith(prefix));
    if (options.method === 'POST') {
      assert.equal(new Headers(options.headers).get('X-CSRFToken'), 'framework-token');
      assert.equal(options.credentials, 'same-origin');
      assert.match(new Headers(options.headers).get('Idempotency-Key'), /^[a-f0-9-]{36}$/);
    }
    if (url.includes('/documents?')) {
      assert.equal(options.body, file);
      assert.equal(new Headers(options.headers).get('Content-Type'), 'application/pdf');
      if (upload) return upload(options);
      return Response.json({ document: { document_version_id: 'doc-1', sha256 } }, { status: 201 });
    }
    if (url.endsWith('/extractions')) {
      assert.equal(options.body, undefined);
      if (admit) return admit(options);
      return Response.json({ context: { run_id: 'run-1' }, execution_status: 'queued' }, { status: 202 });
    }
    if (url === prefix + runPath) return Response.json({ execution_status: statuses.length > 1 ? statuses.shift() : statuses[0], error: { code: 'QWEN_OUTCOME_UNKNOWN' } });
    if (url === prefix + runPath + '/requirements') return Response.json(result);
    throw new Error('unexpected request: ' + url);
  });
  return calls;
}

test('등록·해시 확인·단일 접수·조회 후 다양한 문단을 기존 입력에 반영한다', async t => {
  const calls = setup(t); const updates = []; const attempt = {};
  const response = await extractDocumentWithProgress(file, { projectId: 'project-1', attempt,
    onProgress: update => updates.push(update.percent), waitFor: async () => {} });
  assert.equal(attempt.runId, 'run-1');
  assert.equal(calls.filter(c => c.url.endsWith('/extractions')).length, 1);
  assert.deepEqual(updates, [5, 15, 20, 60, 95]);
  const merged = mergeDocumentRequirements('직접 입력: 유지', response, file.name);
  assert.equal(merged.requirements, '직접 입력: 유지\n\n필수 고지: 무료 관람\n\n문구와 이미지가 잘리지 않아야 합니다.');
  assert.equal(merged.sources[1].type, 'visual');
});

test('접수 응답이 유실되면 같은 키를 재사용하고 성공 뒤에는 조회만 한다', async t => {
  let first = true;
  const calls = setup(t, { statuses: ['waiting_criteria'], admit: () => {
    if (first) { first = false; throw new TypeError('lost reply'); }
    return Response.json({ context: { run_id: 'run-1' } }, { status: 202 });
  } });
  const attempt = {};
  await assert.rejects(extractDocumentWithProgress(file, { projectId: 'p', attempt }));
  await extractDocumentWithProgress(file, { projectId: 'p', attempt });
  await extractDocumentWithProgress(file, { projectId: 'p', attempt });
  const posts = calls.filter(c => c.url.endsWith('/extractions'));
  assert.equal(posts.length, 2);
  assert.equal(posts[0].options.headers['Idempotency-Key'], posts[1].options.headers['Idempotency-Key']);
  assert.equal(calls.filter(c => c.url.includes('/documents?')).length, 1);
});

test('결과 불명은 새 추론으로 자동 재접수하지 않는다', async t => {
  const calls = setup(t, { statuses: ['outcome_unknown'] }); const attempt = {};
  await assert.rejects(extractDocumentWithProgress(file, { projectId: 'p', attempt }), /QWEN_OUTCOME_UNKNOWN/);
  await assert.rejects(extractDocumentWithProgress(file, { projectId: 'p', attempt }), /QWEN_OUTCOME_UNKNOWN/);
  assert.equal(calls.filter(c => c.url.endsWith('/extractions')).length, 1);
});

test('취소 후에는 문단 결과를 반환하지 않고 같은 실행으로 재조회한다', async t => {
  const calls = setup(t, { statuses: ['extracting', 'waiting_criteria'] });
  const controller = new AbortController(); const attempts = new Map();
  await assert.rejects(extractDocumentWithProgress(file, { projectId: 'p', attempts, signal: controller.signal,
    onProgress: update => { if (update.percent === 60) controller.abort(); } }), { name: 'AbortError' });
  await extractDocumentWithProgress(file, { projectId: 'p', attempts });
  assert.equal(calls.filter(c => c.url.endsWith('/extractions')).length, 1);
});

test('원문 해시가 다르면 모델 추출을 접수하지 않는다', async t => {
  const calls = setup(t, { upload: () => Response.json({ document: { document_version_id: 'd', sha256: 'wrong' } }) });
  await assert.rejects(extractDocumentWithProgress(file, { projectId: 'p' }), /원문 저장/);
  assert.equal(calls.filter(c => c.url.endsWith('/extractions')).length, 0);
});

test('프로젝트 목록은 쓰기 역할만 보여주고 페이지를 모두 읽는다', async t => {
  t.mock.method(globalThis, 'fetch', async url => {
    if (url.endsWith('/health')) return Response.json({ requirements_presentation: true, pdf_extraction_mode: 'qwen' });
    return url.includes('cursor=') ? Response.json({ items: [{ id: 'e', my_role: 'editor' }], next_cursor: null })
      : Response.json({ items: [{ id: 'o', my_role: 'owner' }, { id: 'v', my_role: 'viewer' }], next_cursor: 'next' });
  });
  assert.deepEqual((await loadDocumentProjects()).map(p => p.id), ['o', 'e']);
});
