import test from 'node:test';
import assert from 'node:assert/strict';
import { initialState, workflowReducer as reduce, canRun, canConfirm, evidenceBoxStyle } from './workflow.js';

function confirmed() {
  let state = reduce(initialState, { type: 'sample', projectName: '축제', requirements: '행사명: 김천김밥축제\n로고 포함', file: { name: 'poster.png' } });
  state = reduce(state, { type: 'prepare' });
  return reduce(state, { type: 'confirm' });
}

test('saved review restores its frozen criteria, original file and result after reload', () => {
  const file = { name: 'original.jpg' };
  const review = { id: 'saved-1', status: 'completed', project_name: '저장 검수', criteria_version: 7,
    criteria: [{ id: 'c1', text: '행사명: 축제', type: 'information', source: 'PDF', document_condition: { requirement: '축제' } }] };
  const state = reduce(initialState, { type: 'restore', review, file });
  assert.equal(state.step, 'results');
  assert.equal(state.version, 7);
  assert.equal(state.file, file);
  assert.deepEqual(state.snapshot.criteria, review.criteria);
  assert.equal(state.criteria[0].selected, true);
  assert.equal(canRun(state), true);
  assert.equal(reduce(state, { type: 'unlock' }).snapshot, null);
});

test('confirmed snapshot contains selected criteria only and remains locked', () => {
  let state = confirmed();
  assert.equal(state.version, 1);
  const original = state.snapshot.criteria[0].text;
  state = reduce(state, { type: 'update', id: 'criterion-1', patch: { text: '변경' } });
  assert.equal(state.snapshot.criteria[0].text, original);
  assert.equal(canRun(state), true);
});

test('editing clears old results, locks execution, and reconfirmation increments version', () => {
  let state = confirmed();
  state = reduce(state, { type: 'run', review: { id: 'r1', status: 'queued' } });
  state = reduce(state, { type: 'progress', review: { id: 'r1', status: 'completed', results: [] } });
  state = reduce(state, { type: 'unlock' });
  assert.equal(state.review, null);
  assert.equal(canRun(state), false);
  state = reduce(state, { type: 'update', id: 'criterion-2', patch: { selected: false } });
  state = reduce(state, { type: 'confirm' });
  assert.equal(state.version, 2);
  assert.equal(state.snapshot.criteria.length, 1);
  assert.equal(canRun(state), true);
});

test('new image or requirements invalidate all prior confirmation and results', () => {
  for (const field of ['file', 'requirements']) {
    const state = reduce(confirmed(), { type: 'input', field, value: field === 'file' ? { name: 'new.png' } : '새 항목' });
    assert.equal(canRun(state), false);
    assert.equal(state.review, null);
    assert.deepEqual(state.criteria, []);
  }
});

test('empty or all-excluded criteria cannot be confirmed', () => {
  let state = reduce(confirmed(), { type: 'unlock' });
  state = reduce(state, { type: 'select', selected: false });
  assert.equal(canConfirm(state), false);
  assert.equal(reduce(state, { type: 'confirm' }).snapshot, null);
});

test('late poll cannot replace a different running review', () => {
  let state = reduce(confirmed(), { type: 'run', review: { id: 'new', status: 'running' } });
  state = reduce(state, { type: 'progress', review: { id: 'old', status: 'completed' } });
  assert.equal(state.review.id, 'new');
  assert.equal(state.step, 'running');
});

test('evidence uses original image coordinates and clamps out-of-bounds regions', () => {
  assert.deepEqual(evidenceBoxStyle([60, 84.8, 300, 424], 600, 848), { left: '10%', top: '10%', width: '40%', height: '40%' });
  assert.deepEqual(evidenceBoxStyle([-10, 0, 700, 848], 600, 848), { left: '0%', top: '0%', width: '100%', height: '100%' });
  assert.equal(evidenceBoxStyle([2, 1, 1, 2], 600, 848), null);
});


test('500-character original requirement remains intact in the confirmed source', () => {
  const source = '가'.repeat(500);
  let state = reduce(initialState, { type: 'sample', projectName: '긴 요구사항', requirements: source, file: { name: 'poster.png' } });
  state = reduce(state, { type: 'prepare' });
  state = reduce(state, { type: 'update', id: 'criterion-1', patch: { text: '행사명: 김천김밥축제' } });
  state = reduce(state, { type: 'confirm' });
  assert.equal(state.snapshot.criteria[0].source, source);
  assert.equal(state.snapshot.criteria[0].text, '행사명: 김천김밥축제');
});

test('overlong criteria cannot bypass confirmation validation', () => {
  let state = reduce(confirmed(), { type: 'unlock' });
  state = reduce(state, { type: 'update', id: 'criterion-1', patch: { text: '가'.repeat(501) } });
  assert.equal(canConfirm(state), false);
});
