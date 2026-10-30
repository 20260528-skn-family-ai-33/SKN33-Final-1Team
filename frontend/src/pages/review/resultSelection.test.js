import test from 'node:test';
import assert from 'node:assert/strict';
import { filterResults, selectVisibleResult, evidenceOverlays } from './resultSelection.js';

const results = [
  { criterion_id: 'date', status: 'fail', criterion: '기간 일치', observed_text: '11월 2일', reason: '종료일 불일치', evidence: [{ text: 'Festival', bbox_xyxy: [10, 20, 40, 50] }] },
  { criterion_id: 'logo', status: 'review', criterion: '로고 확인', evidence: [{ text: '작은 로고', bbox_xyxy: [50, 10, 80, 40] }, { text: '두 번째 로고', bbox_xyxy: [10, 60, 40, 90] }] },
  { criterion_id: 'title', status: 'pass', criterion: '행사명 일치', evidence: [] },
];

test('criterion selection remains stable after list order changes', () => {
  assert.equal(selectVisibleResult([...results].reverse(), 'date'), results[0]);
});
test('filtering away the selected criterion picks the first visible item', () => {
  const visible = filterResults(results, 'review');
  assert.equal(selectVisibleResult(visible, 'date'), results[1]);
});
test('search covers criterion, observation, reason and evidence text with trim/case normalization', () => {
  for (const query of ['기간', '11월', '불일치', ' FESTIVAL ']) {
    assert.deepEqual(filterResults(results, 'all', query), [results[0]]);
  }
  assert.deepEqual(filterResults(results, 'pass', '불일치'), []);
});
test('empty matches clear selection and overlays without a stale criterion', () => {
  const visible = filterResults(results, 'fail', '없음');
  assert.equal(selectVisibleResult(visible, 'date'), null);
  assert.deepEqual(evidenceOverlays(visible), []);
});
test('all valid regions retain their criterion ID, including multiple regions of one item', () => {
  const overlays = evidenceOverlays(filterResults(results, 'review'));
  assert.deepEqual(overlays.map(item => item.id), ['logo', 'logo']);
  assert.deepEqual(overlays.map(item => item.bbox_xyxy), [[50, 10, 80, 40], [10, 60, 40, 90]]);
});
test('missing, malformed and reversed boxes never become image overlays', () => {
  const invalid = [{ text: 'none' }, { bbox_xyxy: [0, 0, NaN, 1] }, { bbox_xyxy: [10, 0, 1, 2] }, { bbox_xyxy: [0, 0, 1] }];
  assert.deepEqual(evidenceOverlays([{ criterion_id: 'invalid', evidence: invalid }]), []);
});
