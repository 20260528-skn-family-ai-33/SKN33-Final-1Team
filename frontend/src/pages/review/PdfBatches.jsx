import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { loadDocumentProjects } from './frameworkClient.js';
import { submitDocument } from './frameworkDocument.js';
import { checkPdfBatch, isActivePdf, maxBatchFiles, readPdfBatches, savePdfBatch, updatePdfBatch } from './pdfBatches.js';

const statusLabels = {
  queued: '대기', extracting: '분석 중', ready: '기준 확인 필요', failed: '분석 실패',
  cancelled: '취소됨', needs_check: '결과 확인 필요',
};

function BatchCard({ batch, uploading, markRead }) {
  const accepted = batch.items.filter(item => item.runId);
  const finished = accepted.filter(item => !isActivePdf(item)).length;
  const totals = [['ready', '기준 확인'], ['failed', '실패'], ['needs_check', '결과 확인'], ['cancelled', '취소']]
    .map(([state, label]) => [label, accepted.filter(item => item.state === state).length]);
  const results = totals.filter(([, count]) => count).map(([label, count]) => label + ' ' + count + '건').join(' · ');
  const unconfirmed = batch.items.length - accepted.length;
  return <article className="rv-batch-card">
    <strong>{batch.projectName} · PDF {batch.items.length}개</strong>
    {!uploading && accepted.length > 0 && finished === accepted.length && !batch.read && (
      <div className="rv-batch-notification">
        <p role="status">PDF {finished}건 처리 종료 · {results}</p>
        <button type="button" className="rv-button secondary" onClick={markRead}>알림 확인</button>
      </div>
    )}
    <p>{uploading ? '서버 접수 중' : '접수 ' + accepted.length + '건'}
      {accepted.length > 0 && ' · 처리 ' + finished + '/' + accepted.length + '건'}
      {!uploading && unconfirmed > 0 && ' · 접수 미확인 ' + unconfirmed + '건'}
    </p>
    {accepted.length > 0 && <progress aria-label="PDF 묶음 처리 진행률" value={finished} max={accepted.length} />}
    <ul className="rv-batch-files">
      {batch.items.map((item, index) => <li key={index}>
        <span>{item.name}</span>
        <span>{statusLabels[item.state] || (uploading ? '접수 대기' : '접수 미확인')}</span>
        {item.error && <small>{item.error}</small>}
        {item.runId && <Link className="rv-button secondary"
          to={'/review/new?framework=' + encodeURIComponent(item.runId)} state={{ projectName: batch.projectName }}
          aria-label={item.name + ' 결과 열기'}>열기</Link>}
      </li>)}
    </ul>
  </article>;
}

