// A02가 낸 후보 가운데 원문 검증에서 빠진 것(extraction.rejected_candidates)을 화면용으로 묶는다.
// 같은 사유는 한 묶음, 그 안에서 같은 쪽·같은 원문은 한 줄로 합친다. 후보를 새로 판단하지 않는다.

export const REJECTION_REASONS = {
  A02_DUPLICATE_REQUIREMENT: '이미 포함된 기준과 중복됩니다.',
  A02_EXPECTED_UNGROUNDED: '기대값이 원문과 일치하지 않습니다.',
  A02_SOURCE_MISMATCH: '선택한 원문 범위를 확인할 수 없습니다.',
  A02_UNIT_UNGROUNDED: '단위가 원문과 일치하지 않습니다.',
  A02_UNRESOLVED_VALUE_INVENTED: '미확정 항목에 값·표시 문구 또는 자동 비교 방식이 함께 지정됐습니다.',
  A02_QUALIFIER_UNGROUNDED: '허용 조건이나 예외의 원문 근거가 일치하지 않습니다.',
  A02_NORMALIZATION_UNSUPPORTED: '이 항목에는 변환된 값 형식을 사용할 수 없습니다.',
  A02_NORMALIZED_VALUE_UNGROUNDED: '변환된 값이 원문과 일치하지 않습니다.',
  A02_NORMALIZED_VALUE_AMBIGUOUS: '변환된 값에 해당하는 원문 표기를 하나로 확인할 수 없습니다.',
  A02_PRESENTATION_MISSING: '기준에 표시할 문구가 없습니다.',
  A02_PRESENTATION_UNGROUNDED: '표시 문구가 원문과 일치하지 않습니다.',
  A02_PRESENTATION_SCOPE_LOST: '표시 문구에서 가로 또는 세로 구분이 빠졌습니다.',
  A02_COMPILED_SOURCE_TOO_LARGE: '원문 인용 범위가 너무 깁니다.',
  A02_RESPONSE_INVALID: '항목의 형식이나 비교 조건이 유효하지 않습니다.',
};
const UNKNOWN_REASON = '원문 검증에 실패했습니다.';

const REJECTION_FIELDS = {
  expected: '비교할 값', unit: '단위', normalized_expected: '날짜 정규화 값',
  operator: '판정 방식', presentation: '화면 표시 문구', source: '원문 범위',
  qualifiers: '허용 조건·예외', target: '검사 대상', kind: '항목 종류', field: '검수 항목',
};

// 서버가 저장한 진단만 표시한다. 모델 원응답이나 공급자 오류 메시지는 화면에 옮기지 않는다.
export function extractionFailureOf(run) {
  const error = run?.error;
  const detail = error?.extraction_rejections?.length ? error
    : [...(Array.isArray(run?.step_records) ? run.step_records : [])].reverse().find(step => step.error?.extraction_rejections?.length)?.error;
  return detail ? { extraction_rejections: detail.extraction_rejections, response_sha256: detail.response_sha256 } : {};
}

export function extractionFailureMessage(run) {
  const code = run?.error?.code;
  if (!code) return '';
  if (extractionFailureOf(run).extraction_rejections?.length) {
    return `요구사항 분석을 완료하지 못했습니다. 실패한 항목과 원인을 확인해 주세요. (${code})`;
  }
  if (code.startsWith('A02_')) {
    return `요구사항 분석을 완료하지 못했습니다. 이 실행에는 항목별 상세 원인이 저장되어 있지 않습니다. (${code})`;
  }
  return code + ' · ' + (run.error.message || '실행을 완료하지 못했습니다.');
}

export function extractionFailureItems(error) {
  const items = Array.isArray(error?.extraction_rejections) ? error.extraction_rejections : [];
  return items.filter(item => item && typeof item === 'object').slice(0, 128).map(item => ({
    candidate: Number.isInteger(item.candidate_index) ? `항목 ${item.candidate_index}` : '항목',
    code: item.code || 'UNKNOWN',
    reason: REJECTION_REASONS[item.code] || UNKNOWN_REASON,
    fields: [...new Set((Array.isArray(item.fields) ? item.fields : []).map(field => REJECTION_FIELDS[field]).filter(Boolean))],
    quote: typeof item.source?.quote === 'string' ? item.source.quote : '',
    source: item.source?.block_id === 'input-text' ? '직접 입력 원문'
      : Number.isInteger(item.source?.page) ? `원문 ${item.source.page}쪽` : '원문',
  }));
}

// 후보 번호를 이어진 구간으로 줄인다. [5,6,7,9] → '후보 5–7, 9'. 구간이 많으면 개수만 적는다.
export function candidateLabel(numbers) {
  const sorted = [...new Set(numbers.filter(Number.isFinite))].sort((a, b) => a - b);
  if (!sorted.length) return '';
  const ranges = [];
  for (const number of sorted) {
    const last = ranges[ranges.length - 1];
    if (last && number === last[1] + 1) last[1] = number;
    else ranges.push([number, number]);
  }
  if (ranges.length > 3) return `후보 ${sorted.length}개`;
  return '후보 ' + ranges.map(([from, to]) => (from === to ? String(from) : `${from}–${to}`)).join(', ');
}

// [{ code, known, reason, count, sources: [{ key, page, quote, candidates, label }] }] — 묶음과 줄은 처음 나온 후보 순서를 따른다.
// known이 false면 문구를 모르는 사유라 화면에 코드를 함께 적는다.
export function rejectionGroups(items = []) {
  const groups = new Map();
  for (const item of items || []) {
    const code = item.code || 'UNKNOWN';
    if (!groups.has(code)) groups.set(code, { code, known: code in REJECTION_REASONS, reason: REJECTION_REASONS[code] || UNKNOWN_REASON, count: 0, sources: new Map() });
    const group = groups.get(code);
    group.count += 1;
    const quote = item.source?.quote?.trim() || '';
    const page = quote ? item.source.page ?? null : null;
    const key = quote ? `${page}:${quote}` : 'none';
    if (!group.sources.has(key)) group.sources.set(key, { key, page, quote, candidates: [] });
    group.sources.get(key).candidates.push(item.candidate_index);
  }
  return [...groups.values()].map(group => ({ ...group,
    sources: [...group.sources.values()].map(source => ({ ...source, label: candidateLabel(source.candidates) })) }));
}
