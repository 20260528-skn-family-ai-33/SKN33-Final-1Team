import test from 'node:test';
import assert from 'node:assert/strict';
import { candidateLabel, rejectionGroups } from './extractionRejections.js';

const rejected = (index, code, page, quote) => ({ candidate_index: index, code, source: quote ? { page, quote } : null });

test('같은 사유는 한 묶음으로, 같은 쪽·같은 원문은 한 줄로 합친다', () => {
  const groups = rejectionGroups([
    ...[1, 2, 3, 4, 5].map(index => rejected(index, 'A02_QUALIFIER_UNGROUNDED', 1, '로고는 흰 배경에서만 쓴다.')),
    rejected(6, 'A02_QUALIFIER_UNGROUNDED', 1, '행사명은 한 줄로 적는다.'),
    rejected(16, 'A02_PRESENTATION_UNGROUNDED', 2, '후원 기관 표기'),
    rejected(22, 'A02_RESPONSE_INVALID', 2, '  후원 기관 표기  '),
    rejected(23, 'A02_RESPONSE_INVALID', 2, '후원 기관 표기'),
    rejected(9, 'A02_QUALIFIER_UNGROUNDED', 1, '로고는 흰 배경에서만 쓴다.'),
  ]);
  assert.deepEqual(groups.map(group => [group.code, group.count]),
    [['A02_QUALIFIER_UNGROUNDED', 7], ['A02_PRESENTATION_UNGROUNDED', 1], ['A02_RESPONSE_INVALID', 2]]);
  assert.equal(groups[0].reason, '허용 조건이나 예외의 원문 근거가 일치하지 않습니다.');
  assert.deepEqual(groups[0].sources.map(source => [source.page, source.quote, source.label]),
    [[1, '로고는 흰 배경에서만 쓴다.', '후보 1–5, 9'], [1, '행사명은 한 줄로 적는다.', '후보 6']]);
  // 앞뒤 공백만 다른 인용은 같은 원문이다
  assert.deepEqual(groups[2].sources.map(source => [source.quote, source.label]), [['후원 기관 표기', '후보 22–23']]);
});

test('원문이 없는 후보와 모르는 사유도 빠뜨리지 않는다', () => {
  const groups = rejectionGroups([rejected(3, 'A02_NEW_RULE', 4, '새 규칙'), rejected(4, 'A02_RESPONSE_INVALID'), rejected(7, 'A02_RESPONSE_INVALID'),
    { candidate_index: 8, code: 'A02_RESPONSE_INVALID', source: { page: 3, quote: '   ' } }]);
  assert.deepEqual(groups.map(group => [group.code, group.known, group.reason, group.count]),
    [['A02_NEW_RULE', false, '원문 검증에 실패했습니다.', 1], ['A02_RESPONSE_INVALID', true, '항목의 형식이나 비교 조건이 유효하지 않습니다.', 3]]);
  assert.deepEqual(groups[1].sources, [{ key: 'none', page: null, quote: '', candidates: [4, 7, 8], label: '후보 4, 7–8' }]);
  assert.deepEqual(rejectionGroups([]), []);
  assert.deepEqual(rejectionGroups(undefined), []);
});

test('후보 번호는 이어진 구간으로 줄이고, 구간이 많으면 개수만 적는다', () => {
  assert.equal(candidateLabel([16]), '후보 16');
  assert.equal(candidateLabel([9, 5, 6, 7, 8]), '후보 5–9');
  assert.equal(candidateLabel([1, 3, 4, 10]), '후보 1, 3–4, 10');
  assert.equal(candidateLabel([1, 3, 5, 7, 9]), '후보 5개');
  assert.equal(candidateLabel([2, 2, 3]), '후보 2–3');
  assert.equal(candidateLabel([]), '');
});
