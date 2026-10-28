import test from 'node:test';
import assert from 'node:assert/strict';
import {
  CONTRACT, FILE_TYPES, FILE_LIMITS, STAGES, parseModel, parseEndpoint, initialFlow, SIGLIP2, modelOptions, parseStageModel, preparationLabel,
  formErrors, fileError, makeManifest, makeFormData, validateHealth, validateRun, podRequest, sshRequest, modeltestRequest,
} from './modeltest.js';
import { boxStyle, decisionLabel, frameLabel, previewURL, elapsedSeconds, formatDuration, modelTimings } from './modeltestResults.js';

test('timings aggregate by model and revision, include active work, and preserve missing measurements', () => {
  const timing = { model_id: 'vismatch/xfeat', revision: 'v1', load_seconds: 2, inference_seconds: 3, calls: 1 };
  const rows = modelTimings([
    { status: 'completed', model_timings: [timing] },
    { status: 'running', model_timings: [{ ...timing, load_seconds: 0, active_phase: 'inference', active_started_at: 17 },
      { ...timing, model_id: SIGLIP2 }, { ...timing, revision: 'v2' }, { ...timing, calls: -1 }, null] },
    { status: 'completed', elapsed_seconds: 12, model_id: 'old/unmeasured' },
  ], 20000);
  assert.equal(rows.length, 3);
  assert.deepEqual([rows[0].load_seconds, rows[0].inference_seconds, rows[0].calls], [2, 9, 2]);
  assert.equal(rows[0].running, true);
  assert.equal(elapsedSeconds({ status: 'running', started_at: 10, elapsed_seconds: 2 }, 20000), 10);
  assert.equal(elapsedSeconds({ status: 'failed', started_at: 10, elapsed_seconds: 3 }, 20000), 3);
  assert.equal(elapsedSeconds({ status: 'completed' }), null);
  assert.equal(formatDuration(null), '미측정');
  assert.equal(formatDuration(-1), '미측정');
  assert.equal(formatDuration(0.002), '0.002초');
  assert.equal(formatDuration(59.99), '1분 0.0초');
});

const health = {
  contract: CONTRACT, ready: true, stages: STAGES.map(stage => stage.id), formats: FILE_TYPES,
  limits: { ...Object.fromEntries(Object.entries(FILE_LIMITS).map(([kind, value]) => [`${kind}_bytes`, value])), total_bytes: 600 * 1024 ** 2 },
};
function fixture(flow) {
  const state = initialFlow(flow);
  state.media = new File(['media'], flow === 'image' ? 'poster.png' : 'video.mp4', { type: flow === 'image' ? 'image/png' : 'video/mp4' });
  if (flow === 'image') state.documents = [new File(['document'], 'contract.pdf', { type: 'application/pdf' })];
  Object.values(state.stages).forEach(stage => { stage.model = 'Qwen/Qwen3.5-4B'; });
  return state;
}

test('owner/model and HF repository links resolve without guessing an owner', () => {
  for (const input of [' Qwen/Qwen3.5-4B ', 'https://huggingface.co/Qwen/Qwen3.5-4B/', 'https://hf.co/Qwen/Qwen3.5-4B?not-for-all-audiences=true']) {
    assert.deepEqual(parseModel(input), { model_id: 'Qwen/Qwen3.5-4B' });
  }
  assert.deepEqual(parseModel('https://huggingface.co/Qwen/Qwen3.5-4B/tree/abc123'), { model_id: 'Qwen/Qwen3.5-4B', revision: 'abc123' });
  for (const invalid of ['Qwen3.5-4B', 'a/b/c', 'owner/model..name', 'owner/model--name', 'owner/.model', 'owner/model.git', 'datasets/model', 'https://example.com/owner/model', 'https://huggingface.co/spaces/owner/app', 'https://huggingface.co/owner/model/blob/main/config.json', 'https://user:password@huggingface.co/owner/model']) {
    assert.throws(() => parseModel(invalid), Error, invalid);
  }
});

