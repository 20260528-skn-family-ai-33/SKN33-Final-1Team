import test from 'node:test';
import assert from 'node:assert/strict';
import { autoDrafts, bundleOutcome, diffKind, groupDifferences, handoff, OUTCOME, videoLogoSteps } from './logoFlow.js';

const group = (rank, sec, box = [10, 20, 60, 80]) => ({ secs: [sec], rank, rep: rank.map(() => [sec, box]), box: rank.map(() => ({})) });

test('video logo steps go straight from candidates to the detailed comparison', () => {
  assert.deepEqual(Object.keys(videoLogoSteps), ['input', 'candidates', 'compare']);
});

test('the server auto selection becomes the confirm screen selection and the same handoff crops', () => {
  const groups = [group([0.5, 0.1], 3), group([0.9, 0.2], 7)];
  const drafts = autoDrafts([{ logo: 0, decision: 'done', groups: [1, 0] }, { logo: 1, decision: 'none', groups: [] }], groups);
  assert.deepEqual(drafts[0], { decision: 'done', confirmed: [1, 0], checked: [1, 0], manuals: [], sec: 7 });
  assert.deepEqual(drafts[1], { decision: 'none', confirmed: [], checked: [], manuals: [], sec: 0 });
  const answer = handoff({ video: 'video', ref: 0, refName: '주최', decision: drafts[0].decision, groups, checked: new Set(drafts[0].checked), manuals: [] });
  // 서버 auto_answers와 같은 모양(group·sec·bbox·source 순)이어야 확정 화면이 '저장됨(자동 선택)'으로 본다.
  assert.deepEqual(answer.crops, [{ group: 1, sec: 7, bbox: [10, 20, 60, 80], source: 'candidate' }, { group: 0, sec: 3, bbox: [10, 20, 60, 80], source: 'candidate' }]);
  // 없어진 묶음 번호는 버리고, 남는 묶음이 없으면 대조하지 않는 것으로 본다.
  assert.equal(autoDrafts([{ logo: 0, decision: 'done', groups: [9] }], groups)[0].decision, 'none');
  assert.deepEqual(autoDrafts(null, groups), {});
});

test('detailed comparison shows hold for differences and pass when there are none', () => {
  assert.equal(OUTCOME.diff.label, '보류');
  assert.equal(OUTCOME.match.label, '통과');
  assert.equal(bundleOutcome({ match: 4, diff: 1 }), 'diff');
  assert.equal(bundleOutcome({ match: 3, unknown: 2 }), 'match');
  assert.equal(bundleOutcome({ unknown: 2 }), 'unknown');
  assert.equal(bundleOutcome(undefined), 'unknown');
});

test('differences are grouped by kind from the server finding sentences', () => {
  // backend/app/video/logos/compare.py가 쓰는 문장 모양
  assert.equal(diffKind("주 문구 '울산' 없음"), '빠짐');
  assert.equal(diffKind("원본 문구 'NAMGU' 없음(같은 크기 원본 OCR 기준)"), '빠짐');
  assert.equal(diffKind("원본에 없는 문구 '할인'"), '추가');
  assert.equal(diffKind('원본에 없는 요소 (위치 [1, 2, 3, 4])'), '추가');
  assert.equal(diffKind('심볼 모양이 원본과 다름(F1 0.41)'), '가림·변형');
  assert.equal(diffKind('심볼 위치가 원본과 다름'), '배치');
  assert.equal(diffKind('심볼:문구 크기 비율 다름 (원본 1.20, 영상 0.80)'), '배치');
  assert.equal(diffKind('로고 외곽 비율이 원본과 다름(가로/세로 원본 2.00, 영상 1.50)'), '배치');
  assert.equal(diffKind('색이 원본과 다름(색상 차이 40°, 빠진 유채색 30%)'), '색');
  assert.equal(diffKind('알 수 없는 문장'), '기타');
  const groups = groupDifferences([
    { sec: 1, outcome: 'diff', findings: ["주 문구 '울산' 없음", '색이 원본과 다름(색상 차이 40°, 빠진 유채색 30%)'] },
    { sec: 2, outcome: 'diff', findings: ["주 문구 '울산' 없음"] },
    { sec: 3, outcome: 'match', findings: [] },
  ]);
  // 차이가 있는 초만, 종류 순서(빠짐 → … → 색)대로 모은다
  assert.deepEqual(groups, [
    { kind: '빠짐', secs: 2, items: [["주 문구 '울산' 없음", 2]] },
    { kind: '색', secs: 1, items: [['색이 원본과 다름(색상 차이 40°, 빠진 유채색 30%)', 1]] },
  ]);
  assert.deepEqual(groupDifferences(undefined), []);
});
