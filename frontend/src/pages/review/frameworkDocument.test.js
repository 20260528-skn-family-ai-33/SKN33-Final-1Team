import assert from 'node:assert/strict';
import test from 'node:test';
import { createHash } from 'node:crypto';
import { extractDocumentWithProgress, extractTextWithProgress } from './frameworkDocument.js';
import { loadDocumentProjects, requirePosterReview } from './frameworkClient.js';
import { mergeDocumentRequirements } from './documentRequirements.js';

const file = new File(['%PDF-test'], '전체요구사항.pdf', { type: 'application/pdf' });
const sha256 = createHash('sha256').update('%PDF-test').digest('hex');
const prefix = '/api/framework/v1';
const runPath = '/runs/run-1';
const result = { framework: { run_id: 'run-1', document_version_id: 'doc-1' }, page_count: 2, pending: [], conditions: [
  { text: '필수 고지: 무료 관람', type: 'information', label: '필수 고지', requirement: '무료 관람', inspection: '', level: '필수', page: 1, source: '무료 관람' },
  { text: '문구와 이미지가 잘리지 않아야 합니다.', type: 'visual', label: '잘림', requirement: '문구와 이미지가 잘리지 않아야 합니다.', inspection: '', level: '필수', page: 2, source: '문구와 이미지가 잘리지 않아야 합니다.' },
] };

function setup(t, { statuses = ['queued', 'extracting', 'waiting_criteria'], upload, admit, requirements = result } = {}) {
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
    if (url === prefix + runPath + '/requirements') return Response.json(requirements);
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

for (const current of ['', '  직접 입력: 유지\n']) {
  test(`미확정 항목만 있어도 기존 입력과 실행 ID를 보존한다 (${current ? '기존 입력' : '빈 입력'})`, async t => {
    const pending = [{ criterion_id: 'pending-1', page: 1, source: '행사 날짜는 확인 중입니다.' }];
    const calls = setup(t, { statuses: ['waiting_criteria'], requirements: { ...result, conditions: [], pending } });
    const attempt = {};
    const response = await extractDocumentWithProgress(file, { projectId: 'p', attempt });
    assert.deepEqual(mergeDocumentRequirements(current, response, file.name), { requirements: current, sources: [], addedCount: 0 });
    assert.equal(response.framework.run_id, 'run-1');
    assert.deepEqual(response.pending, pending);
    await extractDocumentWithProgress(file, { projectId: 'p', attempt });
    assert.equal(calls.filter(c => c.url.endsWith('/extractions')).length, 1);
  });
}

for (const pending of [[], 'invalid', null]) {
  test(`검토 항목 없는 응답은 기존 입력으로 반영하지 않는다 (${JSON.stringify(pending)})`, async t => {
    const requirements = { ...result, conditions: [], pending };
    setup(t, { statuses: ['waiting_criteria'], requirements });
    await assert.rejects(extractDocumentWithProgress(file, { projectId: 'p' }), /검토할 요구사항/);
    assert.throws(() => mergeDocumentRequirements('기존 입력', requirements, file.name));
  });
}

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

test('직접 입력은 원문 바이트를 보존하고 같은 접수의 조회를 재사용한다', async t => {
  const text = '  행사명: 봄의 기록\r\n날짜는 담당자 확인 후 확정\n';
  const attempts = new Map(); const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    calls.push({ url, options });
    assert.ok(url.startsWith(prefix));
    if (url.endsWith('/csrf')) return Response.json({ csrf_token: 'text-token' });
    if (options.method === 'POST') {
      assert.equal(new Headers(options.headers).get('X-CSRFToken'), 'text-token');
      assert.ok(new Headers(options.headers).get('Idempotency-Key'));
    }
    if (url.endsWith('/text-documents')) {
      assert.deepEqual(JSON.parse(options.body), { text });
      return Response.json({ document: { document_version_id: 'text-1', sha256: createHash('sha256').update(text).digest('hex') } });
    }
    if (url.endsWith('/extractions')) return Response.json({ context: { run_id: 'text-run' } });
    if (url.endsWith('/requirements')) return Response.json({ conditions: [], pending: [{ criterion_id: 'pending' }],
      framework: { run_id: 'text-run', document_version_id: 'text-1' } });
    if (url.endsWith('/text-run')) return Response.json({ execution_status: 'waiting_criteria' });
    throw new Error('unexpected URL: ' + url);
  });
  const first = await extractTextWithProgress(text, { projectId: 'p', attempts });
  assert.equal(first.framework.run_id, 'text-run');
  await extractTextWithProgress(text, { projectId: 'p', attempts });
  assert.equal(calls.filter(c => c.url.endsWith('/text-documents')).length, 1);
  assert.equal(calls.filter(c => c.url.endsWith('/extractions')).length, 1);
});

test('직접 입력의 저장 해시가 다르면 추출을 접수하지 않는다', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async url => {
    calls.push(url);
    if (url.endsWith('/csrf')) return Response.json({ csrf_token: 'text-token' });
    return Response.json({ document: { document_version_id: 'wrong', sha256: '0'.repeat(64) } });
  });
  await assert.rejects(extractTextWithProgress('행사명: 봄', { projectId: 'p' }), /원문 저장 결과/);
  assert.equal(calls.some(url => url.endsWith('/extractions')), false);
  assert.throws(() => extractTextWithProgress('   ', { projectId: 'p' }), /공백/);
});

test('PDF 분석 가능 여부를 포스터 검수 준비나 텍스트 등록 준비로 간주하지 않는다', async t => {
  let health = { requirements_presentation: true, pdf_extraction_mode: 'qwen', poster_reviews: true };
  t.mock.method(globalThis, 'fetch', async url => {
    assert.equal(url, prefix + '/health');
    return Response.json(health);
  });
  await assert.rejects(requirePosterReview(), /포스터 검사·보고/);
  health.poster_review_ready = true;
  await requirePosterReview();
  await assert.rejects(requirePosterReview({ freeText: true }), /텍스트 원문 저장/);
  health.free_text_registration = true;
  await requirePosterReview({ freeText: true });
});
