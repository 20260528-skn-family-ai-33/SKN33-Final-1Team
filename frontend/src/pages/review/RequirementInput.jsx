import React from 'react';

export default function RequirementInput({ id, value, onChange, placeholder, disabled, children }) {
  const count = value.split(/\r?\n/).filter(line => line.trim()).length;
  return <section className="rv-form-block rv-input-requirements">
    <label htmlFor={id}>검수 요구사항</label>
    <textarea id={id} rows={4} maxLength={6000} value={value} onChange={event => onChange(event.target.value)}
      placeholder={placeholder} disabled={disabled} aria-describedby={id + '-hint'} />
    <div className="rv-form-row rv-form-hint" id={id + '-hint'}><span>한 줄에 한 항목을 적어 주세요.</span><span>직접 입력 {count}개</span></div>
    {children}
  </section>;
}

export function ExtractedRequirements({ rows, onChange, disabled }) {
  const selected = rows.filter(row => row.selected && row.text.trim()).length;
  function edit(id, change) {
    onChange(rows.map(row => row.id === id ? { ...row, ...change } : row));
  }
  return <div className="rv-extracted" aria-label="문서에서 추출한 요구사항">
    <div className="rv-form-row"><strong>문서에서 추출한 요구사항</strong><span className="rv-input-count">검수 포함 {selected} / {rows.length}개</span></div>
    {rows.length > 0 ? <>
      <p className="rv-form-hint">검수에 포함할 항목을 체크하세요. 문구는 입력 칸에서 수정할 수 있어요.</p>
      <ul>{rows.map((row, index) => <li key={row.id}>
        <input type="checkbox" checked={row.selected} disabled={disabled} aria-label={'문서 요구사항 ' + (index + 1) + '번 검수에 포함'}
          onChange={event => edit(row.id, { selected: event.target.checked })} />
        <textarea rows={2} maxLength={500} value={row.text} disabled={disabled} aria-label={'문서 요구사항 ' + (index + 1) + '번 문구'}
          onChange={event => edit(row.id, { text: event.target.value.replace(/[\r\n]+/g, ' ') })} />
        <button type="button" className="rv-button secondary rv-compact" disabled={disabled} aria-label={'문서 요구사항 ' + (index + 1) + '번 삭제'}
          onClick={() => onChange(rows.filter(item => item.id !== row.id))}>삭제</button>
      </li>)}</ul>
    </> : <p className="rv-form-hint">남아 있는 문서 요구사항이 없어요.</p>}
  </div>;
}
