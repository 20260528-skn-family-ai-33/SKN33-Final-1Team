import test from 'node:test';
import assert from 'node:assert/strict';
import {
  CONTRACT, FILE_TYPES, FILE_LIMITS, STAGES, parseModel, parseEndpoint, initialFlow, SIGLIP2, SIGLIP2_SO400M, FILTER_SCOPES, modelOptions, parseStageModel, preparationLabel,
  formErrors, fileError, makeManifest, makeFormData, validateHealth, validateRun, podRequest, sshRequest, modeltestRequest, workflowPreset, workflowError,
} from './modeltest.js';
import { agentTemplate, compileAgents, newAgent, newAgentStep } from './modeltestAgents.js';
import { containedPoint, boxStyle, decisionLabel, frameLabel, previewURL, elapsedSeconds, formatDuration, modelTimings, preferredReference, latestCompletedStage, nextSimilaritySort, similarityRows } from './modeltestResults.js';
import { modelChoice } from './modeltestCatalog.js';
import { readSSH, writeSSH, connectionTarget } from './modeltestConnection.js';
import { promptSpec, promptText, promptFields } from './modeltestPrompts.js';

test('similarity sorting cycles ascending, descending and attachment order independently per column', () => {
  for (const key of ['cosine', 'descriptor', 'decision']) {
    const ascending = nextSimilaritySort(null, key);
    assert.deepEqual(ascending, { key, direction: 'ascending' });
    const descending = nextSimilaritySort(ascending, key);
    assert.deepEqual(descending, { key, direction: 'descending' });
    assert.equal(nextSimilaritySort(descending, key), null);
  }
  assert.deepEqual(nextSimilaritySort({ key: 'cosine', direction: 'descending' }, 'descriptor'), { key: 'descriptor', direction: 'ascending' });
});

function similarityFixture() {
  return {
    files: Array.from({ length: 6 }, () => ({ name: 'same-name.png' })),
    cosine: new Map([0, .8, null, -.2, .8].map((image_cosine_similarity, index) => [index, { image_cosine_similarity }])),
    matches: new Map([.7, .1, null, -.1, .7].map((descriptor_similarity, index) => [index, { descriptor_similarity, decision: ['uncertain', 'candidate_match', 'uncertain', 'mismatch', 'candidate_match'][index] }])),
  };
}

test('similarity sorting keeps stable identities and ties, with missing scores last in either direction', () => {
  const { files, cosine, matches } = similarityFixture(); const before = structuredClone({ files, cosine, matches });
  const order = (key, direction) => similarityRows(files, cosine, matches, key ? { key, direction } : null).rows.map(row => row.index);
  assert.deepEqual(order('cosine', 'ascending'), [3, 0, 1, 4, 2, 5]);
  assert.deepEqual(order('cosine', 'descending'), [1, 4, 0, 3, 2, 5]);
  assert.deepEqual(order('descriptor', 'ascending'), [3, 1, 0, 4, 2, 5]);
  assert.deepEqual(order('descriptor', 'descending'), [0, 4, 1, 3, 2, 5]);
  assert.deepEqual(order('decision', 'ascending'), [3, 1, 4, 0, 2, 5]);
  assert.deepEqual(order('decision', 'descending'), [0, 2, 1, 4, 3, 5]);
  assert.deepEqual(order(), [0, 1, 2, 3, 4, 5]);
  assert.deepEqual({ files, cosine, matches }, before);
  assert.equal(similarityRows(files, cosine, matches).rows[4].file, files[4]);
});

