import React, { useEffect, useRef, useState } from 'react';
import DocumentInput from '../review/DocumentInput.jsx';
import { mergeDocumentRequirements } from '../review/documentRequirements.js';
import { extractDocumentWithProgress, requirementDocument, watchDocumentDeadline } from '../review/frameworkDocument.js';
import { extractVideoDocument } from './videoDocumentAnalysis.js';

export default function VideoDocumentInput({ projectId, projectReady, documentName, disabled, busy, setBusy, onDocument, dedicated = false }) {
  const [progress, setProgress] = useState(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [warnings, setWarnings] = useState([]);
  const [inspectId, setInspectId] = useState('');
  const [excluded, setExcluded] = useState([]);
  const [useDedicated, setUseDedicated] = useState(false);
  const dedicatedEnabled = dedicated && useDedicated;
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
    setWarnings([]);
    setInspectId('');
    setExcluded([]);
    setProgress(null);
    onDocument('', []);
  }

  async function select(files) {
    if (active.current || disabled || !projectReady || !projectId) return;
    if (input.current) input.current.value = '';
    const [file] = files;
    if (files.length !== 1 || !file.size || file.size > 10 * 1024 * 1024) {
      setError('내용이 있는 10MiB 이하 ' + (dedicatedEnabled ? 'PDF·DOCX·TXT' : 'PDF·DOCX') + ' 문서 1개를 선택해 주세요.');
      return;
    }
    const controller = new AbortController();
    const { signal } = controller;
    const startedAt = Date.now();
    active.current = controller;
    setBusy(true);
    setError('');
    setNotice('');
    setWarnings([]);
    setInspectId('');
    setExcluded([]);
    setPendingName(file.name);
    setProgress({ startedAt, stage: 'preparing', percent: 0, message: '문서를 확인하고 있습니다.' });
    const deadline = watchDocumentDeadline(controller);
    try {
      if (dedicatedEnabled) {
        const result = await extractVideoDocument(file, { signal,
          onProgress: update => { if (!signal.aborted) setProgress(deadline.update(update)); } });
        if (signal.aborted) return;
        const rows = result.items.filter(item => item.include).map(item => ({
          id: item.draft_id, text: item.text, type: item.type, source: item.source.quote, selected: false,
        }));
        onDocument(file.name, rows);
        setExcluded([
          ...result.items.filter(item => !item.include).map(item => ({ id: item.draft_id, reason: item.reason, source: item.source })),
          ...(result.rejected_candidates || []).map(item => ({ id: 'rejected-' + item.candidate_index, reason: item.code, source: item.source })),
        ]);
        const imagePages = (result.warnings || []).map(code => /^page_(\d+)_embedded_images_not_read$/.exec(code)?.[1]).filter(Boolean);
        setWarnings(imagePages.length ? [imagePages.join(', ') + '쪽은 읽지 못한 내용이 있습니다. 원본에서 확인해 주세요.'] : []);
        setNotice(file.name + ' · 영상 기준 ' + rows.length + '개');
        return;
      }
      const document = await requirementDocument(file);
      const result = await extractDocumentWithProgress(document, { projectId, attempts: attempts.current, signal,
        onAccepted: id => { deadline.runId = id; },
        onProgress: update => { if (!signal.aborted) setProgress(deadline.update(update)); } });
      if (signal.aborted) return;
      const { sources } = mergeDocumentRequirements('', result, file.name);
      onDocument(file.name, sources.map((item, index) => ({ ...item, id: `document-${index}`, selected: false })));
      setProgress({ startedAt, stage: 'completed', percent: 100, message: '요구사항 추출을 완료했습니다.' });
      setWarnings(result.warnings || []);
      if (result.warnings?.length) setInspectId(result.framework.run_id);
      setNotice(`${file.name} · ${sources.length}개 추출${result.pending?.length ? ` · 미확정 ${result.pending.length}개` : ''}`);
    } catch (failure) {
      if (active.current === controller && (!signal.aborted || deadline.expired)) {
        setProgress(null);
        setInspectId(failure.runId || deadline.runId);
        const waitMessage = dedicatedEnabled ? '화면 대기 시간이 끝났습니다. 접수된 영상 문서 분석은 서버에서 계속될 수 있습니다.'
          : '화면 대기 시간이 끝났습니다. 접수된 분석은 기존 실행에서 확인할 수 있어요.';
        setError(deadline.expired ? waitMessage : failure.message);
      }
    } finally {
      deadline.stop();
      if (active.current === controller && (!signal.aborted || deadline.expired)) {
        active.current = null;
        setPendingName('');
        setBusy(false);
      }
    }
  }

  return <DocumentInput kind="video" documentBusy={busy} documentProgress={progress} documentNotice={notice} documentError={error}
    documentResumeId={inspectId} allowText={dedicatedEnabled}
    documentInput={input} selectDocument={select} cancelDocument={cancel} documentName={pendingName || documentName}
    clearDocument={disabled ? undefined : clearDocument} disabled={disabled || !projectReady || !projectId}>
    {!projectId && <p className="rv-form-hint">상단에서 저장할 프로젝트를 선택하거나 만들어 주세요.</p>}
    {dedicated && <label className="rv-form-hint"><input type="checkbox" checked={useDedicated} disabled={busy || disabled}
      onChange={event => setUseDedicated(event.target.checked)} /> 영상 전용 분석으로 기준과 제외 항목 분류 (문서 원본은 별도 보관)</label>}
    {warnings.length > 0 && <ul className="rv-form-hint" aria-label="문서 분석 확인 사항">{warnings.map((warning, index) =>
      <li key={index}>{warning}</li>)}</ul>}
    {excluded.length > 0 && <details><summary>기준에서 제외된 항목 {excluded.length}개</summary>
      <ul>{excluded.map(item => <li key={item.id}><p>{item.reason}</p>
        {item.source && <blockquote>{item.source.quote}</blockquote>}</li>)}</ul>
    </details>}
  </DocumentInput>;
}