export default function PdfBatches({ userId, onAccepted }) {
  const [batches, setBatches] = useState([]);
  const [showForm, setShowForm] = useState(false);
  const [projects, setProjects] = useState(null);
  const [projectId, setProjectId] = useState('');
  const [uploading, setUploading] = useState(null);
  const [error, setError] = useState('');
  const [readError, setReadError] = useState('');
  const submission = useRef(null);
  const active = batches.some(batch => batch.items.some(isActivePdf));

  useEffect(() => {
    function refresh() {
      try { setBatches(readPdfBatches(userId)); }
      catch { setReadError('이 브라우저에서 PDF 묶음 기록을 불러오지 못했어요.'); }
    }
    refresh();
    window.addEventListener('storage', refresh);
    return () => {
      window.removeEventListener('storage', refresh);
      submission.current?.abort();
    };
  }, [userId]);

  useEffect(() => {
    if (!showForm) return;
    const controller = new AbortController();
    setProjects(null);
    loadDocumentProjects({ signal: controller.signal }).then(items => {
      if (controller.signal.aborted) return;
      setProjects(items);
      setProjectId(current => items.some(item => item.id === current) ? current : (items.length === 1 ? items[0].id : ''));
    }).catch(failure => {
      if (!controller.signal.aborted) setError(failure.message);
    });
    return () => controller.abort();
  }, [showForm]);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let timer;
    async function refresh() {
      try {
        let succeeded = true;
        for (const batch of readPdfBatches(userId)) {
          if (!batch.items.some(isActivePdf)) continue;
          if (!await checkPdfBatch(userId, batch, controller.signal)) succeeded = false;
        }
        if (controller.signal.aborted) return;
        setBatches(readPdfBatches(userId));
        setReadError(succeeded ? '' : '일부 PDF 상태를 불러오지 못했어요. 잠시 후 다시 확인합니다.');
      } catch {
        if (!controller.signal.aborted) setReadError('PDF 상태를 불러오지 못했어요. 잠시 후 다시 확인합니다.');
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(refresh, 6000);
      }
    }
    refresh();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [active, userId]);

  async function submit(event) {
    event.preventDefault();
    if (submission.current) return;
    const form = event.currentTarget;
    const files = [...form.elements.pdfs.files];
    const project = projects?.find(item => item.id === projectId);
    setError('');
    if (!project || !files.length || files.length > maxBatchFiles) {
      setError('프로젝트를 선택하고 PDF를 1~' + maxBatchFiles + '개 첨부해 주세요.');
      return;
    }
    const controller = new AbortController();
    submission.current = controller;
    try {
      const id = crypto.randomUUID();
      const batch = { id, projectName: project.name, createdAt: Date.now(), items: files.map(file => ({ name: file.name })) };
      savePdfBatch(userId, batch);
      setBatches(readPdfBatches(userId));
      setUploading(id);
      for (const [index, file] of files.entries()) {
        controller.signal.throwIfAborted();
        let result;
        try {
          if (!file.size || file.size > 10 * 1024 * 1024 ||
              await file.slice(0, 5).text() !== '%PDF-') throw new Error('내용이 있는 10MiB 이하 PDF를 선택해 주세요.');
          const pdf = file.type === 'application/pdf' ? file : new File([file], file.name, { type: 'application/pdf' });
          const attempt = await submitDocument(pdf, { projectId, signal: controller.signal });
          result = { runId: attempt.runId, state: 'queued' };
        } catch (failure) {
          controller.signal.throwIfAborted();
          result = { error: failure.message };
        }
        updatePdfBatch(userId, id, current => ({ ...current, items: current.items.map((item, position) =>
          position === index ? { ...item, ...result } : item) }));
        setBatches(readPdfBatches(userId));
      }
      form.elements.pdfs.value = '';
      onAccepted();
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message || 'PDF 접수 기록을 저장하지 못했어요.');
    } finally {
      submission.current = null;
      if (!controller.signal.aborted) setUploading(null);
    }
  }

  function markRead(id) {
    try {
      updatePdfBatch(userId, id, batch => ({ ...batch, read: true }));
      setBatches(readPdfBatches(userId));
    } catch {
      setError('알림 확인 상태를 저장하지 못했어요.');
    }
  }

  return <section id="pdf-batches" className="rv-panel rv-task-panel rv-batches" aria-labelledby="pdf-batches-title">
    <div className="rv-task-toolbar">
      <h2 id="pdf-batches-title">PDF 묶음 분석</h2>
      <button type="button" className="rv-button secondary" aria-expanded={showForm} disabled={!!uploading}
        onClick={() => { setError(''); setShowForm(value => !value); }}>{showForm ? '첨부 닫기' : 'PDF 여러 개 올리기'}</button>
    </div>
    {showForm && <form className="rv-batch-form" onSubmit={submit}>
      <label>저장 프로젝트 <select value={projectId} onChange={event => setProjectId(event.target.value)} disabled={!!uploading || !projects} required>
        <option value="">프로젝트 선택</option>
        {projects?.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
      </select></label>
      <label>분석할 PDF <input name="pdfs" type="file" accept="application/pdf,.pdf" multiple required disabled={!!uploading} /></label>
      <p>한 번에 최대 {maxBatchFiles}개 · 파일당 10MiB 이하. 접수가 끝나면 탭을 닫아도 분석이 계속돼요.</p>
      {projects?.length === 0 && <p>저장할 프로젝트를 먼저 만들어 주세요.</p>}
      <button type="submit" className="rv-button primary" disabled={!projectId || !!uploading || !projects}>
        {uploading ? '접수 중…' : '묶음 분석 접수'}
      </button>
    </form>}
    <ErrorMessage message={error || readError} />
    {batches.map(batch => <BatchCard key={batch.id} batch={batch} uploading={uploading === batch.id}
      markRead={() => markRead(batch.id)} />)}
  </section>;
}
