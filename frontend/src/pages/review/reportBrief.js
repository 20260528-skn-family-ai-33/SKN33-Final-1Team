// 팀 A07 보고서 템플릿(backend/a07_reporter/report_template.json, poster)의 context를 실행 기록으로 채운다.
// 판정은 A06 결과를 그대로 옮기고, 담당자 확인은 같은 줄에 덧붙인다(기계 판정은 덮어쓰지 않는다).
// 코드표는 템플릿 JSON과 같아야 한다(reportBrief.test.js가 대조한다).

export const CODES = {
  verdict: { pass: '충족', fail: '불충족', review: '확인 필요', na: '해당 없음' },
  observation: { observed: '읽음', absent: '포스터에서 찾지 못함', unreadable: '읽을 수 없음', not_checked: '검사하지 않음', unknown: '관측 상태 모름' },
  field_label: {
    event_name: '행사명', title_phrase: '제목 문구', wording: '문구', year: '연도', event_date: '행사일', start_date: '시작일',
    end_date: '종료일', event_period: '기간', weekday: '요일', venue: '장소', viewing_hours: '관람 시간',
    visual_requirements: '시각 요소', logo: '로고', visual_color: '색상', composition: '구성', imagery: '이미지',
    pixel_width: '가로 픽셀', pixel_height: '세로 픽셀', resolution_dpi: '해상도', file_size_bytes: '파일 크기',
    file_format: '파일 형식', color_mode: '색상 모드', quantity: '수량', unspecified: '기타 조건',
  },
  issue_reason: {
    value_mismatch: '값이 기준과 다름', observed_absence: '포스터에 없음', unit_mismatch: '단위가 다름',
    verdict_conflict: '검사 판정과 값 비교가 엇갈림', missing_expected_value: '기준값이 아직 정해지지 않음',
    date_comparison_review_required: '날짜·시간 표기 확인', qualifier_interpretation_required: '허용 범위 안인지 확인',
    normalized_value_review_required: '변환한 값이 원문과 같은지 확인', typed_attribute_review_required: '담당자 직접 확인 항목',
    unreadable_observation: '읽을 수 없음', missing_observation: '찾거나 읽지 못함', uncertain_observation: '검사 결과가 불확실함',
    model_review: '검사 모델이 사람 확인을 요청함', numeric_observation_invalid: '읽은 값을 숫자로 비교할 수 없음',
    numeric_expectation_invalid: '기준값을 숫자로 읽을 수 없음', unsupported_criterion: '자동 검사 범위 밖',
    missing_citation: '기준 원문 인용 없음', missing_requirement_evidence: '기준 원문 근거 없음',
    context_incomplete: '기준 근거가 최신이 아님', observation_status_unknown: '관측 상태 없음',
    observation_not_checked: '검사하지 않음', _unknown: '기타 사유({code})',
  },
  reason_priority: ['value_mismatch', 'observed_absence', 'unit_mismatch', 'verdict_conflict', 'missing_expected_value',
    'date_comparison_review_required', 'qualifier_interpretation_required', 'normalized_value_review_required',
    'typed_attribute_review_required', 'unreadable_observation', 'missing_observation', 'uncertain_observation', 'model_review',
    'numeric_observation_invalid', 'numeric_expectation_invalid', 'unsupported_criterion', 'missing_citation',
    'missing_requirement_evidence', 'context_incomplete', 'observation_status_unknown', 'observation_not_checked'],
  limitation_ko: {
    'Code validation only; visual accuracy and source authority are not independently verified.': '코드 검증만 했습니다. 이미지 인식 정확도와 기준 출처는 따로 검증하지 않았습니다.',
    'Approved-criteria context only; no PostgreSQL/Neo4j relationship lookup was executed.': '승인된 기준만 근거로 썼고 PostgreSQL·Neo4j 관계 조회는 하지 않았습니다.',
    'Approved version reused; a fresh poster-input confirmation is required.': '승인된 기준 버전을 다시 썼습니다. 새 포스터 입력은 다시 확인해야 합니다.',
  },
  action_rules: {
    value_mismatch: '{label}을(를) ‘{expected}’(으)로 고쳐 주세요.',
    observed_absence: '{label} ‘{expected}’ 표기를 넣어 주세요.',
    unit_mismatch: '{label}의 단위를 기준({expected})에 맞춰 주세요.',
    date_comparison_review_required: '{label}이(가) 기준과 같은 날짜·시간인지 확인해 주세요.',
    qualifier_interpretation_required: '{label} 문구가 허용 범위 안인지 확인해 주세요.',
    missing_expected_value: '{label}은(는) 담당자 회신으로 기준을 정한 뒤 다시 검수해 주세요.',
    typed_attribute_review_required: '{labels}은(는) 담당자가 직접 확인해 주세요.',
    _default_fail: '{label}을(를) 기준에 맞게 고쳐 주세요.',
    _default_review: '{label}을(를) 담당자가 직접 확인해 주세요.',
  },
};

