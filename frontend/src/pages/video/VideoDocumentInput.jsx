import React, { useEffect, useRef, useState } from 'react';
import DocumentInput from '../review/DocumentInput.jsx';
import { mergeDocumentRequirements } from '../review/documentRequirements.js';
import { DOCUMENT_WAIT_TIMEOUT_MS, extractDocumentWithProgress, requirementDocument } from '../review/frameworkDocument.js';

export default function VideoDocumentInput({ projectId, projectReady, documentName, disabled, busy, setBusy, onDocument }) {
  const [progress, setProgress] = useState(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [pendingName, setPendingName] = useState('');
  const input = useRef(null);
  const active = useRef(null);
  const attempts = useRef(new Map());
  useEffect(() => () => {
    if (active.current) {
      active.current.abort();
      active.current = null;
      setBusy(false);
    }
  }, [setBusy]);

  function cancel() {
    active.current?.abort();
    active.current = null;
    setBusy(false);
    setProgress(null);
    setPendingName('');
  }

  function clearDocument() {
    setError('');
    setNotice('');
    setProgress(null);
    onDocument('', []);
  }

  async function select(files) {
    if (active.current || disabled || !projectReady || !projectId) return;
    if (input.current) input.current.value = '';
    const [file] = files;
    if (files.length !== 1 || !file.size || file.size > 10 * 1024 * 1024) {
      setError('내용이 있는 10MiB 이하 PDF 또는 Word(.docx) 문서 1개를 선택해 주세요.');
      return;
    }
    const controller = new AbortController();
    const { signal } = controller;
    const startedAt = Date.now();
    active.current = controller;
    setBusy(true);
    setError('');
    setNotice('');
    setPendingName(file.name);
    setProgress({ startedAt, stage: 'preparing', percent: 0, message: '문서를 확인하고 있습니다.' });
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, DOCUMENT_WAIT_TIMEOUT_MS);
    try {
      const document = await requirementDocument(file);
      const result = await extractDocumentWithProgress(document, { projectId, attempts: attempts.current, signal,
        onProgress: update => { if (!signal.aborted) setProgress({ ...update, startedAt }); } });
      if (signal.aborted) return;
      const { sources } = mergeDocumentRequirements('', result, file.name);
      onDocument(file.name, sources.map((item, index) => ({ ...item, id: `document-${index}`, selected: false })));
      setProgress({ startedAt, stage: 'completed', percent: 100, message: '요구사항 추출을 완료했습니다.' });
      setNotice(`${file.name} · ${sources.length}개 추출${result.pending?.length ? ` · 미확정 ${result.pending.length}개` : ''}`);
    } catch (failure) {
      if (active.current === controller && (!signal.aborted || timedOut)) {
        setProgress(null);
        setError(timedOut ? '문서 분석 대기 시간이 15분을 넘었습니다. 접수된 분석은 내 작업에서 확인할 수 있어요.' : failure.message);
      }
    } finally {
      clearTimeout(timer);
      if (active.current === controller && (!signal.aborted || timedOut)) {
        active.current = null;
        setPendingName('');
        setBusy(false);
      }
    }
  }

  return <DocumentInput kind="video" documentBusy={busy} documentProgress={progress} documentNotice={notice} documentError={error}
    documentInput={input} selectDocument={select} cancelDocument={cancel} documentName={pendingName || documentName}
    clearDocument={disabled ? undefined : clearDocument} disabled={disabled || !projectReady || !projectId}>
    {!projectId && <p className="rv-form-hint">상단에서 저장할 프로젝트를 선택하거나 만들어 주세요.</p>}
  </DocumentInput>;
}
