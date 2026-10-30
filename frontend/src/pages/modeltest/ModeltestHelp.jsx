import React, { useId, useLayoutEffect, useRef, useState } from 'react';

export default function ModeltestHelp({ label, children, variant = 'tip' }) {
  const id = useId();
  const button = useRef(null); const popover = useRef(null); const [open, setOpen] = useState(false);
  function position() {
    if (variant !== 'tip' || !popover.current?.matches(':popover-open')) return;
    const anchor = button.current.getBoundingClientRect(); const box = popover.current.getBoundingClientRect();
    const below = innerHeight - anchor.bottom - 8;
    const top = box.height > below && anchor.top > below ? anchor.top - box.height - 8 : anchor.bottom + 8;
    popover.current.style.left = `${Math.max(8, Math.min(anchor.left, innerWidth - box.width - 8))}px`;
    popover.current.style.top = `${Math.max(8, Math.min(top, innerHeight - box.height - 8))}px`;
  }
  useLayoutEffect(() => {
    if (!open || variant !== 'tip') return;
    position(); window.addEventListener('resize', position); window.addEventListener('scroll', position, true);
    return () => { window.removeEventListener('resize', position); window.removeEventListener('scroll', position, true); };
  }, [open, variant]);
  return <>
    <button ref={button} type="button" className="mt-help-button" aria-label={`${label} 도움말`} aria-haspopup="dialog" aria-expanded={open} aria-controls={id} popoverTarget={id}>ⓘ</button>
    <div ref={popover} id={id} className="mt-help-popover" data-variant={variant} popover="auto" role="dialog" aria-label={`${label} 도움말`} onToggle={event => {
      setOpen(event.newState === 'open');
      if (event.newState === 'open') { position(); event.currentTarget.querySelector('button').focus({ preventScroll: true }); }
    }}>
      <div className="mt-help-heading"><strong>{label}</strong><button type="button" className="text-btn" popoverTarget={id} popoverTargetAction="hide" aria-label={`${label} 도움말 닫기`}>닫기</button></div>
      {children}
    </div>
  </>;
}