test('known models are offered before connection and reject incompatible stages', () => {
  assert.equal(modelOptions().length, 4);
  for (const [model, stage] of [['google/owlv2-base-patch16', 'text_extract'], [SIGLIP2, 'logo_extract'], ['vismatch/xfeat', 'visual_extract']]) {
    assert.throws(() => parseStageModel(stage, { model }), /단계에서/);
  }
  assert.deepEqual(parseStageModel('logo_compare', { model: 'vismatch/xfeat' }), { model_id: 'vismatch/xfeat' });
  const offered = modelOptions({ models: [{ id: 'Qwen/Qwen3.5-4B', stages: ['conditions'] }, { id: 'owner/custom', stages: ['conditions'] }] });
  assert.equal(offered.length, 5);
  assert.throws(() => parseStageModel('text_extract', { model: 'owner/custom' }, { models: offered }), /단계에서/);
});

test('result coordinates preserve frame scaling and reject unsafe previews', () => {
  assert.equal(preparationLabel({ phase: 'uploading' }), 'Pod에 파일 전송 중');
  assert.equal(preparationLabel({ phase: 'connecting' }), 'SSH 연결 중');
  assert.equal(preparationLabel(), '실행 준비 중');
  assert.deepEqual(boxStyle([8, 4, 40, 20], 80, 40), { left: '10%', top: '10%', width: '40%', height: '40%' });
  assert.equal(boxStyle([8, 4, 100, 20], 80, 40), null);
  assert.equal(boxStyle([8, 4, 8, 20], 80, 40), null);
  assert.equal(frameLabel({ index: 30, timestamp_seconds: 1.25 }), '30 프레임 · 1.25초');
  assert.equal(decisionLabel('candidate_match'), '일치 후보');
  for (const value of [{ mime: 'text/html', data: '/9j/aA==' }, { mime: 'image/jpeg', data: 'https://example.test/private' }]) assert.throws(() => previewURL(value));
  assert.equal(previewURL({ mime: 'image/jpeg', data: '/9j/aA==' }), 'data:image/jpeg;base64,/9j/aA==');
  assert.deepEqual(parseStageModel('logo_compare', { model: 'vismatch/xfeat', imageSimilarity: true }), { model_id: 'vismatch/xfeat', image_similarity: true });
  assert.deepEqual(parseStageModel('logo_compare', { model: 'Qwen/Qwen3.5-4B', imageSimilarity: true }), { model_id: 'Qwen/Qwen3.5-4B' });
});

test('SigLIP2 candidates survive manifest creation and stale labels do not affect another model', () => {
  const state = fixture('video');
  state.stages.visual_extract.model = `https://huggingface.co/${SIGLIP2}/tree/main`;
  assert.ok(formErrors('video', state, health).some(error => error.includes('후보 문구')));
  state.stages.visual_extract.labels = ' a red background \r\n\na blue logo';
  assert.deepEqual(formErrors('video', state, health), []);
  assert.deepEqual(makeManifest('video', state).stages.find(stage => stage.id === 'visual_extract'), {
    id: 'visual_extract', model_id: SIGLIP2, revision: 'main', labels: ['a red background', 'a blue logo'],
  });
  for (const labels of [' ', 'a\n a ', 'a'.repeat(201), Array.from({ length: 33 }, (_, i) => `label ${i}`).join('\n')]) {
    state.stages.visual_extract.labels = labels;
    assert.throws(() => makeManifest('video', state));
  }
  state.stages.visual_extract.model = 'Qwen/Qwen3.5-4B';
  assert.ok(!('labels' in makeManifest('video', state).stages.find(stage => stage.id === 'visual_extract')));
});

test('Pod connection accepts proxy and loopback origins without credentials or paths', () => {
  assert.equal(parseEndpoint(' https://my-pod-8000.proxy.runpod.net/ '), 'https://my-pod-8000.proxy.runpod.net');
  assert.equal(parseEndpoint('http://127.0.0.1:8000'), 'http://127.0.0.1:8000');
  for (const invalid of ['http://pod-8000.proxy.runpod.net', 'https://proxy.runpod.net.evil.test', 'https://runpod.io', 'javascript:alert(1)', 'https://key@pod-8000.proxy.runpod.net', 'https://pod-8000.proxy.runpod.net/api', 'https://pod-8000.proxy.runpod.net?token=secret']) {
    assert.throws(() => parseEndpoint(invalid), Error, invalid);
  }
});

