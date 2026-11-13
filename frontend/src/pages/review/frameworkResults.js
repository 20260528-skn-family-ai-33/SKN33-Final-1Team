import { CODES } from './reportBrief.js';

// 결과 목록에서 항목을 한눈에 구분하도록 이름(행사명·기간…)과 기준값을 따로 준다.
// 한 원문 문장에서 나온 여러 기준은 문장이 같아서, 문장만 보이면 서로 구분되지 않는다. 이름을 알 수 없는 기준은 문장만 보인다.
function nameOf(criterion) {
  const presented = criterion?.details?.presentation?.label;
  if (presented) return presented;
  const field = criterion?.details?.field;
  return (field && field !== 'unspecified' && CODES.field_label[field]) || null;
}
function withUnit(value, unit) {
  const text = value == null ? '' : String(value).trim();
  if (!text) return null;
  return !unit || text.endsWith(unit) ? text : text + unit;
}
// 이름·기준값 아래에 덧붙일 기준 문장. '행사일: 11월 20일'처럼 이름·기준값과 같은 말이면 다시 적지 않는다.
const bare = text => String(text || '').replace(/[\s:：·,.\-–—~‘’'"“”()]/g, '');
function ruleOf(text, label, expected) {
  if (!label) return null;
  const sentence = bare(text);
  return !sentence || sentence === bare(label + (expected || '')) || sentence === bare(expected) ? null : text;
}

// 프레임워크의 판정·출처를 보존하고 기존 결과 화면이 받는 표시 형식만 맞춘다.
export function frameworkResults(run, image, projectName) {
  const criteria = run.criteria?.criteria || [];
  return {
    id: run.context.run_id,
    project_name: projectName || '포스터 검수',
    criteria_version_label: '승인된 검수 기준',
    status_label: run.error ? '검수 처리 오류 · 저장된 결과' : '자동 검수 완료',
    image,
    criteria: criteria.map(item => ({ id: item.criterion_id, document_condition: {
      level: item.required ? '필수 조건' : '참고 조건',
      inspection: `${item.source.page}쪽 · ${item.source.quote}${item.value_source ? `\n보완 회신: ${item.value_source.quote} · ${item.value_source.reference}` : ''}`,
    } })),
    results: (run.validated?.items || []).map(item => {
      const box = item.image?.asset_version_id === run.context.asset_version_id && item.image.bbox;
      const located = image?.width > 0 && image?.height > 0 && Array.isArray(box) && box.length === 4
        && box.every(value => Number.isFinite(value) && value >= 0 && value <= 1) && box[0] < box[2] && box[1] < box[3];
      const criterion = criteria.find(entry => entry.criterion_id === item.criterion_id);
      const label = nameOf(criterion);
      const expected = withUnit(item.expected ?? criterion?.expected, item.expected_unit ?? criterion?.unit);
      return {
        criterion_id: item.criterion_id, criterion: item.criterion_text,
        label, expected, rule: ruleOf(item.criterion_text, label, expected),
        type: item.kind === 'visual' ? 'visual' : 'information', status: item.verdict,
        observed_text: item.observed,
        reason: item.issues.length ? item.issues.map(issue => issue === 'date_comparison_review_required'
          ? '날짜는 원문과 대조한 뒤 담당자가 직접 확인해야 합니다.' : issue).join('\n')
          : item.verdict === 'pass' ? '검증 결과, 해당 기준을 충족합니다.' : '판정과 원문 기준을 함께 확인해 주세요.',
        evidence: located ? [{ text: item.observed || item.criterion_text,
          bbox_xyxy: box.map((value, index) => value * (index % 2 ? image.height : image.width)) }] : [],
      };
    }),
  };
}