// backend/a07_reporter/adapter.py와 같은 문구. A07이 Qwen 할 일 문장을 썼는지는 이 안내 줄로 안다.
export const DRAFT_NOTICE = '자동 검수 초안입니다. 담당자가 확인하기 전에는 승인이 아닙니다.';
export const SYNTHETIC_NOTICE = '합성 시험 데이터로 만든 보고서입니다.';
export const POLISH_NOTICE = '할 일 문장은 Qwen이 검수 결과를 바탕으로 쓴 초안입니다.';
const EVIDENCE_STATUS = { empty: '이 실행에는 기준 근거가 없습니다.', stale: '기준 근거가 최신 상태가 아닙니다.', unavailable: '기준 근거를 조회하지 못했습니다.' };
const FAIL_RULES = ['value_mismatch', 'observed_absence', 'unit_mismatch'];
const REVIEW_RULES = ['date_comparison_review_required', 'qualifier_interpretation_required', 'missing_expected_value', 'typed_attribute_review_required'];
const ORDER = { fail: 0, review: 1, pass: 2 };
const ACTION_KIND = { fail: '수정', review: '확인' };
const LINE = /^(수정|확인) \[([A-Za-z0-9_.:,-]+)\] (.+)$/;
const SKIP = new Set(" \t'\"‘’“”()[]{}<>「」『』.,!?…·:;~-");
const DIGIT_FINAL = { 0: 21, 1: 8, 3: 16, 6: 1, 7: 8, 8: 8 };
const LETTER_FINAL = { l: 8, m: 16, n: 4, r: 8 };

function finalOf(text) {
  for (const char of [...text].reverse()) {
    if (SKIP.has(char)) continue;
    if (char >= '가' && char <= '힣') return (char.charCodeAt(0) - 0xac00) % 28;
    if (/[0-9]/.test(char)) return DIGIT_FINAL[char] || 0;
    if (/[A-Za-z]/.test(char)) return LETTER_FINAL[char.toLowerCase()] || 0;
    return null;
  }
  return null;
}

// 을(를)·이(가)·은(는)·와(과)·(으)로를 앞 글자 받침으로 고른다. 모르면 괄호 쓰기 그대로 둔다.
export function particle(before, marker) {
  const final = finalOf(before);
  if (final === null) return marker;
  if (marker === '(으)로') return final === 0 || final === 8 ? '로' : '으로';
  const [outside, inside] = [marker[0], marker[2]];
  const [withFinal, without] = marker === '와(과)' ? [inside, outside] : [outside, inside];
  return final ? withFinal : without;
}

export function fill(rule, values) {
  const text = rule.replace(/\{(\w+)\}/g, (_, key) => values[key] ?? '');
  let out = '', last = 0;
  for (const match of text.matchAll(/을\(를\)|이\(가\)|은\(는\)|와\(과\)|\(으\)로/g)) {
    out += text.slice(last, match.index);
    out += particle(out, match[0]);
    last = match.index + match[0].length;
  }
  return out + text.slice(last);
}

export function reasonCode(issues = []) {
  return CODES.reason_priority.find(code => issues.includes(code)) || issues[0] || null;
}

export function reasonText(code) {
  if (!code) return '';
  return CODES.issue_reason[code] || CODES.issue_reason._unknown.replace('{code}', code);
}

