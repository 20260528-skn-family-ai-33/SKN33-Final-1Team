import test from 'node:test';
import assert from 'node:assert/strict';
import { buildConfirmationForm } from './confirmationForm.js';
import { buildReportContext } from './reportBrief.js';

const run = {
  context: { run_id: 'run-1', asset_version_id: 'poster-v1' },
  baseline: { criteria_version_id: 'criteria-v1' },
  criteria: { criteria: [
    { criterion_id: 'size', text: '이미지 크기', details: { presentation: { label: '이미지 크기' } } },
    { criterion_id: 'notice', text: '안내문', details: { presentation: { label: '중앙 건물의 안내문' } } },
    { criterion_id: 'char', text: '캐릭터', details: { presentation: { label: '김밥 캐릭터' } } },
    { criterion_id: 'name', text: '행사명', details: { field: 'event_name' } },
    { criterion_id: 'eng', text: '작은 영문', details: { presentation: { label: '표지판의 작은 영문' } } },
  ] },
  validated: { verdict: 'fail', items: [
    { criterion_id: 'name', required: true, expected: '김천김밥축제', observed: '김천김밥축제', observation_status: 'observed', verdict: 'pass', issues: [] },
    { criterion_id: 'notice', required: true, expected: null, observation_status: 'not_observed', verdict: 'review', issues: [] },
    { criterion_id: 'size', required: true, expected: '720 × 1018', observed: '600 × 848', observation_status: 'observed', verdict: 'fail', issues: ['value_mismatch'] },
    { criterion_id: 'char', required: true, expected: null, observation_status: 'not_observed', verdict: 'review', issues: [] },
    { criterion_id: 'eng', required: false, expected: null, observation_status: 'not_observed', verdict: 'review', issues: [] },
  ] },
  step_records: [{ role: 'A06', finished_at: '2026-10-14T01:43:00Z' }],
  approvals: [{ decision: 'approve_changes', human_reviews: [{ criterion_id: 'char', verdict: 'pass', observed: '원본 대조' }] }],
};

test('확인서는 보고서 context의 수치·항목 번호를 그대로 쓴다', () => {
  const context = buildReportContext(run, { projectName: '김천김밥축제' });
  const form = buildConfirmationForm(context, { comments: { notice: '  가독성 양호  ' }, decision: 'changes_approved' });
  assert.equal(form.title, '김천김밥축제 포스터 검수 확인서');
  assert.equal(form.checkedAt, '2026-10-14 10:43');
  assert.equal(form.criteriaVersion, 'criteria-v1');
  assert.equal(form.criteriaDocument, null);
  assert.deepEqual(form.counts, { required: 4, pass: 1, review: 2, fail: 1, optional: 1 });
  // 보고서 순서(불충족 → 확인 필요 → 충족)와 번호가 같다.
  assert.deepEqual(form.fixes, [{ number: '01', label: '이미지 크기', observed: '‘600 × 848’', request: '기준 ‘720 × 1018’ · 값이 기준과 다름' }]);
  assert.deepEqual(form.checks.map(check => [check.number, check.label, check.human, check.comment]),
    [['02', '중앙 건물의 안내문', null, '가독성 양호'], ['03', '김밥 캐릭터', '충족', '']]);
  assert.deepEqual(form.optional, ['04 표지판의 작은 영문']);
  assert.equal(form.decision, 'changes_approved');
});

test('결정 기록이 없거나 모르는 값이면 결정 칸을 비운다', () => {
  const context = buildReportContext(run, {});
  assert.equal(buildConfirmationForm(context, { decision: 'unreviewed' }).decision, null);
  assert.equal(buildConfirmationForm(context).title, '포스터 검수 확인서');
  assert.deepEqual(buildConfirmationForm(null).counts, { required: 0, pass: 0, review: 0, fail: 0, optional: 0 });
});
