import React, { useId, useState } from 'react';

export function useInspector(key) {
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(key) !== 'closed'; } catch { return true; } });
  return [open, value => {
    setOpen(value);
    try { localStorage.setItem(key, value ? 'open' : 'closed'); } catch { /* The inspector still works when storage is unavailable. */ }
  }];
}

export default function ModeltestInspector({ open, onToggle, label, title, board, children }) {
  const id = useId();
  return <>
    <div className="mt-inspector-toolbar"><button type="button" className="btn secondary small" aria-label={`${label} ${open ? '접기' : '펼치기'}`} aria-expanded={open} aria-controls={id} onClick={onToggle}>설정 {open ? '접기' : '펼치기'}</button></div>
    <div className={`mt-model-editor ${open ? '' : 'is-collapsed'}`}>
      {board}
      <aside id={id} className="mt-model-inspector" aria-label={label} tabIndex={0} hidden={!open}>
        <div className="mt-panel-heading"><h3>{title}</h3></div>
        <div className="mt-inspector-body">{children}</div>
      </aside>
    </div>
  </>;
}