test('image needs both poster and document; video extraction works with video only', () => {
  const image = fixture('image'); assert.deepEqual(formErrors('image', image, health), []);
  image.documents = []; assert.ok(formErrors('image', image, health).some(error => error.includes('문서')));
  const video = fixture('video'); assert.deepEqual(formErrors('video', video, health), []);
  video.media = null; assert.ok(formErrors('video', video, health).some(error => error.includes('영상')));
});

test('comparison requires its extraction, conditions, and logo references', () => {
  const state = fixture('video'); state.stages.logo_compare.enabled = true;
  assert.ok(formErrors('video', state, health).some(error => error.includes('원본 로고')));
  state.references = [new File(['logo'], 'brand.webp')];
  state.stages.text_compare.enabled = true;
  assert.ok(formErrors('video', state, health).some(error => error.includes('직접 입력')));
  state.requirements = '행사명을 표시해야 합니다.';
  assert.deepEqual(formErrors('video', state, health), []);
  state.stages.text_extract.enabled = false;
  assert.ok(formErrors('video', state, health).some(error => error.includes('추출 단계')));
});

test('frame count is integral, seconds can be fractional, maximum frame count is bounded', () => {
  const state = fixture('video'); state.sampling.interval = '1.5';
  assert.ok(formErrors('video', state).length);
  state.sampling.unit = 'seconds'; assert.deepEqual(formErrors('video', state), []);
  for (const interval of ['', '0', '-1', 'Infinity', 'NaN', '3601']) {
    state.sampling.interval = interval; assert.ok(formErrors('video', state).length, interval);
  }
  state.sampling.interval = '1'; state.sampling.max_frames = '1001'; assert.ok(formErrors('video', state).length);
});

test('server capabilities narrow accepted files, stage support and total upload size', () => {
  const state = fixture('image');
  assert.ok(fileError(new File([], 'empty.png'), 'image'));
  assert.ok(fileError(new File(['svg'], 'image.svg'), 'image'));
  assert.ok(fileError({ name: 'large.png', size: FILE_LIMITS.image + 1 }, 'image'));
  const limited = { ...health, formats: { ...FILE_TYPES, image: ['.jpg'] }, stages: [], limits: { ...health.limits, total_bytes: 1 } };
  const errors = formErrors('image', state, limited);
  assert.ok(errors.some(error => error.includes('형식')));
  assert.ok(errors.some(error => error.includes('지원하지')));
  assert.ok(errors.some(error => error.includes('합계')));
});

test('multipart contains the selected models, original files, references and sampling', () => {
  const state = fixture('video'); state.documents = [new File(['a'], 'a.pdf'), new File(['b'], 'b.hwpx')];
  state.references = [new File(['logo'], 'logo.png')]; state.stages.visual_extract.model = 'https://huggingface.co/owner/vision/tree/rev';
  const manifest = makeManifest('video', state); const form = makeFormData(manifest, state);
  assert.equal(manifest.contract, CONTRACT); assert.equal(manifest.stages.length, 3);
  assert.deepEqual(manifest.stages.find(stage => stage.id === 'visual_extract'), { id: 'visual_extract', model_id: 'owner/vision', revision: 'rev' });
  assert.deepEqual(manifest.sampling, { unit: 'frames', interval: 30, max_frames: 120 });
  assert.deepEqual(JSON.parse(form.get('config')), manifest);
  assert.equal(form.get('media').name, 'video.mp4'); assert.equal(form.getAll('documents').length, 2); assert.equal(form.getAll('references').length, 1);
  assert.ok(!('sampling' in makeManifest('image', fixture('image'))));
});

test('an OCR-only health response or incomplete capability declaration cannot enable execution', () => {
  assert.equal(validateHealth(health), health);
  for (const value of [{ ready: true }, { ...health, contract: 'ocr.v1' }, { ...health, limits: {} }, { ...health, formats: {} }]) assert.throws(() => validateHealth(value));
});

test('a complete run must contain every requested stage, including video frames', () => {
  const manifest = makeManifest('video', fixture('video'));
  const run = { contract: CONTRACT, id: 'job-1', status: 'completed', stages: ['frames', ...manifest.stages.map(stage => stage.id)].map(id => ({ id, status: 'completed' })) };
  assert.equal(validateRun(run, 'job-1', manifest), run);
  assert.throws(() => validateRun({ ...run, id: undefined }));
  assert.throws(() => validateRun(run, 'wrong-id', manifest));
  assert.throws(() => validateRun({ ...run, stages: run.stages.slice(1) }, 'job-1', manifest));
  assert.throws(() => validateRun({ ...run, stages: [...run.stages, run.stages[0]] }));
  assert.throws(() => validateRun({ ...run, stages: run.stages.map(stage => ({ ...stage, status: 'running' })) }, 'job-1', manifest));
});

