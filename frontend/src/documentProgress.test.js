import assert from 'node:assert/strict';
import test from 'node:test';
import { extractDocumentWithProgress } from './documentProgress.js';

const file = new File(['%PDF-test'], 'test.pdf', { type: 'application/pdf' });
const result = { conditions: [{ text: '시간: 17:30' }], page_count: 1 };
const progress = percent => ({ type: 'progress', stage: 'extracting', percent, message: '페이지 조건 분석 중' });
const encode = value => new TextEncoder().encode(JSON.stringify(value) + '\n');
function respond(t, stream) {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url === '/api/v1/auth/csrf') return Response.json({ csrf_token: 'test-csrf' });
    assert.equal(url, '/api/requirements/extract/stream');
    assert.equal(options.headers['X-CSRFToken'], 'test-csrf');
    assert.equal(new Headers(options.headers).get('X-CSRFToken'), 'test-csrf');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.body.get('document').name, 'test.pdf');
    return new Response(stream, { headers: { 'content-type': 'application/x-ndjson' } });
  });
}

test('한글·JSON 청크 분할과 heartbeat를 처리하고 완료 응답 전에는 결과를 반환하지 않는다', async t => {
  let controller;
  let reached;
  const started = new Promise(resolve => { reached = resolve; });
  const stream = new ReadableStream({ start(value) { controller = value; } });
  respond(t, stream);
  let completed = false;
  const updates = [];
  const running = extractDocumentWithProgress(file, { onProgress: update => { updates.push(update); reached(); } })
    .then(value => { completed = true; return value; });
  const first = encode(progress(50));
  for (let i = 0; i < first.length; i += 3) controller.enqueue(first.slice(i, i + 3));
  controller.enqueue(new TextEncoder().encode('\n'));
  await started;
  assert.equal(completed, false);
  assert.equal(updates[0].message, '페이지 조건 분석 중');
  controller.enqueue(encode(progress(96)));
  controller.enqueue(encode({ type: 'result', result }));
  controller.close();
  assert.deepEqual(await running, result);
  assert.deepEqual(updates.map(u => u.percent), [50, 96]);
});

test('이전 진행률이 늦게 와도 퍼센트가 내려가지 않는다', async t => {
  respond(t, new ReadableStream({ start(c) {
    for (const value of [progress(60), progress(20), { type: 'result', result }]) c.enqueue(encode(value));
    c.close();
  } }));
  const updates = [];
  await extractDocumentWithProgress(file, { onProgress: u => updates.push(u.percent) });
  assert.deepEqual(updates, [60]);
});

for (const [name, payload, message] of [
  ['서버 실패', JSON.stringify({ type: 'error', status: 503, message: '모델을 확인해 주세요.' }), /모델을 확인/],
  ['결과 없는 연결 종료', JSON.stringify(progress(50)), /연결이 중단/],
  ['잘못된 JSON', 'not json', /진행 상태/],
  ['반영 전 100%', JSON.stringify(progress(100)), /진행 상태/],
]) {
  test(`${name}를 성공으로 처리하지 않는다`, async t => {
    respond(t, new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(payload + '\n')); c.close(); } }));
    await assert.rejects(extractDocumentWithProgress(file, {}), message);
  });
}

test('취소한 청크에 완료 응답이 함께 있어도 반영하지 않는다', async t => {
  const abort = new AbortController();
  let cancelled = false;
  respond(t, new ReadableStream({ start(c) {
    c.enqueue(new TextEncoder().encode(JSON.stringify(progress(50)) + '\n' + JSON.stringify({ type: 'result', result }) + '\n'));
  }, cancel() { cancelled = true; } }));
  await assert.rejects(extractDocumentWithProgress(file, { signal: abort.signal, onProgress: () => abort.abort() }), { name: 'AbortError' });
  assert.equal(cancelled, true);
});

test('스트림 시작 전 HTTP 오류도 기존 오류 메시지를 유지한다', async t => {
  t.mock.method(globalThis, 'fetch', async url => url === '/api/v1/auth/csrf' ? Response.json({ csrf_token: 'test-csrf' }) : new Response(JSON.stringify({ detail: '다른 문서를 분석하고 있습니다.' }), {
    status: 429, headers: { 'content-type': 'application/json' },
  }));
  await assert.rejects(extractDocumentWithProgress(file, {}), error => error.status === 429 && /다른 문서/.test(error.message));
});
