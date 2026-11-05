import React from 'react';

const targets = { poster_text: '포스터 문구', poster_visual: '포스터 시각 요소', file_metadata: '파일 정보', delivery: '납품 조건' };
const fields = {
  poster_text: { wording: '일반 문구', event_name: '행사명', title_phrase: '제목', year: '연도', start_date: '시작일', end_date: '종료일', event_date: '행사일', event_period: '행사 기간', weekday: '요일', venue: '장소', viewing_hours: '관람시간' },
  poster_visual: { visual_requirements: '시각 조건', logo: '로고', visual_color: '색상', composition: '배치', imagery: '이미지' },
  file_metadata: { pixel_width: '가로 픽셀', pixel_height: '세로 픽셀', file_format: '파일 형식', color_mode: '색상 모드' },
  delivery: { quantity: '납품 수량' },
};
const operators = { text: { contains: '문구 포함', equals: '문구 일치' }, number: { equals: '같음', gte: '이상', lte: '이하' }, visual: { present: '포함 여부' } };

export default function CriteriaAmendment({ value, onChange, disabled }) {
  const resolution = value.resolution;
  const fieldOptions = fields[resolution.target] || {};
  const updateResolution = patch => onChange({ ...value, resolution: { ...resolution, ...patch } });
  const updateSource = patch => onChange({ ...value, source: { ...value.source, ...patch } });
  return <fieldset className="rv-criteria-amendment" disabled={disabled}>
    <legend>기준 보완</legend>
    <label>검사 대상 <select aria-label="검사 대상" required value={resolution.target} onChange={e => {
      const target = e.target.value;
      const kind = target === 'poster_visual' ? 'visual' : resolution.kind === 'visual' ? 'text' : resolution.kind;
      updateResolution({ target, field: '', kind, operator: Object.keys(operators[kind])[0], unit: kind === 'number' ? resolution.unit : null });
    }}><option value="">선택해 주세요</option>{Object.entries(targets).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
    <label>검사 항목 <select aria-label="검사 항목" required value={resolution.field} onChange={e => updateResolution({ field: e.target.value })}>
      <option value="">선택해 주세요</option>{Object.entries(fieldOptions).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      {resolution.field && !fieldOptions[resolution.field] && <option value={resolution.field}>기존 검사 항목</option>}
    </select></label>
    <label>값 유형 <select aria-label="값 유형" value={resolution.kind} onChange={e => updateResolution({ kind: e.target.value, operator: Object.keys(operators[e.target.value])[0], unit: null })}>
      {resolution.target !== 'poster_visual' && <><option value="text">문구</option><option value="number">숫자</option></>}
      {resolution.target === 'poster_visual' && <option value="visual">시각 요소</option>}
    </select></label>
    <label>비교 방법 <select aria-label="비교 방법" value={resolution.operator} onChange={e => updateResolution({ operator: e.target.value })}>
      {Object.entries(operators[resolution.kind]).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
    </select></label>
    <label>보완값 <input required maxLength={4000} value={resolution.expected} onChange={e => updateResolution({ expected: e.target.value })} /></label>
    <label><input type="checkbox" checked={resolution.replacement_text !== undefined} onChange={e => {
      const next = { ...resolution };
      if (e.target.checked) next.replacement_text = ''; else delete next.replacement_text;
      onChange({ ...value, resolution: next });
    }} /> 회신에 따라 조건 자체를 변경</label>
    {resolution.replacement_text !== undefined && <label>변경한 전체 조건 <textarea required maxLength={4000}
      value={resolution.replacement_text} onChange={e => updateResolution({ replacement_text: e.target.value })} /></label>}
    {resolution.replacement_text !== undefined && <p>기존 허용 조건을 포함해 대체할 조건 전체를 회신 원문 그대로 입력하세요. 새 버전의 원문·변경 이력을 보존하고 전체 기준을 다시 승인받습니다.</p>}
    {resolution.kind === 'number' && <label>단위 <input maxLength={80} value={resolution.unit || ''} onChange={e => updateResolution({ unit: e.target.value || null })} /></label>}
    <label><input type="checkbox" checked={resolution.required} onChange={e => updateResolution({ required: e.target.checked })} /> 필수 조건</label>
    <label>보완 사유 <input required maxLength={4000} value={value.reason} onChange={e => onChange({ ...value, reason: e.target.value })} /></label>
    <label>회신 출처 <input required maxLength={500} value={value.source.reference} onChange={e => updateSource({ reference: e.target.value })} /></label>
    <label>회신 원문 <textarea required maxLength={4000} value={value.source.quote} onChange={e => updateSource({ quote: e.target.value })} /></label>
    <p>보완값과 단위가 포함된 회신 원문을 입력해 주세요. 날짜·시각 요소·파일 정보·납품 조건은 현재 자동 판정 범위에 따라 확인 필요로 남을 수 있습니다.</p>
  </fieldset>;
}