test('similarity filters combine inclusive numeric ranges and decisions without treating missing scores as zero', () => {
  const { files, cosine, matches } = similarityFixture();
  const filter = value => similarityRows(files, cosine, matches, null, value);
  const indices = value => filter(value).rows.map(row => row.index);
  assert.deepEqual(indices({ cosine: { min: '-1', max: '0' } }), [0, 3]);
  assert.deepEqual(indices({ cosine: { min: '.8', max: '.8' }, descriptor: { max: '.5' }, decision: '일치 후보' }), [1]);
  assert.deepEqual(indices({ descriptor: { min: '.7', max: '1' }, decision: '판단 보류' }), [0]);
  assert.deepEqual(indices({ decision: '미계산' }), [5]);
  assert.deepEqual(indices({ descriptor: { min: '-1' }, decision: '미계산' }), []);
  assert.deepEqual(indices({ cosine: { min: '', max: ' ' }, descriptor: {} }), [0, 1, 2, 3, 4, 5]);
  assert.deepEqual(filter({ cosine: { min: '1', max: '-1' } }).rows, []);
  assert.match(filter({ cosine: { min: '1', max: '-1' } }).error, /최솟값은 최댓값보다/);
  for (const value of ['-1.1', '1.1', 'NaN', 'Infinity', 'invalid']) {
    assert.match(filter({ descriptor: { min: value } }).error, /-1~1/);
  }
  assert.ok(filter({ decision: '미계산' }).decisions.includes('일치 후보'));
  assert.deepEqual(similarityRows([], new Map(), new Map(), null, { decision: '미계산' }).decisions, ['미계산'], 'Active decision stays selectable when streaming results change');
  assert.deepEqual(similarityRows([], new Map(), new Map()).rows, []);
});

test('image similarity sends one base and up to one hundred candidates with explicit resize and both models', () => {
  const state = initialFlow('similarity');
  state.media = new File(['base'], 'base.png', { type: 'image/png' });
  state.references = Array.from({ length: 100 }, (_, i) => new File(['candidate'], `candidate-${i}.png`, { type: 'image/png' }));
  state.resize = { width: '100', height: '50' };
  assert.deepEqual(formErrors('similarity', state), []);
  const manifest = makeManifest('similarity', state);
  assert.deepEqual(manifest.resize, { width: 100, height: 50 });
  assert.deepEqual(manifest.stages.map(stage => [stage.id, stage.model_id]), [['logo_similarity', SIGLIP2], ['logo_compare', 'vismatch/xfeat']]);
  assert.deepEqual(manifest.files.documents, []);
  const body = makeFormData(manifest, state);
  assert.equal(body.getAll('media').length, 1); assert.equal(body.getAll('references').length, 100);
  state.references.push(state.references[0]); assert.ok(formErrors('similarity', state).some(error => error.includes('1~100'))); state.references.pop();
  for (const resize of [{ width: '100', height: '' }, { width: '0', height: '50' }, { width: '1.5', height: '50' }, { width: '8193', height: '50' }]) {
    state.resize = resize; assert.ok(formErrors('similarity', state).some(error => error.includes('다운사이징')));
  }
  state.resize = { width: '', height: '' }; assert.equal(makeManifest('similarity', state).resize, null);
  const health = { stages: ['logo_similarity', 'logo_compare'], formats: FILE_TYPES, limits: { total_bytes: 600 * 1024 ** 2 } };
  assert.ok(formErrors('similarity', state, health).some(error => error.includes('현재 연결')));
  assert.deepEqual(formErrors('similarity', state, { ...health, similarity_test: 1 }), []);
  state.references = []; assert.ok(formErrors('similarity', state).some(error => error.includes('1~100')));
});

