import React from 'react';

const supported = new Set(['typed_attribute_review_required', 'qualifier_interpretation_required', 'normalized_value_review_required',
  'date_comparison_review_required', 'uncertain_observation', 'missing_observation', 'unreadable_observation',
  'observation_status_unknown', 'observation_not_checked', 'model_review', 'verdict_conflict', 'numeric_observation_invalid', 'unit_mismatch']);

export function reviewableItems(run) {
  if (run.evidence?.status !== 'complete' || !run.context.asset_version_id) return [];
  return (run.validated?.items || []).filter(item => item.verdict === 'review' && item.issues.length && item.issues.every(code => supported.has(code)));
}

export default function HumanReview({ items, values, onChange, disabled }) {
  return <section><h2>담당자 직접 확인</h2><p>현재 포스터와 승인된 기준을 직접 확인하고 판단 근거를 기록하세요. 기계 판정은 변경하지 않습니다.</p>
    {items.map(item => {
      const value = values[item.criterion_id] || { criterion_id: item.criterion_id, verdict: '', observed: '', reference: '', reason: '', checked_asset: false };
      const update = patch => onChange({ ...values, [item.criterion_id]: { ...value, ...patch } });
      return <fieldset className="rv-criteria-amendment" key={item.criterion_id} disabled={disabled}>
        <legend>{item.criterion_id} · {item.criterion_text}</legend>
        <p>기대값: {item.expected} {item.expected_unit || ''} · 기계 관측: {item.observed || '없음'}</p>
        <label>담당자 판단 <select aria-label="담당자 판단" value={value.verdict} onChange={e => update({ verdict: e.target.value })}>
          <option value="">미확인</option><option value="pass">충족</option><option value="fail">불충족</option>
        </select></label>
        <label>직접 확인한 내용 <textarea required={!!value.verdict} maxLength={4000} value={value.observed} onChange={e => update({ observed: e.target.value })} /></label>
        <label>확인 근거·위치 <input required={!!value.verdict} maxLength={1000} value={value.reference} onChange={e => update({ reference: e.target.value })} /></label>
        <label>판단 사유 <textarea required={!!value.verdict} maxLength={4000} value={value.reason} onChange={e => update({ reason: e.target.value })} /></label>
        <label><input type="checkbox" required={!!value.verdict} checked={value.checked_asset} onChange={e => update({ checked_asset: e.target.checked })} /> 현재 포스터를 직접 확인했습니다</label>
      </fieldset>;
    })}
  </section>;
}
