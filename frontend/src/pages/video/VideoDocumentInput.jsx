import React, { useEffect, useRef, useState } from 'react';
import DocumentInput from '../review/DocumentInput.jsx';
import { mergeDocumentRequirements } from '../review/documentRequirements.js';
import { extractDocumentWithProgress } from '../review/frameworkDocument.js';

export default function VideoDocumentInput({ projectName, disabled, busy, setBusy, onApply }) {
  const [projectId, setProjectId] = useState('');
  const [progress, setProgress] = useState(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [rows, setRows] = useState([]);
  const input = useRef(null);
  const active = useRef(null);
  const attempts = useRef(new Map());
  useEffect(() => () => active.current?.abort(), []);

  function cancel() {
    active.current?.abort();
    active.current = null;
    setBusy(false);
    setProgress(null);
  }

  async function select(files, selectedProject) {
    if (active.current || disabled) return;
    if (input.current) input.current.value = '';
    const [file] = files;
    if (files.length !== 1 || !file.size || file.size > 10 * 1024 * 1024) {
      setError('내용이 있는 10MiB 이하 PDF 1개를 선택해 주세요.');
      return;
    }
    const controller = new AbortController();
    const { signal } = controller;
    const startedAt = Date.now();
    active.current = controller;
    setBusy(true);
    setError('');
    setProgress({ startedAt, stage: 'preparing', percent: 0, message: 'PDF를 확인하고 있습니다.' });
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 630_000);
    try {
      const signature = new Uint8Array(await file.slice(0, 5).arrayBuffer());
      if (String.fromCharCode(...signature) !== '%PDF-') throw new Error('PDF 파일만 읽을 수 있습니다.');
      const pdf = file.type === 'application/pdf' ? file : new File([file], file.name, { type: 'application/pdf' });
      const result = await extractDocumentWithProgress(pdf, { projectId: selectedProject, attempts: attempts.current, signal,
        onProgress: update => { if (!signal.aborted) setProgress({ ...update, startedAt }); } });
      if (signal.aborted) return;
      const { sources } = mergeDocumentRequirements('', result, file.name);
      setRows(sources.map(item => ({ ...item, selected: false })));
      setProgress({ startedAt, stage: 'completed', percent: 100, message: '요구사항 추출을 완료했습니다.' });
      setNotice(`${file.name} · ${sources.length}개 추출${result.pending?.length ? ` · 미확정 ${result.pending.length}개` : ''}`);
    } catch (failure) {
      if (!signal.aborted || timedOut) {
        setProgress(null);
        setError(timedOut ? '문서 분석 대기 시간이 초과됐습니다.' : failure.message);
      }
    } finally {
      clearTimeout(timer);
      if (active.current === controller && (!signal.aborted || timedOut)) {
        active.current = null;
        setBusy(false);
      }
    }
  }

  const selected = rows.filter(row => row.selected);
  return <div className="rv-input-document">
    <DocumentInput documentBusy={busy} documentProgress={progress} documentNotice={notice} documentError={error}
      documentInput={input} selectDocument={select} cancelDocument={cancel} sampleBusy={disabled} projectName={projectName}
      projectId={projectId} setProjectId={setProjectId} />
    {rows.length > 0 && <fieldset className="rv-video-document-rows" disabled={busy || disabled}>
      <legend>문서에서 추출한 요구사항</legend>
      {rows.map((row, index) => <label key={row.text}><input type="checkbox" checked={row.selected} onChange={event => {
        const checked = event.target.checked;
        setRows(previous => previous.map((item, i) => i === index ? { ...item, selected: checked } : item));
      }} />{row.text}</label>)}
      <button type="button" className="rv-button secondary" disabled={!selected.length}
        onClick={() => { if (onApply(selected)) setRows(previous => previous.map(item => ({ ...item, selected: false }))); }}>선택한 항목 추가</button>
    </fieldset>}
  </div>;
}
