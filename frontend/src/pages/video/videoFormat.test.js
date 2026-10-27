import test from 'node:test';
import assert from 'node:assert/strict';
import { parseRequirements } from '../../workflow.js';
import { criteriaPayload, diffChars, formatTime } from './videoFormat.js';

test('video criteria payload drops screen-only fields that the server rejects', () => {
  const payload = criteriaPayload(parseRequirements('행사명: 김천김밥축제\n로고가 보여야 합니다'));
  assert.deepEqual(payload.map(item => Object.keys(item).sort()), [['id', 'source', 'text', 'type'], ['id', 'source', 'text', 'type']]);
  assert.deepEqual(payload.map(item => item.type), ['information', 'visual']);
});

test('time labels never overflow the tenth digit', () => {
  assert.deepEqual([5963, 29800, 3000, 65200].map(formatTime), ['0:05.9', '0:29.8', '0:03', '1:05.2']);
});

test('diff marks only the differing characters', () => {
  assert.deepEqual(diffChars('할인 70%', '할인 50%').filter(([, d]) => d).map(([c]) => c), ['5']);
});