test('percentage resize accepts decimals, excludes inactive pixel fields and checks runner support', () => {
  const state = initialFlow('similarity'); state.media = new File(['base'], 'base.png'); state.references = [new File(['candidate'], 'candidate.png')];
  state.resize = { mode: 'percent', percent: '12.5', width: '100', height: '50' };
  assert.deepEqual(formErrors('similarity', state), []);
  assert.deepEqual(makeManifest('similarity', state).resize, { percent: 12.5 });
  for (const percent of ['0', '-1', '100.1', 'NaN', 'Infinity', '0.001']) {
    state.resize.percent = percent; assert.ok(formErrors('similarity', state).some(error => error.includes('다운사이징 비율')));
  }
  state.resize.percent = '50';
  const capable = { stages: ['logo_similarity', 'logo_compare'], formats: FILE_TYPES, limits: { total_bytes: 600 * 1024 ** 2 }, similarity_test: 1 };
  assert.ok(formErrors('similarity', state, capable).some(error => error.includes('최신 백엔드')));
  assert.deepEqual(formErrors('similarity', state, { ...capable, similarity_resize_percent: true }), []);
  state.resize.percent = ''; assert.equal(makeManifest('similarity', state).resize, null);
  state.resize.mode = 'pixels'; assert.deepEqual(makeManifest('similarity', state).resize, { width: 100, height: 50 });
});

test('candidate resize is independent, validated and requires explicit runner support', () => {
  const state = initialFlow('similarity'); state.media = new File(['base'], 'base.png'); state.references = [new File(['candidate'], 'candidate.png')];
  state.resize = { mode: 'percent', percent: '50', width: '', height: '' };
  assert.equal(makeManifest('similarity', state).reference_resize, null);
  state.referenceResize = { mode: 'pixels', width: '20', height: '10', percent: '12.5' };
  assert.deepEqual(formErrors('similarity', state), []);
  assert.deepEqual(makeManifest('similarity', state).reference_resize, { width: 20, height: 10 });
  assert.deepEqual(makeManifest('similarity', state).resize, { percent: 50 });
  state.referenceResize.mode = 'percent';
  assert.deepEqual(makeManifest('similarity', state).reference_resize, { percent: 12.5 });
  const health = { stages: ['logo_similarity', 'logo_compare'], formats: FILE_TYPES, limits: { total_bytes: 600 * 1024 ** 2 }, similarity_test: 1, similarity_resize_percent: true };
  assert.ok(formErrors('similarity', state, health).some(error => error.includes('대조 이미지 다운사이징을 지원')));
  assert.deepEqual(formErrors('similarity', state, { ...health, similarity_reference_resize: true }), []);
  for (const bad of [{ mode: 'percent', percent: '0' }, { mode: 'percent', percent: '101' }, { width: '20', height: '' }, { width: '-1', height: '10' }]) {
    state.referenceResize = bad; assert.ok(formErrors('similarity', state).some(error => error.includes('대조 이미지 다운사이징')));
  }
  delete state.referenceResize; assert.equal(makeManifest('similarity', state).reference_resize, null);
});

test('teammate models and explicit candidate scopes reach the manifest without replacing defaults', () => {
  const detection = parseStageModel('logo_extract', { model: 'https://huggingface.co/google/owlv2-base-patch16-ensemble' });
  assert.equal(detection.model_id, 'google/owlv2-base-patch16-ensemble');
  const state = initialFlow('similarity'); state.stages.logo_similarity.model = `https://huggingface.co/${SIGLIP2_SO400M}/tree/release`;
  state.media = new File(['base'], 'base.png'); state.references = [new File(['candidate'], 'candidate.png')];
  assert.deepEqual(makeManifest('similarity', state).stages[0], { id: 'logo_similarity', name: '이미지 코사인 유사도', model_id: SIGLIP2_SO400M, revision: 'release' });
  assert.deepEqual(parseStageModel('logo_compare', { model: 'vismatch/xfeat', imageSimilarity: true, imageSimilarityModel: SIGLIP2_SO400M }), { model_id: 'vismatch/xfeat', image_similarity: true, image_similarity_model: SIGLIP2_SO400M });
  for (const scope of Object.keys(FILTER_SCOPES)) {
    const result = parseStageModel('candidate_filter', { filterScope: scope, topK: '2', threshold: '' });
    assert.equal(result.scope || 'references_per_crop', scope); assert.equal(result.top_k, 2);
  }
  assert.throws(() => parseStageModel('candidate_filter', { filterScope: 'crops_per_reference_video', topK: '2' }, { ready: true }), /다시 연결/);
  const video = initialFlow('video'); video.stages.logo_similarity.model = SIGLIP2_SO400M;
  assert.equal(workflowPreset('video', video, 'filtered').stages.logo_similarity.model, SIGLIP2_SO400M);
  assert.equal(initialFlow('similarity').stages.logo_similarity.model, SIGLIP2);
  const registered = { id: 'so', model_id: SIGLIP2_SO400M, resolved_revision: 'a'.repeat(40), status: 'unverified', tasks: ['logo_similarity'], fingerprint: 'so-space' };
  state.stages.logo_similarity.model = 'registered:so';
  assert.equal(makeManifest('similarity', state, { registered: [registered] }).stages[0].registered_model, 'so');
});

