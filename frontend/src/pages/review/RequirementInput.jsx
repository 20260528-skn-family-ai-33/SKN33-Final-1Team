import React, { useEffect, useRef, useState } from 'react';

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

// 문서에서 추출한 요구사항. 추출한 항목은 처음부터 모두 검수에 포함되어 있고(만드는 쪽에서 selected: true), 뺄 항목만 체크를 푼다.
// 한꺼번에 길게 늘어놓지 않는다: 문구는 늘 한 줄로 보이고(긴 문구는 마우스를 올리면 전체가 보인다) 그 자리에서 고친다. FOLD개를 넘는 나머지는 접어 둔다.
// 고칠 때 줄 높이를 바꾸지 않는다. 높이가 바뀌면 다른 곳을 누르는 순간 아래 줄이 밀려 엉뚱한 칸을 누르게 된다.
const FOLD = 8;
export function ExtractedRequirements({ rows, onChange, disabled }) {
  const [open, setOpen] = useState(false);
  const everything = useRef(null);
  const selected = rows.filter(row => row.selected && row.text.trim()).length;
  const checked = rows.filter(row => row.selected).length;
  const shown = open ? rows : rows.slice(0, FOLD);
  // 일부만 고른 상태는 '전체 선택' 칸에 줄(–)로 표시한다.
  useEffect(() => {
    if (everything.current) everything.current.indeterminate = checked > 0 && checked < rows.length;
  }, [checked, rows.length]);
  function edit(id, change) {
    onChange(rows.map(row => row.id === id ? { ...row, ...change } : row));
  }
  return <div className="rv-extracted" aria-label="문서에서 추출한 요구사항">
    <div className="rv-form-row"><strong>문서에서 추출한 요구사항</strong><span className="rv-input-count">검수 포함 {selected} / {rows.length}개</span></div>
    {rows.length > 0 ? <>
      <div className="rv-extracted-tools">
        <label><input ref={everything} type="checkbox" checked={checked === rows.length} disabled={disabled}
          onChange={event => onChange(rows.map(row => ({ ...row, selected: event.target.checked })))} />전체 선택</label>
        <p className="rv-form-hint">검수에서 뺄 항목은 체크를 풀어 주세요. 문구를 누르면 고칠 수 있어요.</p>
      </div>
      <ul>{shown.map((row, index) => <li key={row.id} className={row.selected ? undefined : 'is-off'}>
        <input type="checkbox" checked={row.selected} disabled={disabled} aria-label={'문서 요구사항 ' + (index + 1) + '번 검수에 포함'}
          onChange={event => edit(row.id, { selected: event.target.checked })} />
        <textarea rows={1} maxLength={500} value={row.text} title={row.text} disabled={disabled} aria-label={'문서 요구사항 ' + (index + 1) + '번 문구'}
          onChange={event => edit(row.id, { text: event.target.value.replace(/[\r\n]+/g, ' ') })} />
        <button type="button" className="rv-button secondary rv-compact" disabled={disabled} aria-label={'문서 요구사항 ' + (index + 1) + '번 삭제'}
          onClick={() => onChange(rows.filter(item => item.id !== row.id))}>삭제</button>
      </li>)}</ul>
      {rows.length > FOLD && <button type="button" className="rv-extracted-more" aria-expanded={open}
        onClick={() => setOpen(value => !value)}>{open ? '접기' : `나머지 ${rows.length - FOLD}개 더 보기`}</button>}
    </> : <p className="rv-form-hint">남아 있는 문서 요구사항이 없어요.</p>}
  </div>;
}
