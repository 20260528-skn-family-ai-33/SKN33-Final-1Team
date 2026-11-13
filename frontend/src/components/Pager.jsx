import React, { useState } from 'react';

// 긴 목록을 몇 개씩 넘겨 본다(펼치기 대신 쪽 넘기기).
// 목록이 줄어 지금 쪽이 없어지면 마지막 쪽을 보인다. start는 지금 쪽 첫 항목의 전체 순번(0부터)이다.
export function usePaged(items, size) {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(items.length / size));
  const current = Math.min(page, pages - 1);
  const start = current * size;
  return { page: current, pages, start, shown: items.slice(start, start + size), setPage };
}

// 이전 · 지금 쪽 / 전체 쪽 · 다음. 한 쪽에 다 들어가면 그리지 않는다.
// 단추 이름은 '이전 쪽'·'다음 쪽'으로 두어 화면의 다른 '이전'·'다음' 단추와 섞이지 않게 한다. label은 무엇을 넘기는지(예: '검수 항목 쪽').
export function Pager({ page, pages, onChange, label, disabled = false }) {
  if (pages <= 1) return null;
  return <nav className="op-pager" aria-label={label}>
    <button type="button" aria-label="이전 쪽" disabled={disabled || page === 0} onClick={() => onChange(page - 1)}>
      <span aria-hidden="true">←</span> 이전</button>
    <span className="op-pager-count" role="status"><b>{page + 1}</b> / {pages}</span>
    <button type="button" aria-label="다음 쪽" disabled={disabled || page >= pages - 1} onClick={() => onChange(page + 1)}>
      다음 <span aria-hidden="true">→</span></button>
  </nav>;
}
