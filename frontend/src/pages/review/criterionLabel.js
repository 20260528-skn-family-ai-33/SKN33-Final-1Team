import { CODES } from './reportBrief.js';

// 검수 기준을 목록 한 줄로 보일 때 쓰는 이름 · 기준값 · 기준 문장(기준 확인 화면과 결과 화면이 같이 쓴다).
// 한 원문 문장에서 나온 여러 기준은 문장이 같아서, 문장만 보이면 서로 구분되지 않는다. 그래서 이름(행사명·기간…)과 기준값을 먼저 보인다.

// 이름: 기준에 적힌 표시 이름 → 항목 종류의 이름. 알 수 없으면 null(목록은 문장만 보인다).
export function criterionName(criterion) {
  const presented = criterion?.details?.presentation?.label;
  if (presented) return presented;
  const field = criterion?.details?.field;
  return (field && field !== 'unspecified' && CODES.field_label[field]) || null;
}

export function withUnit(value, unit) {
  const text = value == null ? '' : String(value).trim();
  if (!text) return null;
  return !unit || text.endsWith(unit) ? text : text + unit;
}

// 이름·기준값 아래에 덧붙일 기준 문장. '행사일: 11월 20일'처럼 이름·기준값과 같은 말이면 다시 적지 않는다.
const bare = text => String(text || '').replace(/[\s:：·,.\-–—~‘’'"“”()]/g, '');
export function criterionRule(text, label, expected) {
  if (!label) return null;
  const sentence = bare(text);
  return !sentence || sentence === bare(label + (expected || '')) || sentence === bare(expected) ? null : text;
}

// 기준 한 건의 목록 표시 값. 판정 결과처럼 문장·기준값을 따로 가진 쪽은 values로 넘긴다.
export function criterionLine(criterion, values = {}) {
  const label = criterionName(criterion);
  const expected = withUnit(values.expected ?? criterion?.expected, values.unit ?? criterion?.unit);
  return { label, expected, rule: criterionRule(values.text ?? criterion?.text, label, expected) };
}
