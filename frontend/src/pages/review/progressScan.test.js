import assert from 'node:assert/strict';
import test from 'node:test';
import { documentSteps, scanFocus } from './progressScan.js';

test('문서 분석은 화면이 받은 단계만으로 세 부분의 상태를 정한다', () => {
  const statuses = stage => documentSteps(stage).map(step => step.status);
  assert.deepEqual(statuses('preparing'), ['active', 'pending', 'pending']);
  assert.deepEqual(statuses('uploading'), ['active', 'pending', 'pending']);
  assert.deepEqual(statuses('queued'), ['completed', 'waiting', 'pending']);
  assert.deepEqual(statuses('extracting'), ['completed', 'active', 'pending']);
  assert.deepEqual(statuses('formatting'), ['completed', 'completed', 'active']);
  assert.deepEqual(statuses('completed'), ['completed', 'completed', 'completed']);
  // 모르는 단계는 지어내지 않고 모두 대기로 둔다.
  assert.deepEqual(statuses('unexpected'), ['pending', 'pending', 'pending']);
  assert.deepEqual(documentSteps('queued').map(step => step.kind), ['document', 'extract', 'criteria']);
});

test('돋보기는 진행·대기·멈춘 단계에, 없으면 다음 단계에, 모두 끝나면 마지막 단계에 놓인다', () => {
  const steps = (...statuses) => statuses.map((status, index) => ({ key: String(index), status }));
  assert.deepEqual(scanFocus(steps('completed', 'active', 'pending')), { index: 1, phase: 'working' });
  assert.deepEqual(scanFocus(steps('waiting', 'pending', 'pending')), { index: 0, phase: 'waiting' });
  assert.deepEqual(scanFocus(steps('completed', 'failed', 'pending')), { index: 1, phase: 'failed' });
  assert.deepEqual(scanFocus(steps('completed', 'pending', 'pending')), { index: 1, phase: 'between' });
  assert.deepEqual(scanFocus(steps('completed', 'completed', 'completed')), { index: 2, phase: 'done' });
  assert.deepEqual(scanFocus(steps('pending')), { index: 0, phase: 'between' });
});