test('feature points account for resized inputs, preview pixels and contain letterboxing', () => {
  const box = { x: 10, y: 20, width: 300, height: 200 };
  assert.deepEqual(containedPoint([50, 25], [100, 50], box, [1000, 500]), { x: 160, y: 120 });
  assert.deepEqual(containedPoint([0, 0], [100, 50], box, [1000, 500]), { x: 10, y: 45 });
  assert.deepEqual(containedPoint([25, 50], [50, 100], box, [250, 500]), { x: 160, y: 120 });
  for (const point of [[-1, 0], [101, 0], [NaN, 0], null]) assert.equal(containedPoint(point, [100, 50], box, [1000, 500]), null);
  assert.equal(containedPoint([0, 0], [0, 50], box, [1000, 500]), null);
});

test('latest completed stage follows finish time across parallel and reordered workflows', () => {
  const slow = { id: 'slow', status: 'completed', started_at: 100, elapsed_seconds: 30 };
  const quick = { id: 'quick', status: 'completed', started_at: 110, elapsed_seconds: 5 };
  const running = { id: 'running', status: 'running', started_at: 150, elapsed_seconds: 10 };
  const failed = { ...running, id: 'failed', status: 'failed' };
  const stages = [slow, quick, running, failed];
  assert.equal(latestCompletedStage(stages), slow);
  assert.equal(latestCompletedStage([quick, slow]), slow);
  assert.deepEqual(stages.map(stage => stage.id), ['slow', 'quick', 'running', 'failed']);
  const zero = { ...quick, id: 'zero', started_at: 140, elapsed_seconds: 0 };
  assert.equal(latestCompletedStage([zero, slow]), zero);
  const tied = { ...quick, id: 'tied', started_at: 120, elapsed_seconds: 10 };
  assert.equal(latestCompletedStage([slow, tied]), tied);
  const missing = { id: 'missing', status: 'completed' };
  const invalid = { ...missing, id: 'invalid', started_at: 500, elapsed_seconds: -1 };
  assert.equal(latestCompletedStage([slow, missing, invalid]), slow);
  assert.equal(latestCompletedStage([missing, invalid]), invalid, 'Legacy untimed responses retain their previous fallback');
  assert.equal(latestCompletedStage([running, failed]), undefined);
  assert.equal(latestCompletedStage([]), undefined);
});

test('reference defaults use the highest image similarity and preserve manual choices', () => {
  const matches = [
    { reference_index: 0, image_cosine_similarity: -0.4, descriptor_similarity: 0.99 },
    { reference_index: 2, image_cosine_similarity: -0.1 },
    { reference_index: 1, image_cosine_similarity: -0.1 },
    { reference_index: 9, image_cosine_similarity: 1 },
    { reference_index: 3, image_cosine_similarity: NaN },
  ];
  assert.equal(preferredReference(matches, 4), 1);
  assert.equal(preferredReference(matches, 4, 0), 0);
  assert.equal(preferredReference(matches, 4, 99), 1);
  assert.equal(matches[0].reference_index, 0, 'Do not reorder the result payload');
  assert.equal(preferredReference([{ reference_index: 2, descriptor_similarity: 0.5 }], 4), 2);
  assert.equal(preferredReference([], 4), 0);
  assert.equal(preferredReference([], 0), 0);
});

