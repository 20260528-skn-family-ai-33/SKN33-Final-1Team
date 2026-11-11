import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { Icon } from '../../components/Icon.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { loadDocumentProjects, createDocumentProject } from './frameworkClient.js';
import posterExample from '../../assets/sample-poster-requirements.pdf?inline';
import videoExample from '../../assets/sample-video-requirements.pdf?inline';

export function DocumentProjectPicker({ projectId, setProjectId, projectName, disabled, onStatus }) {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [creating, setCreating] = useState(false);
  const [connected, setConnected] = useState(false);
  const creation = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setConnected(false);
    loadDocumentProjects({ signal: controller.signal }).then(items => {
      if (controller.signal.aborted) return;
      setProjects(items);
      setConnected(true);
      setProjectId(current => items.some(item => item.id === current) ? current : items.length === 1 ? items[0].id : '');
    }).catch(failure => {
      if (!controller.signal.aborted) setError(failure.status === 401
        ? '검수 서버에 같은 PostgreSQL 계정으로 로그인해야 합니다.' : failure.message);
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [reload, setProjectId]);

  useEffect(() => () => creation.current?.abort(), []);
  useEffect(() => {
    onStatus({ ready: connected && !loading && !creating && !!projectId, busy: loading || creating });
  }, [connected, projectId, loading, creating, onStatus]);

  async function createProject() {
    if (creation.current) return;
    const name = projectName?.trim();
    if (!name) { setError('프로젝트 이름을 입력해 주세요.'); return; }
    const controller = new AbortController();
    creation.current = controller;
    setCreating(true);
    setError('');
    try {
      const project = await createDocumentProject(name, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setProjects(current => [...current, project]);
      setProjectId(project.id);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message + ' 목록을 다시 불러와 생성 여부를 확인해 주세요.');
    } finally {
      creation.current = null;
      if (!controller.signal.aborted) setCreating(false);
    }
  }

  const unavailable = disabled || loading || creating;
  const selected = projects.find(project => project.id === projectId);
  const needsProject = connected && !unavailable && !selected;
  return <details className={`rv-project-picker${needsProject ? ' is-required' : ''}`}>
    <summary title={selected?.name}>{loading ? '프로젝트 확인 중' : selected ? '저장 프로젝트' : '저장할 프로젝트 선택'}</summary>
    <div className="rv-project-picker-panel">
      <label htmlFor="document-project">요구사항을 저장할 프로젝트</label>
      <select id="document-project" value={projectId} onChange={event => setProjectId(event.target.value)} disabled={unavailable}>
        <option value="">프로젝트 선택</option>
        {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
      </select>
      <div className="rv-project-picker-actions">
        <button type="button" className="text-btn" disabled={unavailable} onClick={() => setReload(value => value + 1)}>목록 새로고침</button>
        <button type="button" className="text-btn" disabled={unavailable || !connected} onClick={createProject}>
          {creating ? '만드는 중…' : '위 이름으로 프로젝트 만들기'}
        </button>
      </div>
      <ErrorMessage message={error} />
    </div>
  </details>;
}

export default function DocumentInput({ documentBusy, documentProgress, documentNotice, documentError, documentInput,
  selectDocument, cancelDocument, cancelLabel = '입력 반영 취소', cancelBusy, documentName, clearDocument, disabled, kind = 'poster', children }) {
  const [now, setNow] = useState(Date.now);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const startedAt = documentProgress?.startedAt;
  useEffect(() => {
    if (!documentBusy || !startedAt) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [documentBusy, startedAt]);

  const seconds = startedAt ? Math.max(0, Math.floor((now - startedAt) / 1000)) : 0;
  const elapsed = Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
  const off = documentBusy || disabled;
  const dropActive = dragging && !off;
  const filename = documentProgress?.source || documentName;
  const uploadHint = off ? 'PDF 1개 첨부' : 'PDF 1개를 끌어 놓거나 파일을 선택하세요.';

  function handleDragEnter(event) {
    if (!event.dataTransfer.types.includes('Files')) return;
    dragDepth.current += 1;
    setDragging(true);
  }

  function handleDragOver(event) {
    if (!event.dataTransfer.types.includes('Files')) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = off ? 'none' : 'copy';
  }

  function handleDragLeave() {
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragging(false);
  }

  function handleDrop(event) {
    const files = Array.from(event.dataTransfer.files);
    if (!files.length && !event.dataTransfer.types.includes('Files')) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepth.current = 0;
    setDragging(false);
    if (!off && files.length) selectDocument(files);
  }

  function loadExample() {
    const source = kind === 'video' ? videoExample : posterExample;
    const bytes = Uint8Array.from(atob(source.split(',')[1]), character => character.charCodeAt(0));
    selectDocument([new File([bytes], '빛의 기록_요구사항.pdf', { type: 'application/pdf' })]);
  }
  return <section className={`rv-form-block rv-document-card${dropActive ? ' is-dragging' : ''}`}
    aria-label="요구사항 문서 첨부" aria-busy={documentBusy || undefined}
    onDragEnter={handleDragEnter} onDragOver={handleDragOver} onDragLeave={handleDragLeave} onDrop={handleDrop}>
    <h2>요구사항 문서 첨부</h2>
    <div className="rv-form-upload">
      <div className="rv-form-row">
        <span className="rv-upload-name">{dropActive ? '여기에 PDF를 놓으세요.' : filename || uploadHint}</span>
        <input ref={documentInput} type="file" className="sr-only" accept="application/pdf,.pdf" aria-label="요구사항 PDF 선택" disabled={off}
          onChange={event => { if (event.target.files.length) selectDocument([...event.target.files]); }} />
        <button type="button" className="rv-button secondary rv-compact" disabled={off} onClick={() => documentInput.current?.click()}>
          <Icon name="file" />{filename ? '바꾸기' : '파일 선택'}
        </button>
      </div>
      {documentBusy && documentProgress && <div className="rv-document-inline" role="status">
        <span className="rv-progress-spinner" aria-hidden="true" />
        <p>{documentProgress.message}</p>
        <progress max={100} value={documentProgress.percent} aria-label="문서 분석 단계" />
        {startedAt && <small role="timer" aria-label="경과 시간">경과 {elapsed} · 처리 단계 기준</small>}
      </div>}
      <div className="rv-form-row rv-form-hint">
        <span>{!documentBusy && documentNotice ? documentNotice : '텍스트 PDF · 최대 10MiB / 50페이지'}</span>
        {documentBusy ? <button type="button" className="rv-button secondary rv-compact" disabled={cancelBusy} onClick={cancelDocument}>{cancelLabel}</button>
          : documentName && clearDocument ? <button type="button" className="rv-button secondary rv-compact" onClick={clearDocument}>첨부 지우기</button>
            : <button type="button" className="rv-button secondary rv-compact" disabled={off} onClick={loadExample}>추출 예시 보기</button>}
      </div>
    </div>
    {kind === 'poster' && !off && <Link className="text-btn" to="/review/tasks#pdf-batches">PDF 여러 개 올리기</Link>}
    <ErrorMessage message={documentError} />
    {children}
  </section>;
}
