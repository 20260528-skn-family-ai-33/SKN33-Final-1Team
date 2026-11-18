import React, { useEffect, useRef, useState } from 'react';
import DocumentInput from '../review/DocumentInput.jsx';
import { mergeDocumentRequirements } from '../review/documentRequirements.js';
import { extractDocumentWithProgress, requirementDocument, watchDocumentDeadline } from '../review/frameworkDocument.js';
import { extractVideoDocument } from './videoDocumentAnalysis.js';

// modelRoute: 보안 여부(일반 자료 openai, 보안 자료 runpod). 동영상 전용 분석과 A02 분석 모두에 실어 보낸다.
// projectBlock: 저장할 프로젝트가 준비되지 않은 이유(꺼진 '파일 선택'에 마우스를 올리면 보인다).
// dedicated: 서버가 영상 전용 문서 분석(/api/video-reviews/requirements)을 제공하면 true. 이때는 항상 영상 전용 분석을 쓴다.
export default function VideoDocumentInput({ projectId, projectReady, projectBlock, documentName, disabled, busy, setBusy, onDocument, dedicated = false,
  modelRoute = 'default' }) {
  const [progress, setProgress] = useState(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [warnings, setWarnings] = useState([]);
  const [inspectId, setInspectId] = useState('');
  const [excluded, setExcluded] = useState([]);
  // 영상 요구사항은 포스터 A02 프롬프트로 읽으면 0개로 끝날 수 있어, 서버가 제공하면 영상 전용 분석을 쓴다(2026-11-16).
  // 고르는 체크박스는 2026-11-18에 뺐다. 서버가 제공하지 않으면(dedicated=false) 포스터와 같은 A02 문서 분석을 쓴다.
  const dedicatedEnabled = dedicated;
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
      setError('내용이 있는 10MiB 이하 PDF·DOCX 문서 1개를 선택해 주세요.');
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
        const result = await extractVideoDocument(file, { signal, modelRoute,
          onProgress: update => { if (!signal.aborted) setProgress(deadline.update(update)); } });
        if (signal.aborted) return;
        const rows = result.items.filter(item => item.include).map(item => ({
          // 포스터처럼 추출한 항목은 처음부터 모두 검수에 포함한다(뺄 항목만 검수 항목 상자에서 체크를 푼다).
          id: item.draft_id, text: item.text, type: item.type, source: item.source.quote, selected: true,
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
      const result = await extractDocumentWithProgress(document, { projectId, attempts: attempts.current, signal, modelRoute,
        onAccepted: id => { deadline.runId = id; },
        onProgress: update => { if (!signal.aborted) setProgress(deadline.update(update)); } });
      if (signal.aborted) return;
      const { sources } = mergeDocumentRequirements('', result, file.name);
      // 추출한 항목은 처음부터 모두 검수에 포함한다(포스터와 같다). 뺄 항목만 화면에서 체크를 푼다.
      onDocument(file.name, sources.map((item, index) => ({ ...item, id: `document-${index}`, selected: true })));
      setProgress({ startedAt, stage: 'completed', percent: 100, message: '요구사항 추출을 완료했습니다.' });
      setWarnings(result.warnings || []);
      if (result.warnings?.length) setInspectId(result.framework.run_id);
      setNotice(`추출 완료 · ${/\.docx$/i.test(file.name) ? 'Word 문서' : `${result.page_count}페이지`} · 조건 ${sources.length}개`);
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

  return <DocumentInput kind="video" documentBusy={busy} documentProgress={progress} documentNotice={notice && { summary: notice, warnings }} documentError={error}
    documentResumeId={inspectId}
    documentInput={input} selectDocument={select} cancelDocument={cancel} documentName={pendingName || documentName}
    clearDocument={disabled ? undefined : clearDocument} disabled={disabled || !projectReady || !projectId}
    disabledReason={disabled ? '동영상을 접수하는 중이에요. 잠시 뒤 다시 눌러 주세요.'
      : projectBlock || (!projectId ? '먼저 위쪽 「저장할 프로젝트」에서 프로젝트를 고르거나 만들어 주세요.' : undefined)}>
    {!projectId && <p className="rv-form-hint">상단에서 저장할 프로젝트를 선택하거나 만들어 주세요.</p>}
    {excluded.length > 0 && <details><summary>기준에서 제외된 항목 {excluded.length}개</summary>
      <ul>{excluded.map(item => <li key={item.id}><p>{item.reason}</p>
        {item.source && <blockquote>{item.source.quote}</blockquote>}</li>)}</ul>
    </details>}
  </DocumentInput>;
}