test('review prompt defaults, replacements and legacy instructions follow the selected model', () => {
  const qwen = 'Qwen/Qwen3.5-4B'; const spec = promptSpec('text_extract', qwen);
  assert.match(promptText({}, spec), /Transcribe all visible text/);
  assert.equal(promptSpec('logo_compare', 'vismatch/xfeat'), null);
  assert.equal(promptSpec('text_extract', 'PaddlePaddle/PaddleOCR-VL-1.6-GGUF').default, 'OCR:');
  assert.equal(promptSpec('logo_extract', 'registered-id', { family: 'owlv2', options: { labels: ['custom logo'] } }).default, 'custom logo');
  assert.deepEqual(promptFields('text_extract', { promptOverride: 'custom' }, qwen), { prompt_override: 'custom' });
  assert.deepEqual(promptFields('text_extract', { prompt: 'legacy' }, qwen), { prompt: 'legacy' });
  assert.equal(promptText({ promptOverride: '', prompt: 'legacy' }, spec), '');
  assert.throws(() => promptFields('text_extract', { promptOverride: 'x'.repeat(8001) }, qwen), /8,000/);
  assert.throws(() => promptFields('logo_extract', { promptOverride: 'logo\nlogo' }, 'google/owlv2-base-patch16'), /중복/);
  assert.throws(() => parseStageModel('text_extract', { model: qwen, promptOverride: 'custom' }, { ready: true }), /프롬프트 교체/);
  assert.equal(parseStageModel('text_extract', { model: qwen, promptOverride: 'custom' }, { ready: true, prompt_overrides: 1 }).prompt_override, 'custom');
  const state = initialFlow('image'); state.media = { name: 'image.png' };
  for (const value of Object.values(state.stages)) value.enabled = false;
  state.stages.text_extract = { enabled: true, model: qwen, promptOverride: 'replacement' };
  assert.equal(makeManifest('image', state).stages[0].prompt_override, 'replacement');
  assert.equal(modelChoice('vismatch/xfeat').promptOverride, null);
});

test('SSH preferences retain only the command and a local session endpoint', () => {
  let value; const storage = { getItem: () => value, setItem: (_, next) => { value = next; } };
  writeSSH({ command: 'ssh root@203.0.113.10 -i ~/.ssh/id_ed25519', endpoint: '/api/modeltest/ssh/connections/fixture', token: 'secret', health: { ready: true } }, storage);
  assert.deepEqual(readSSH(storage), { command: 'ssh root@203.0.113.10 -i ~/.ssh/id_ed25519', endpoint: '/api/modeltest/ssh/connections/fixture' });
  assert.equal(value.includes('secret'), false); assert.equal(value.includes('ready'), false);
  writeSSH({ command: '', endpoint: 'https://example.com/health' }, storage);
  assert.deepEqual(readSSH(storage), { command: '', endpoint: '' });
  assert.deepEqual(readSSH({ getItem: () => { throw new Error('blocked'); } }), { command: '', endpoint: '' });
});