// backend/a07_reporter/details.py location_hint와 같은 3×3 칸 말.
export function locationHint(bbox) {
  if (!Array.isArray(bbox) || bbox.length !== 4) return null;
  if (bbox[2] - bbox[0] >= 0.9 && bbox[3] - bbox[1] >= 0.9) return '전체';
  const x = (bbox[0] + bbox[2]) / 2, y = (bbox[1] + bbox[3]) / 2;
  const row = ['위', '가운데', '아래'][Math.min(Math.floor(y * 3), 2)];
  const col = ['왼쪽', '가운데', '오른쪽'][Math.min(Math.floor(x * 3), 2)];
  if (row === '가운데' && col === '가운데') return '가운데';
  if (row === '가운데') return col;
  return col === '가운데' ? `${row}쪽` : `${col} ${row}`;
}

const withUnit = (value, unit) => {
  if (value == null || !String(value).trim()) return null;
  const text = String(value).trim();
  return !unit || text.endsWith(unit) ? text : text + unit;
};

const shortText = text => {
  const flat = String(text || '').split(/\s+/).filter(Boolean).join(' ');
  return flat.length <= 20 ? flat : flat.slice(0, 20).trimEnd() + '…';
};

// 항목 이름: presentation.label → field_label[field] → 기준 문장 앞 20자.
// 기타 조건(unspecified)은 여러 개가 같은 이름이 되므로 기준 문장으로 대신한다.
export function itemLabel(criterion, item) {
  const presented = criterion?.details?.presentation?.label;
  if (presented) return presented;
  const field = criterion?.details?.field;
  if (field && field !== 'unspecified' && CODES.field_label[field]) return CODES.field_label[field];
  return shortText(item?.criterion_text || criterion?.text || item?.criterion_id);
}

export function actionText(verdict, code, label, expected) {
  const allowed = verdict === 'fail' ? FAIL_RULES : REVIEW_RULES;
  let rule = allowed.includes(code) ? CODES.action_rules[code] : null;
  if (!rule || (rule.includes('{expected}') && !expected)) rule = CODES.action_rules[verdict === 'fail' ? '_default_fail' : '_default_review'];
  return fill(rule, { label, labels: label, expected: expected || '' });
}

function groupable(verdict, code) {
  if (verdict !== 'review') return false;
  const rule = REVIEW_RULES.includes(code) ? CODES.action_rules[code] : CODES.action_rules._default_review;
  return !rule.includes('{expected}');
}

// A07 change_requests 가운데 Qwen이 쓴 할 일(조건 하나짜리 줄)만 조건 ID별로 돌려준다.
export function polishedActions(report) {
  const polished = (report?.summary || '').split('\n').includes('- ' + POLISH_NOTICE);
  if (!polished) return {};
  return Object.fromEntries((report.change_requests || []).map(line => line.match(LINE))
    .filter(match => match && !match[2].includes(',')).map(match => [match[2], match[3]]));
}

// 템플릿 actions: 불충족은 '수정', 확인 필요는 '확인'. 사유가 같은 '확인'은 첫 항목 자리에서 한 줄로 묶는다.
export function buildActions(items, polished = {}) {
  const groups = new Map();
  for (const item of items) {
    const code = item.verdict.code;
    if (code === 'pass') continue;
    const reason = reasonCode(item.issues);
    const key = groupable(code, reason) ? 'group:' + reason : 'item:' + item.criterion_id;
    if (!groups.has(key)) groups.set(key, { verdict: code, reason, members: [] });
    groups.get(key).members.push(item);
  }
  return [...groups.values()].map(({ verdict, reason, members }) => {
    const label = members.map(member => member.label).join('·');
    const single = members.length === 1 && polished[members[0].criterion_id];
    return { kind: ACTION_KIND[verdict], label, time: null,
      text: single || actionText(verdict, reason, label, members[0].expected), criterion_ids: members.map(m => m.criterion_id) };
  });
}

export function kstMinute(value) {
  const time = Date.parse(value || '');
  if (!Number.isFinite(time)) return null;
  return new Date(time + 9 * 3600 * 1000).toISOString().slice(0, 16).replace('T', ' ');
}

