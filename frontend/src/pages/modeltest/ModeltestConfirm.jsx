import React, { useEffect, useRef } from 'react';

export default function ModeltestConfirm({ request, onResolve }) {
  const dialog = useRef(null); const cancel = useRef(null); const confirm = useRef(null);
  useEffect(() => {
    if (!request) return;
    const previous = document.activeElement; const element = dialog.current;
    element.showModal(); cancel.current.focus();
    return () => { element.close(); if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, [request]);
  return <dialog ref={dialog} className="mt-confirm-dialog" aria-labelledby="mt-confirm-title" aria-describedby="mt-confirm-message"
    onCancel={event => { event.preventDefault(); onResolve(false); }}
    onKeyDown={event => {
      if (event.key !== 'Tab') return;
      if (event.shiftKey && document.activeElement === cancel.current) { event.preventDefault(); confirm.current.focus(); }
      else if (!event.shiftKey && document.activeElement === confirm.current) { event.preventDefault(); cancel.current.focus(); }
    }}>
    <h2 id="mt-confirm-title">{request?.title || '작업 확인'}</h2><p id="mt-confirm-message">{request?.message}</p>
    <div className="mt-confirm-actions"><button ref={cancel} type="button" className="btn secondary" onClick={() => onResolve(false)}>취소</button>
      <button ref={confirm} type="button" className={`btn ${request?.danger ? 'mt-confirm-danger' : 'primary'}`} onClick={() => onResolve(true)}>{request?.confirmLabel || '확인'}</button></div>
  </dialog>;
}
