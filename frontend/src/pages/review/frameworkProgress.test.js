import assert from 'node:assert/strict';
import test from 'node:test';
import { frameworkProgress } from './frameworkProgress.js';

test('접수·추론 중인 단계에 임의 백분율을 만들지 않는다', () => {
  const run = { baseline: {}, execution_status: 'queued', step_records: [] };
  const queued = frameworkProgress(run);
  assert.equal(queued.percent, null);
  assert.equal(queued.detail, '0 / 3개 처리 단계 완료');
  assert.equal(frameworkProgress({ ...run, execution_status: 'reviewing',
    step_records: [{ role: 'A04_A05', status: 'running', duration_ms: 100000 }] }).percent, null);
});

test('부분 판정이 있어도 A07이 실행 중이면 완료한 경계만 진행률에 반영한다', () => {
  const progress = frameworkProgress({ baseline: {}, execution_status: 'reviewing', validated: {},
    step_records: [{ role: 'A04_A05', status: 'completed' }, { role: 'A06', status: 'completed' }, { role: 'A07', status: 'running' }] });
  assert.equal(progress.percent, 67);
  assert.equal(progress.steps[2].status, 'active');
  assert.equal(progress.message, '검수 보고서 정리');
});

test('PDF 추출 상태와 과거 완료 필드의 조회 호환성을 유지한다', () => {
  const extracting = frameworkProgress({ execution_status: 'extracting', step_records: [{ role: 'A02', status: 'running' }] });
  assert.equal(extracting.percent, null);
  assert.equal(extracting.steps.length, 1);
  assert.equal(frameworkProgress({ baseline: {}, stages: ['A04_A05', 'A06'], report: {} }).percent, 100);
});