test('registered profiles enforce task, version, stale settings and prepare without files', () => {
  const model = { id: 'fixture', model_id: 'google/siglip2-base-patch16-224', resolved_revision: 'a'.repeat(40), fingerprint: 'one', status: 'unverified', tasks: ['image_embed'], name: 'SigLIP', family: 'clip', reasons: [] };
  const capable = { registered: [model], ready: true, registry_version: 1 };
  const value = modelChoice('registered:fixture', capable);
  assert.equal(parseStageModel('image_embed', value, capable).registered_model, model.id);
  assert.throws(() => parseStageModel('text_embed', value, capable), /지원하지/);
  assert.throws(() => parseStageModel('image_embed', value, { ...capable, registered: [{ ...model, fingerprint: 'two' }] }), /설정이 바뀌/);
  assert.throws(() => parseStageModel('image_embed', value, { ...capable, registered: [] }), /삭제/);
  assert.throws(() => parseStageModel('text_generate', { model: 'unknown/generation' }), /먼저 등록/);
  const manifest = { contract: CONTRACT, flow: 'video', purpose: 'prepare', stages: [{ id: 'model_prepare' }] };
  assert.equal(makeFormData(manifest, { media: null, documents: [], references: [] }).has('media'), false);
  assert.equal(validateRun({ contract: CONTRACT, id: 'prepared', status: 'completed', stages: [{ id: 'model_prepare', status: 'completed' }] }, 'prepared', manifest).status, 'completed');
});

test('agent graph checks vector identity and output type before submission', () => {
  const state = fixture('image'); const agent = newAgent();
  const models = ['one', 'two'].map(id => ({ id, model_id: `owner/${id}`, resolved_revision: 'a'.repeat(40), fingerprint: id, status: 'unverified', tasks: ['image_embed'], name: id, family: 'clip', reasons: [] }));
  const capable = { ...health, workflow: { agents: true }, registry_version: 1, registered: models };
  const first = { ...newAgentStep('image_embed'), ...modelChoice('registered:one', capable) };
  const second = { ...newAgentStep('image_embed'), ...modelChoice('registered:one', capable) };
  const compare = { ...newAgentStep('vector_compare'), bindings: { left: first.id, right: second.id } };
  agent.nodes = [first, second, compare]; agent.edges = [first, second].map(node => ({ source: node.id, target: compare.id }));
  state.agentWorkflow = { enabled: true, agents: [agent], edges: [], positions: {}, maxParallel: 1 };
  assert.equal(compileAgents('image', state, capable).stages[2].inputs.left, first.id);
  second.model = 'registered:two'; second.modelFingerprint = 'two';
  assert.throws(() => compileAgents('image', state, capable), /같은 모델/);
  second.kind = 'text_input'; second.paramsText = '{"documents":["sample"]}';
  assert.throws(() => compileAgents('image', state, capable), /선행 연결/);
});

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
  assert.equal(modelOptions().length, 7);
  for (const [model, stage] of [['google/owlv2-base-patch16', 'text_extract'], [SIGLIP2, 'logo_extract'], ['vismatch/xfeat', 'visual_extract']]) {
    assert.throws(() => parseStageModel(stage, { model }), /단계에서/);
  }
  assert.deepEqual(parseStageModel('logo_compare', { model: 'vismatch/xfeat' }), { model_id: 'vismatch/xfeat' });
  const offered = modelOptions({ models: [{ id: 'Qwen/Qwen3.5-4B', stages: ['conditions'] }, { id: 'owner/custom', stages: ['conditions'] }] });
  assert.equal(offered.length, 8);
  assert.throws(() => parseStageModel('text_extract', { model: 'owner/custom' }, { models: offered }), /단계에서/);
});

test('PaddleOCR GGUF is available only for OCR and accepts the official repository URL', () => {
  const id = 'PaddlePaddle/PaddleOCR-VL-1.6-GGUF';
  assert.deepEqual(parseStageModel('text_extract', { model: `https://huggingface.co/${id}` }), { model_id: id });
  assert.throws(() => parseStageModel('conditions', { model: id }), /단계에서/);
});