function latestHumanReviews(approvals = []) {
  const out = {};
  for (const approval of approvals) for (const review of approval.human_reviews || []) out[review.criterion_id] = review;
  return out;
}

// 실행 기록(RunSnapshot)으로 템플릿 poster context를 만든다. 실행 기록에 없는 파일 이름은 null로 둔다.
export function buildReportContext(run, { projectName, targetName = null, documentName = null } = {}) {
  const criteria = run?.criteria?.criteria || [];
  const evidence = run?.evidence?.evidence || [];
  const humans = latestHumanReviews(run?.approvals);
  const validated = run?.validated;
  const ordered = [...(validated?.items || [])].map((item, index) => ({ item, index }))
    .sort((a, b) => (ORDER[a.item.verdict] ?? 3) - (ORDER[b.item.verdict] ?? 3) || a.index - b.index).map(entry => entry.item);
  const items = ordered.map(item => {
    const criterion = criteria.find(entry => entry.criterion_id === item.criterion_id);
    const pages = [...new Set(evidence.filter(entry => entry.kind === 'current_requirement' && entry.criterion_ids?.includes(item.criterion_id))
      .map(entry => entry.source?.page).filter(page => page != null))].sort((a, b) => a - b);
    const image = item.image && (!item.image.asset_version_id || item.image.asset_version_id === run.context?.asset_version_id) ? item.image : null;
    const human = humans[item.criterion_id];
    const issues = [...(item.issues || [])];
    return {
      criterion_id: item.criterion_id,
      label: itemLabel(criterion, item),
      required: !!item.required,
      expected: withUnit(item.expected, item.expected_unit),
      observed: item.observation_status === 'observed' ? withUnit(item.observed, item.observed_unit) : null,
      observation: CODES.observation[item.observation_status] || CODES.observation.unknown,
      location: image?.bbox ? locationHint(image.bbox) : null,
      verdict: { code: item.verdict, label: CODES.verdict[item.verdict] || item.verdict },
      reason: item.verdict === 'pass' ? '' : reasonText(reasonCode(issues)),
      issues,
      source: pages.length ? `PDF ${pages.join('·')}쪽` : null,
      human: human ? { verdict: { code: human.verdict, label: CODES.verdict[human.verdict] || human.verdict },
        note: [human.observed, human.reason && '사유 ' + human.reason].filter(Boolean).join(' · ') } : null,
    };
  });
  const required = items.filter(item => item.required);
  const count = code => required.filter(item => item.verdict.code === code).length;
  const synthetic = !!(run?.synthetic || validated?.synthetic || run?.evidence?.synthetic);
  const polished = polishedActions(run?.report);
  const notes = [DRAFT_NOTICE, ...(synthetic ? [SYNTHETIC_NOTICE] : []), ...(Object.keys(polished).length ? [POLISH_NOTICE] : []),
    ...(validated?.limitations || []).map(text => CODES.limitation_ko[text] || text),
    ...(EVIDENCE_STATUS[run?.evidence?.status] ? [EVIDENCE_STATUS[run.evidence.status]] : []),
    ...(run?.evidence?.warnings || []).map(text => CODES.limitation_ko[text] || text)];
  const finished = [...(run?.step_records || [])].reverse().find(record => record.role === 'A06' && record.finished_at);
  const verdictCode = validated?.verdict || run?.machine_verdict;   // A06 판정. 실행 기록 요약값으로 보충한다
  return {
    report_type: 'poster',
    title: '포스터 검수 보고서',
    synthetic,
    checked_at: kstMinute(finished?.finished_at),
    run_id: run?.context?.run_id || null,
    project: { name: projectName || null },
    target: { name: targetName, version: run?.context?.asset_version_id || null },
    criteria: { document: documentName, version: run?.baseline?.criteria_version_id || run?.context?.criteria_version_id || null },
    verdict: { code: verdictCode || null, label: CODES.verdict[verdictCode] || '판정 없음' },
    counts: { required: required.length, pass: count('pass'), fail: count('fail'), review: count('review'), optional: items.length - required.length },
    items,
    actions: buildActions(items, polished).map(({ criterion_ids, ...action }) => action),
    notes: [...new Set(notes)],
  };
}
