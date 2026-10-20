import test from 'node:test';
import assert from 'node:assert/strict';
import * as C from '../../src/v1/contracts.js';
import { fixtures, ids, wrap, page, uid } from './fixtures.mjs';
test('revision preserves bigint boundaries and rejects numbers, leading zeros and overflow', () => {
  for (const value of ['1', '9007199254740993', '9223372036854775807']) assert.equal(C.revision(value), true);
  for (const value of [1, null, '01', '-1', '0', '9223372036854775808', '1e3']) assert.equal(C.revision(value), false);
});
test('wrong contract and missing state fail closed', () => {
  for (const body of [{}, { meta: { contract_version: 'legacy', request_id: uid(1) } }, { meta: { contract_version: 'cc-api-v1', request_id: 'old' } }]) assert.throws(() => C.envelope(body));
  assert.throws(() => C.session(wrap({ user: null }))); assert.equal(C.session(wrap({ authenticated: false, user: null })), null);
});
test('current IDs and project identity cannot be substituted', () => {
  assert.throws(() => C.sameProject(fixtures().image, uid(80))); assert.throws(() => C.sameId(fixtures().draft, uid(80)));
  assert.throws(() => C.image({ ...fixtures().image, url: 'https://elsewhere.invalid/image' }));
});
test('draft fields and selected booleans remain strict', () => {
  const f = fixtures(); assert.equal(C.draft(f.draft).revision, '9007199254740993');
  for (const patch of [{ revision: 2 }, { state: 'completed' }, { items: [{ ...f.draft.items[0], selected: 'true' }] }, { items: [f.draft.items[0], f.draft.items[0]] }, { source_documents: undefined }]) assert.throws(() => C.draft({ ...f.draft, ...patch }));
});
test('server version and unknown is_required are preserved', () => { const f = fixtures(); assert.equal(C.criteria(f.criteria).version_no, 37); assert.equal(C.criteria(f.criteria).criteria[0].is_required, null); });
test('failed and imported processing cannot manufacture a verdict', () => {
  const f = fixtures(); for (const processing_status of ['failed', 'unsupported', 'imported']) {
    assert.throws(() => C.review({ ...f.run, results: [{ ...f.run.results[0], processing_status, verdict: 'pass' }] }));
    assert.equal(C.review({ ...f.run, results: [{ ...f.run.results[0], processing_status, verdict: null }] }).results[0].verdict, null);
  }
});
test('unknown counts, duplicate IDs and unbounded cursors are rejected', () => {
  assert.throws(() => C.list(page([fixtures().project, fixtures().project]), C.project));
  assert.throws(() => C.list(page([], { has_more: true, next_cursor: '' }), C.project));
  assert.throws(() => C.list(page([], { has_more: true, next_cursor: 'x'.repeat(2049) }), C.project));
});
test('display bbox is never divided by OCR scale or clamped into a different frame', () => {
  const { evidence, image } = fixtures(); assert.deepEqual(C.boxStyle(evidence, image), { left: '10%', top: '10%', width: '80%', height: '20%' });
  for (const patch of [{ frame_id: uid(90) }, { coordinate_space: 'ocr_input_pixels' }, { bbox_xyxy: [-1, 0, 40, 60] }, { bbox_xyxy: [0, 0, 300, 60] }, { bbox_xyxy: [10, 10, 5, 20] }, { bbox_xyxy: [0, 0, Infinity, 2] }, { bbox_xyxy: null }]) assert.equal(C.boxStyle({ ...evidence, ...patch }, image), null);
});
test('null confidence stays unknown and invalid confidence fails', () => {
  assert.equal(C.evidence(fixtures().evidence).confidence, null); for (const confidence of [undefined, NaN, 1.1, -1, '1']) assert.throws(() => C.evidence({ ...fixtures().evidence, confidence }));
});
test('Unicode lengths and confirmation selection do not truncate input', () => {
  const value = C.content(fixtures().draft); value.items[0].text = '😀'.repeat(500); assert.equal(C.contentError(value, true), '');
  value.items[0].text += 'a'; assert.notEqual(C.contentError(value, true), ''); value.items[0].selected = false; assert.notEqual(C.contentError(value, true), '');
  assert.deepEqual(Object.keys(C.content(fixtures().draft)), ['raw_requirements', 'items', 'source_documents']);
});
test('login return URL accepts only local UUID workflow paths', () => {
  const pathname = `/projects/${ids.project}/reviews/${ids.run}`; assert.equal(C.destination({ pathname, search: '?cursor=safe' }), pathname + '?cursor=safe');
  for (const pathname of ['https://example.test', '//example.test', '/projects/old/reviews/nope', '/api/v1/reviews', '/projects/../secret']) assert.equal(C.destination({ pathname }), '/projects');
});