test('workflow presets compile filtering and parallel branches, rejecting cycles and missing dependencies', () => {
  const state = fixture('video'); state.references = [new File(['logo'], 'logo.png')];
  state.stages.logo_similarity.model = SIGLIP2_SO400M;
  const filtered = workflowPreset('video', state, 'filtered');
  const capable = { ...health, workflow: { version: 1, max_parallel: 2, agents: true } };
  assert.deepEqual(formErrors('video', filtered, capable), []);
  assert.equal(makeManifest('video', filtered).stages.find(stage => stage.id === 'candidate_filter').top_k, 2);
  filtered.stages.candidate_filter.threshold = '0.85';
  assert.equal(makeManifest('video', filtered).stages.find(stage => stage.id === 'candidate_filter').threshold, 0.85);
  filtered.workflow.edges.push({ source: 'logo_compare', target: 'logo_extract' });
  assert.match(workflowError('video', filtered), /순환/);
  const parallel = workflowPreset('video', state, 'parallel');
  assert.equal(workflowError('video', parallel), '');
  assert.equal(parallel.workflow.maxParallel, 2);
  for (const target of ['logo_compare', 'logo_similarity']) assert.ok(parallel.workflow.edges.some(edge => edge.source === 'logo_extract' && edge.target === target));
  parallel.workflow.edges = parallel.workflow.edges.filter(edge => edge.target !== 'logo_compare');
  assert.match(workflowError('video', parallel), /연결이 필요/);
});

test('agent templates bypass disabled agents, bind inputs and preserve agent metadata in manifests', () => {
  for (const flow of ['image', 'video']) {
    const state = fixture(flow); state.agentWorkflow = agentTemplate(flow);
    state.documents = [new File(['contract'], 'contract.txt')];
    if (flow === 'video') state.references = [new File(['logo'], 'logo.png')];
    const plan = compileAgents(flow, state, { ...health, workflow: { agents: true } });
    assert.equal(plan.workflow.agents.length, 6);
    const conditions = plan.stages.find(stage => stage.kind === 'conditions');
    const text = plan.stages.find(stage => stage.kind === 'text_extract');
    const comparison = plan.stages.find(stage => stage.kind === 'text_compare');
    assert.equal(comparison.inputs.conditions, conditions.id);
    assert.equal(comparison.inputs.text_extract, text.id);
    assert.ok(plan.workflow.edges.some(edge => edge.source === conditions.id && edge.target === text.id));
    assert.deepEqual(makeManifest(flow, state).stages, plan.stages);
    assert.throws(() => compileAgents(flow, state, health), /지원하지 않습니다/);
    assert.throws(() => compileAgents(flow, state, { ...health, stages: [], workflow: { agents: true } }), /지원하지 않습니다/);
  }
});

