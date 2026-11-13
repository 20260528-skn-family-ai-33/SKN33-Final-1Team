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

test('목록에 보일 이름·기준값을 따로 주고, 같은 말인 기준 문장은 다시 적지 않는다', () => {
  const criterion = (id, field, label) => ({ criterion_id: id, required: true, source: { page: 1, quote: '원문' },
    details: { field, ...(label ? { presentation: { label } } : {}) } });
  const result = (id, text, expected, extra = {}) => ({ criterion_id: id, criterion_text: text, expected, kind: 'text', verdict: 'pass', issues: [], ...extra });
  const sentence = '2026. 8. 22. 토요일 ~ 2026. 9. 6. 일요일 날짜를 정규화해 비교한다.';
  const view = frameworkResults({ context: { run_id: 'run-2', asset_version_id: 'poster-1' },
    criteria: { criteria: [criterion('start', 'start_date', '행사 일정'), criterion('weekday', 'weekday', '행사 일정'),
      criterion('place', 'venue'), criterion('size', 'pixel_width'), criterion('other', 'unspecified')] },
    validated: { items: [result('start', sentence, '2026. 8. 22.'), result('weekday', sentence, '토요일'),
      result('place', '장소: 시민공원', '시민공원'), result('size', '가로 1080', '1080', { expected_unit: 'px' }),
      result('other', '문구가 잘리지 않아야 한다', '')] } }, {});
  // 한 문장에서 나온 두 기준은 문장이 같아도 기준값으로 구분된다
  assert.deepEqual(view.results.slice(0, 2).map(item => [item.label, item.expected, item.rule]),
    [['행사 일정', '2026. 8. 22.', sentence], ['행사 일정', '토요일', sentence]]);
  // 이름은 기준에 적힌 표시 이름 → 항목 종류의 이름 순으로 정하고, 이름·기준값과 같은 말인 문장은 다시 적지 않는다
  assert.deepEqual([view.results[2].label, view.results[2].expected, view.results[2].rule], ['장소', '시민공원', null]);
  assert.deepEqual([view.results[3].label, view.results[3].expected, view.results[3].rule], ['가로 픽셀', '1080px', '가로 1080']);
  // 이름을 알 수 없는 기준은 문장만 보인다
  assert.deepEqual([view.results[4].label, view.results[4].rule, view.results[4].criterion], [null, null, '문구가 잘리지 않아야 한다']);
});

test('이전 포스터·좌표 미제공·잘못된 좌표·크기 미확인에는 위치를 만들어내지 않는다', () => {
  for (const image of [{ ...item.image, asset_version_id: 'old-poster' }, { ...item.image, bbox: null },
    { ...item.image, bbox: [-.1, .2, .5, .6] }, { ...item.image, bbox: [.5, .2, .1, .6] }]) {
    const view = frameworkResults({ ...run, validated: { items: [{ ...item, image }] } }, { width: 1000, height: 2000 });
    assert.deepEqual(view.results[0].evidence, []);
  }
  assert.deepEqual(frameworkResults(run, {}).results[0].evidence, []);
});
