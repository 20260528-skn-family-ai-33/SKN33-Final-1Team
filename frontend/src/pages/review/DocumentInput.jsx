import React, { useEffect, useState } from 'react';
import { Icon } from '../../components/Icon.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';

export default function DocumentInput({ documentBusy, documentProgress, documentNotice, documentError, documentInput, selectDocument, cancelDocument, sampleBusy }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!documentBusy) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [documentBusy, documentProgress?.startedAt]);
  const seconds = Math.max(0, Math.floor((now - (documentProgress?.startedAt || now)) / 1000));
  const elapsed = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  return <div className="document-input" aria-busy={documentBusy}>
    <div className="document-input-heading"><div><strong>요구사항 문서 불러오기</strong><p>PDF의 조건을 읽어 아래 입력란에 추가합니다.</p></div>
      <input ref={documentInput} type="file" className="sr-only" accept="application/pdf,.pdf" aria-label="요구사항 PDF 선택" disabled={documentBusy || sampleBusy}
        onChange={event => { if (event.target.files.length) selectDocument([...event.target.files]); }} />
      <button type="button" className="btn secondary small" disabled={documentBusy || sampleBusy} onClick={() => documentInput.current.click()}><Icon name="file" />{documentBusy ? '조건 읽는 중…' : 'PDF 파일 추가'}</button>
    </div>
    <p className="help">PDF 1개 · 최대 10MiB / 12페이지 · 기존 입력은 유지됩니다.</p>
    {documentProgress && <div className={`document-progress ${documentProgress.stage === 'completed' ? 'completed' : ''}`}>
      <div className="document-progress-heading" role="status"><span>{documentProgress.message}</span><strong>{documentProgress.percent}%</strong></div>
      <div className="document-progress-track" role="progressbar" aria-label="PDF 조건 읽기 진행률" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={documentProgress.percent} aria-valuetext={`${documentProgress.percent}% · ${documentProgress.message}`}>
        <div className="document-progress-fill" style={{ width: `${documentProgress.percent}%` }} />
      </div>
      <div className="document-progress-meta"><span>{documentBusy ? '처리 중' : '반영 완료'} · 경과 {elapsed}</span><span>페이지·처리 단계 기준</span></div>
    </div>}
    {documentNotice && <p className="document-notice" role="status">{documentNotice}</p>}
    {documentBusy && <button type="button" className="text-btn" onClick={cancelDocument}>입력 반영 취소</button>}
    <ErrorMessage message={documentError} />
  </div>;
}