test('request transport uses the supplied Pod only, omits cookies and preserves an idempotency key', async t => {
  const calls = []; const run = { contract: CONTRACT, id: 'job-1', status: 'queued', stages: [] };
  t.mock.method(globalThis, 'fetch', async (url, options) => { calls.push({ url, options }); return Response.json(run); });
  const state = fixture('image'); const body = makeFormData(makeManifest('image', state), state);
  for (let n = 0; n < 2; n++) await podRequest('https://pod-8000.proxy.runpod.net', '/runs', { method: 'POST', body, token: 'test-only-token', headers: { 'Idempotency-Key': 'same-request' } });
  assert.equal(calls.length, 2);
  for (const call of calls) {
    assert.equal(call.url, 'https://pod-8000.proxy.runpod.net/api/modeltest/runs'); assert.equal(call.options.body, body);
    assert.equal(call.options.credentials, 'omit'); assert.equal(call.options.redirect, 'error');
    assert.equal(call.options.headers['Idempotency-Key'], 'same-request'); assert.equal(call.options.headers.Authorization, 'Bearer test-only-token');
    assert.equal(call.options.headers['Content-Type'], undefined);
  }
});

test('network/HTTP/protocol failures are surfaced without automatic resubmission or secret echo', async t => {
  const mocked = t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('private transport detail'); });
  await assert.rejects(podRequest('http://localhost:8000', '/runs', { method: 'POST' }), /연결하지 못했습니다/);
  assert.equal(mocked.mock.callCount(), 1);
  mocked.mock.mockImplementation(async () => Response.json({ detail: 'secret-path-token' }, { status: 401 }));
  await assert.rejects(podRequest('http://localhost:8000', '/health'), error => error.status === 401 && !error.message.includes('secret'));
  mocked.mock.mockImplementation(async () => new Response('<html>not an API</html>'));
  await assert.rejects(podRequest('http://localhost:8000', '/health'), /JSON/);
});

test('aborted requests preserve the cancellation signal', async t => {
  const controller = new AbortController(); controller.abort();
  t.mock.method(globalThis, 'fetch', async (_url, options) => { options.signal.throwIfAborted(); });
  await assert.rejects(podRequest('http://localhost:8000', '/health', { signal: controller.signal }), error => error.name === 'AbortError');
});

test('SSH requests stay on the local backend and preserve the multipart request key', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => { calls.push({ url, options }); return Response.json({ ok: true }); });
  const command = 'ssh root@203.0.113.10 -p 12345';
  await sshRequest('/api/modeltest/ssh/connections', { method: 'POST', body: JSON.stringify({ command }), headers: { 'Content-Type': 'application/json' } });
  const body = makeFormData(makeManifest('image', fixture('image')), fixture('image'));
  await modeltestRequest({ transport: 'ssh', endpoint: '/api/modeltest/ssh/connections/test-id' }, '/runs', { method: 'POST', body, headers: { 'Idempotency-Key': 'request-1' } });
  assert.equal(calls[0].options.headers['X-Modeltest-Local'], '1'); assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(calls[1].url, '/api/modeltest/ssh/connections/test-id/runs'); assert.equal(calls[1].options.body, body);
  assert.equal(calls[1].options.headers['Idempotency-Key'], 'request-1'); assert.equal(calls[1].options.headers['Content-Type'], undefined);
  await assert.rejects(sshRequest('https://example.com/connections'), /경로/);
  await assert.rejects(sshRequest('/api/modeltest/ssh/connections/../../secrets'), /경로/);
  assert.equal(calls.length, 2);
});

test('local SSH failures keep actionable messages without silently using an HTTP Pod endpoint', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: 'SSH 키 인증에 실패했습니다.' }, { status: 400 }));
  await assert.rejects(sshRequest('/api/modeltest/ssh/connections'), error => error.status === 400 && error.message.includes('키 인증'));
});