test('multiple outputs of one kind require explicit ancestor input selection', () => {
  const state = fixture('video'); state.requirements = '행사명 필수';
  const agent = newAgent('문구 비교'); const first = newAgentStep('text_extract'); const second = newAgentStep('text_extract'); const compare = newAgentStep('text_compare');
  agent.nodes = [first, second, compare]; agent.edges = [first, second].map(node => ({ source: node.id, target: compare.id }));
  state.agentWorkflow = { enabled: true, agents: [agent], edges: [], positions: {}, maxParallel: 2 };
  assert.throws(() => compileAgents('video', state), /입력이 여러 개/);
  compare.bindings.text_extract = second.id;
  assert.equal(compileAgents('video', state).stages.at(-1).inputs.text_extract, second.id);
  agent.edges = agent.edges.filter(edge => edge.source !== second.id);
  assert.throws(() => compileAgents('video', state), /선행 연결/);
  agent.edges.push({ source: compare.id, target: first.id });
  assert.throws(() => compileAgents('video', state), /순환/);
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
  state.references = [new File(['logo'], 'logo.png')]; state.stages.visual_extract.model = 'https://huggingface.co/Qwen/Qwen3.5-4B/tree/rev';
  const manifest = makeManifest('video', state); const form = makeFormData(manifest, state);
  assert.equal(manifest.contract, CONTRACT); assert.equal(manifest.stages.length, 3);
  assert.deepEqual(manifest.stages.find(stage => stage.id === 'visual_extract'), { id: 'visual_extract', model_id: 'Qwen/Qwen3.5-4B', revision: 'rev' });
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
  t.mock.method(globalThis, 'fetch', async (url, options) => { calls.push({ url, options }); return Response.json(url === '/api/v1/auth/csrf' ? { csrf_token: 'test-session-token' } : { ok: true }); });
  const command = 'ssh root@203.0.113.10 -p 12345';
  await sshRequest('/api/modeltest/ssh/connections', { method: 'POST', body: JSON.stringify({ command }), headers: { 'Content-Type': 'application/json' } });
  const body = makeFormData(makeManifest('image', fixture('image')), fixture('image'));
  await modeltestRequest({ transport: 'ssh', endpoint: '/api/modeltest/ssh/connections/test-id' }, '/runs', { method: 'POST', body, headers: { 'Idempotency-Key': 'request-1' } });
  assert.equal(calls[0].url, '/api/v1/auth/csrf');
  assert.equal(calls[1].options.headers['X-Modeltest-Local'], '1'); assert.equal(calls[1].options.credentials, 'same-origin');
  assert.equal(calls[1].options.headers['X-CSRFToken'], 'test-session-token');
  assert.equal(calls[2].url, '/api/v1/auth/csrf');
  assert.equal(calls[3].url, '/api/modeltest/ssh/connections/test-id/runs'); assert.equal(calls[3].options.body, body);
  assert.equal(calls[3].options.headers['Idempotency-Key'], 'request-1'); assert.equal(calls[3].options.headers['Content-Type'], undefined);
  assert.equal(calls[3].options.headers['X-CSRFToken'], 'test-session-token');
  await assert.rejects(sshRequest('https://example.com/connections'), /경로/);
  await assert.rejects(sshRequest('/api/modeltest/ssh/connections/../../secrets'), /경로/);
  assert.equal(calls.length, 4);
});

test('SSH writes stop before transmission if CSRF preparation fails', async t => {
  const calls = [];
  const mocked = t.mock.method(globalThis, 'fetch', async url => { calls.push(url); return Response.json({ csrf_token: null }); });
  await assert.rejects(sshRequest('/api/modeltest/ssh/connections', { method: 'POST', body: '{}' }), /보안 확인/);
  mocked.mock.mockImplementation(async url => { calls.push(url); return Response.json({ detail: 'denied' }, { status: 403 }); });
  await assert.rejects(sshRequest('/api/modeltest/ssh/connections', { method: 'POST', body: '{}' }));
  assert.deepEqual(calls, ['/api/v1/auth/csrf', '/api/v1/auth/csrf']);
});

test('local SSH failures keep actionable messages without silently using an HTTP Pod endpoint', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: 'SSH 키 인증에 실패했습니다.' }, { status: 400 }));
  await assert.rejects(sshRequest('/api/modeltest/ssh/connections'), error => error.status === 400 && error.message.includes('키 인증'));
});


test('connection summaries show only the destination, excluding key paths and HTTP credentials', () => {
  assert.equal(connectionTarget('ssh', 'ssh -i "key@private" -p 1234 root@203.0.113.10'), 'root@203.0.113.10');
  assert.equal(connectionTarget('ssh', 'ssh "user@ssh.runpod.io" -i "C:/private keys/id_key"'), 'user@ssh.runpod.io');
  assert.equal(connectionTarget('ssh', 'ssh root@[2001:db8::1] -p 22'), 'root@[2001:db8::1]');
  assert.equal(connectionTarget('ssh', 'ssh root@2001:db8::1'), 'root@2001:db8::1');
  assert.equal(connectionTarget('ssh', 'ssh -i key@private'), '');
  assert.equal(connectionTarget('http', 'https://name:secret@pod-8000.proxy.runpod.net/path?token=secret'), 'pod-8000.proxy.runpod.net');
  assert.equal(connectionTarget('http', 'http://localhost:8000'), 'localhost:8000');
  assert.equal(connectionTarget('http', ''), '');
});
