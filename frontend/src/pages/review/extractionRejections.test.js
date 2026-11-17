import test from 'node:test';
import assert from 'node:assert/strict';
import { candidateLabel, rejectionGroups, extractionFailureOf, extractionFailureItems, extractionFailureMessage } from './extractionRejections.js';

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

test('실패 항목은 문제 필드와 원문을 보여 주고 모델 원응답은 제외한다', () => {
  const error = { code: 'A02_UNRESOLVED_VALUE_INVENTED', message: 'private provider response', raw_response: 'private raw response',
    extraction_rejections: [{ candidate_index: 2, code: 'A02_UNRESOLVED_VALUE_INVENTED',
      fields: ['expected', 'unit', 'normalized_expected', 'operator', 'presentation', 'presentation', 'raw_response'],
      source: { block_id: 'input-text', page: 1, quote: '행사 날짜는 미정입니다.' } }], response_sha256: 'a'.repeat(64) };
  const detail = extractionFailureOf({ error });
  assert.deepEqual(Object.keys(detail), ['extraction_rejections', 'response_sha256']);
  assert.deepEqual(extractionFailureItems(detail), [{ candidate: '항목 2', code: error.code,
    reason: '미확정 항목에 값·표시 문구 또는 자동 비교 방식이 함께 지정됐습니다.',
    fields: ['비교할 값', '단위', '날짜 정규화 값', '판정 방식', '화면 표시 문구'],
    quote: '행사 날짜는 미정입니다.', source: '직접 입력 원문' }]);
  assert.doesNotMatch(extractionFailureMessage({ error }), /private/);
});

test('실행 요약에 진단이 없으면 단계 기록을 사용하고 이전 실패도 읽을 수 있다', () => {
  const item = rejected(3, 'A02_NEW_RULE', 4, '행사명은 봄입니다.');
  const stepError = { extraction_rejections: [item], response_sha256: 'b'.repeat(64) };
  const run = { error: { code: 'A02_NEW_RULE', message: 'private model content' }, step_records: [{ error: stepError }] };
  assert.deepEqual(extractionFailureOf(run), stepError);
  assert.deepEqual(extractionFailureItems(extractionFailureOf(run)), [{ candidate: '항목 3', code: 'A02_NEW_RULE',
    reason: '원문 검증에 실패했습니다.', fields: [], quote: '행사명은 봄입니다.', source: '원문 4쪽' }]);
  assert.deepEqual(extractionFailureOf({ error: { code: 'A02_RESPONSE_INVALID' } }), {});
  assert.deepEqual(extractionFailureItems({ extraction_rejections: [null] }), []);
  assert.match(extractionFailureMessage({ error: { code: 'A02_RESPONSE_INVALID' } }), /이 실행에는 항목별 상세 원인이 저장되어 있지 않습니다/);
  assert.equal(extractionFailureMessage({ error: { code: 'LEGACY_ERROR', message: '이전 오류 안내' } }), 'LEGACY_ERROR · 이전 오류 안내');
});
