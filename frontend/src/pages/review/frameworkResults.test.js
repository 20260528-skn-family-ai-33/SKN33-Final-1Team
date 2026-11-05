import test from 'node:test';
import assert from 'node:assert/strict';
import { frameworkResults } from './frameworkResults.js';

const item = { criterion_id: 'date', criterion_text: '개막일', observed: '5월 14일', kind: 'text', verdict: 'review',
  issues: ['date_comparison_review_required'], image: { asset_version_id: 'poster-1', bbox: [.1, .2, .5, .6] } };
const run = { context: { run_id: 'run-1', asset_version_id: 'poster-1' },
  criteria: { criteria: [{ criterion_id: 'date', required: true, source: { page: 2, quote: '개막일 5월 14일' },
    value_source: { quote: '확정된 개막일 5월 14일', reference: '고객 회신' } }] }, validated: { items: [item] } };

test('기계 판정과 출처를 보존하며 현재 이미지의 정규화 좌표만 픽셀로 바꾼다', () => {
  const before = structuredClone(run);
  const view = frameworkResults(run, { width: 1000, height: 2000 });
  assert.equal(view.results[0].status, 'review');
  assert.deepEqual(view.results[0].evidence[0].bbox_xyxy, [100, 400, 500, 1200]);
  assert.match(view.criteria[0].document_condition.inspection, /2쪽 · 개막일 5월 14일/);
  assert.match(view.criteria[0].document_condition.inspection, /고객 회신/);
  assert.deepEqual(run, before);
});

test('이전 포스터·좌표 미제공·잘못된 좌표·크기 미확인에는 위치를 만들어내지 않는다', () => {
  for (const image of [{ ...item.image, asset_version_id: 'old-poster' }, { ...item.image, bbox: null },
    { ...item.image, bbox: [-.1, .2, .5, .6] }, { ...item.image, bbox: [.5, .2, .1, .6] }]) {
    const view = frameworkResults({ ...run, validated: { items: [{ ...item, image }] } }, { width: 1000, height: 2000 });
    assert.deepEqual(view.results[0].evidence, []);
  }
  assert.deepEqual(frameworkResults(run, {}).results[0].evidence, []);
});
