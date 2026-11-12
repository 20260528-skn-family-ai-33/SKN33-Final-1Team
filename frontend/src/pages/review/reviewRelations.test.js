import test from 'node:test';
import assert from 'node:assert/strict';
import { changeNotice, revisionView, subjectParticle } from './reviewRelations.js';

const item = (expected, id = 'c001') => ({ criterion_id: id, text: `장소: ${expected}`, expected, unit: null });
const impact = (changes, summary = { affected_runs: 3, approved: 2 }, basis = 'document_revision') => ({
  previous: { basis, criteria_version_id: 'v1' }, changes, summary, truncated: false,
  affected_runs: [
    { run_id: 'p1', execution_status: 'completed', human_decision: 'final_approved', created_at: '2026-11-01T00:00:00+00:00' },
    { run_id: 'p2', execution_status: 'completed', human_decision: 'exception_approved', created_at: '2026-11-02T00:00:00+00:00' },
    { run_id: 'p3', execution_status: 'waiting_review', human_decision: 'unreviewed', created_at: '2026-11-03T00:00:00+00:00' },
  ].slice(0, summary.affected_runs),
});

test('조사는 받침에 맞춘다', () => {
  assert.equal(subjectParticle('행사 장소'), '가');
  assert.equal(subjectParticle('행사일'), '이');
  assert.equal(subjectParticle('logo'), '이(가)');
});

test('행사 장소가 바뀐 새 PDF의 안내 문구', () => {
  const notice = changeNotice(impact([{ change: 'changed', label: '행사 장소', before: item('시청 광장'), after: item('시민공원', 'c002') }]));
  assert.equal(notice.headline, '행사 장소가 변경됐어요.');
  assert.equal(notice.detail, '이전 기준을 사용한 포스터 검수 3건 중 2건이 승인됐어요.');
  assert.equal(notice.action, '영향받는 검수 3건 보기');
  assert.deepEqual(notice.changes, [{ key: 'changed-0', tag: '변경', label: '행사 장소', before: '시청 광장', after: '시민공원' }]);
  assert.deepEqual(notice.runs.map(run => [run.runId, run.status, run.approved]),
    [['p1', '최종 승인', true], ['p2', '예외 승인', true], ['p3', '승인 대기', false]]);
});

test('여러 항목·추가·삭제와 영향받는 검수가 없는 경우', () => {
  const many = changeNotice(impact([
    { change: 'changed', label: '행사일', before: item('11월 20일'), after: item('11월 21일') },
    { change: 'added', label: null, before: null, after: item('', 'c009') },
    { change: 'removed', label: '로고', before: item('주최 로고'), after: null },
  ], { affected_runs: 0, approved: 0 }, 'project_previous'));
  assert.equal(many.headline, '행사일 외 2개 항목이 달라졌어요.');
  assert.equal(many.detail, '이전 기준을 사용한 포스터 검수는 아직 없어요.');
  assert.equal(many.action, null);
  assert.equal(many.basis, '이 프로젝트에서 직전에 승인한 기준과 비교했어요.');
  assert.deepEqual(many.changes.map(change => [change.tag, change.label, change.before, change.after]),
    [['변경', '행사일', '11월 20일', '11월 21일'], ['추가', '장소: ', null, '기대값 미정'], ['삭제', '로고', '주최 로고', null]]);
  assert.equal(changeNotice(impact([{ change: 'added', label: '로고', before: null, after: item('로고') }])).headline, '로고가 추가됐어요.');
});

test('이전 기준이 없거나 달라진 항목이 없으면 안내하지 않는다', () => {
  assert.equal(changeNotice(null), null);
  assert.equal(changeNotice({ previous: null, changes: [] }), null);
  assert.equal(changeNotice(impact([])), null);
});

const entry = (number, state, records = [], extra = {}) => ({
  run_id: `poster-${number}`, revision_no: number, state, asset_version_id: `asset-${number}`, criteria_version_no: 1,
  created_at: '2026-11-01T00:00:00+00:00', current: false, records, ...extra,
});
const record = (decision, reason, id = decision) => ({ approval_id: id, decision, reason, approved_at: '2026-11-01T01:00:00+00:00' });

test('수정본 검수 이력: 재검수 2회, 2차 수정본 최종 승인', () => {
  const view = revisionView({
    summary: { reviews: 3, rereviews: 2, final_approved: { run_id: 'poster-2', revision_no: 2 }, awaiting_decision: false },
    entries: [
      entry(0, 'changes_requested', [record('approve_criteria', ''), record('approve_changes', '행사 날짜 표기 수정')]),
      entry(1, 'changes_requested', [record('approve_changes', '주최 로고 위치 수정')]),
      entry(2, 'final_approved', [record('approve_final', '')], { current: true }),
    ],
  });
  assert.equal(view.rereviews, 2);
  assert.equal(view.final, '2차 수정본');
  assert.deepEqual(view.rows.map(row => [row.title, row.status, row.approved, row.criteria, row.current]), [
    ['원본 검수', '수정 요청', false, '검수 기준 v1', false],
    ['1차 수정본 검수', '수정 요청', false, '검수 기준 v1', false],
    ['2차 수정본 검수', '최종 승인', true, '검수 기준 v1', true],
  ]);
  // 검수 시작 확인(approve_criteria)은 담당자 결정 기록으로 보여 주지 않는다
  assert.deepEqual(view.rows[0].records.map(line => [line.label, line.reason]), [['수정 요청', '행사 날짜 표기 수정']]);
  assert.deepEqual(view.rows[2].records.map(line => [line.label, line.reason]), [['최종 승인', '']]);
});

test('재검수가 끝났지만 담당자 결정이 없으면 승인 대기', () => {
  const view = revisionView({
    summary: { reviews: 2, rereviews: 1, final_approved: null, awaiting_decision: true },
    entries: [entry(0, 'changes_requested', [record('approve_changes', '날짜 수정')]),
      entry(1, 'awaiting_decision', [record('approve_criteria', '')], { criteria_version_no: null })],
  });
  assert.equal(view.final, null);
  assert.equal(view.waiting, true);
  assert.equal(view.rows[1].status, '승인 대기');
  assert.equal(view.rows[1].criteria, '승인된 검수 기준');
  assert.deepEqual(view.rows[1].records, [{ key: 'none', label: null, reason: '검수가 끝났고 담당자 결정을 기다리고 있어요.', at: null }]);
});

test('수정본이 없으면 이력을 보여 주지 않는다', () => {
  assert.equal(revisionView(null), null);
  assert.equal(revisionView({ entries: [entry(0, 'awaiting_decision')], summary: { rereviews: 0 } }), null);
});
