import React, { useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Link } from 'react-router';
import { Icon } from '../../components/Icon.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { loadDocumentProjects, createDocumentProject } from './frameworkClient.js';
import { DOCX_MEDIA_TYPE } from './frameworkDocument.js';
import posterExample from '../../assets/sample-poster-requirements.pdf?inline';
import videoExample from '../../assets/sample-video-requirements.pdf?inline';

export function DocumentAnalysisProgress({ documentProgress, totalSeconds, stageSeconds, lastChecked,
  cancelLabel, documentResumeId, kind = 'poster' }) {
  const longRunning = totalSeconds >= 5 * 60 && ['queued', 'extracting', 'formatting'].includes(documentProgress?.stage);
  const formatDuration = value => String(Math.floor(value / 60)).padStart(2, '0') + ':' + String(value % 60).padStart(2, '0');
  return <div className="rv-document-inline" role="status">
    <span className="rv-progress-spinner" aria-hidden="true" />
    <p>{documentProgress.message}</p>
    <progress max={100} value={documentProgress.percent} aria-label="문서 분석 단계" />
    {documentProgress.startedAt && <small role="timer" aria-label="문서 분석 경과 시간">
      총 경과 {formatDuration(totalSeconds)} · 현재 단계 {formatDuration(stageSeconds)}
    </small>}
    {lastChecked && <small className="rv-document-last-checked">마지막 상태 확인: {lastChecked}</small>}
    {kind === 'poster' && documentResumeId && <Link className="text-btn" target="_blank" rel="noopener noreferrer"
      to={'/review/new?framework=' + encodeURIComponent(documentResumeId)}>새 탭에서 분석 상태 확인</Link>}
    {longRunning && <p className="rv-document-long-running">
      분석이 계속 진행 중입니다. 완료 시각은 문서 분량과 서버 대기 순서에 따라 달라 예측하기 어렵습니다.
      {cancelLabel === '대기 작업 취소'
        ? ' 이 버튼은 아직 대기 중인 서버 작업의 취소를 요청합니다.'
        : ' 입력 반영 취소는 화면 연결을 멈춥니다. 접수된 서버 분석은 계속될 수 있습니다.'}
    </p>}
  </div>;
}

function projectDate(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return '';
  return `${String(date.getMonth() + 1).padStart(2, '0')}.${String(date.getDate()).padStart(2, '0')} 수정`;
}

// 저장할 프로젝트 고르기. 버튼에 고른 프로젝트 이름을 그대로 보이고, 목록·검색·새로 만들기를 한 패널에서 한다.
// 고르면 패널이 닫히고, 바깥을 누르거나 Esc로도 닫힌다. 새 프로젝트 이름은 검수 이름으로 미리 채운다.
// ref.createFromName(name): 검수 이름 칸에서 Enter를 누르면 그 이름의 프로젝트를 만들고 선택한다(같은 이름이 있으면 그것을 고른다).
// onPick(project): 사람이 목록에서 고르거나 패널에서 새로 만들어 고른 프로젝트. 화면은 이 이름을 검수 이름에 반영한다
// (목록을 불러올 때 하나뿐이라 저절로 고른 경우는 알리지 않는다).
export function DocumentProjectPicker({ projectId, setProjectId, projectName, disabled, onStatus, onPick, ref }) {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [creating, setCreating] = useState(false);
  const [connected, setConnected] = useState(false);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [draft, setDraft] = useState('');
  const creation = useRef(null);
  const root = useRef(null);
  const trigger = useRef(null);
  const search = useRef(null);
  const nameAtOpen = useRef(projectName);
  nameAtOpen.current = projectName;

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

  // 패널을 열면 검색칸에 초점을 두고, 바깥 누르기·Esc로 닫는다.
  useEffect(() => {
    if (!open) return undefined;
    setQuery('');
    setDraft(nameAtOpen.current?.trim() || '');
    search.current?.focus({ preventScroll: true });
    function outside(event) { if (!root.current?.contains(event.target)) setOpen(false); }
    function escape(event) { if (event.key === 'Escape') { setOpen(false); trigger.current?.focus(); } }
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);

  function choose(project) {
    setProjectId(project.id);
    onPick?.(project);
    setOpen(false);
    trigger.current?.focus();
  }

  async function createProject(nameArg) {
    if (creation.current) return;
    const name = (nameArg ?? draft).trim();
    if (!name) { setError('새 프로젝트 이름을 입력해 주세요.'); return; }
    const controller = new AbortController();
    creation.current = controller;
    setCreating(true);
    setError('');
    try {
      const project = await createDocumentProject(name, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setProjects(current => [project, ...current]);
      choose(project);
      setAnnounce(`‘${name}’ 프로젝트를 만들고 선택했어요.`);
    } catch (failure) {
      if (!controller.signal.aborted) {
        setError(failure.message + ' 목록을 다시 불러와 생성 여부를 확인해 주세요.');
        setOpen(true);  // 검수 이름 칸에서 만들다 실패해도 이유가 보이게 패널을 연다
      }
    } finally {
      creation.current = null;
      if (!controller.signal.aborted) setCreating(false);
    }
  }

  const [announce, setAnnounce] = useState('');
  const unavailable = disabled || loading || creating;
  // 검수 이름 칸의 Enter. 같은 이름의 프로젝트가 이미 있으면 새로 만들지 않고 그 프로젝트를 고른다.
  useImperativeHandle(ref, () => ({
    createFromName(raw) {
      const name = (raw || '').trim();
      if (!name || !connected || unavailable) return;
      const same = projects.find(project => project.name.trim() === name);
      if (same) {
        setProjectId(same.id);
        setAnnounce(`이미 있는 ‘${name}’ 프로젝트를 선택했어요.`);
        return;
      }
      createProject(name);
    },
  }), [connected, unavailable, projects, setProjectId]);
  const selected = projects.find(project => project.id === projectId);
  const state = loading || creating ? 'loading' : !connected ? 'error' : selected ? 'selected' : 'required';
  const words = query.trim().toLowerCase();
  const shown = [...projects].sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))
    .filter(project => project.name.toLowerCase().includes(words));
  return <div className="rv-project-picker" ref={root}>
    <span className="rv-project-field-label">저장할 프로젝트</span>
    <button ref={trigger} type="button" className={`rv-project-chip is-${state}`} aria-haspopup="dialog" aria-expanded={open}
      title={selected?.name} disabled={disabled || loading} onClick={() => setOpen(value => !value)}>
      {state === 'loading' ? <><Icon name="refresh" className="icon rv-project-spin" />
        <span className="rv-project-chip-text">{creating ? '프로젝트 만드는 중…' : '프로젝트 확인 중…'}</span></>
        : state === 'error' ? <><Icon name="warning" /><span className="rv-project-chip-text">목록을 불러오지 못했어요</span></>
        : state === 'selected' ? <><Icon name="check" /><span className="rv-project-chip-text">{selected.name}</span></>
        : <><Icon name="folder" /><span className="rv-project-chip-text">프로젝트 선택</span><span className="rv-project-required">필수</span></>}
      {state !== 'loading' && <Icon name="chevron" className="icon rv-project-chevron" />}
    </button>
    <span className="sr-only" role="status">{announce}</span>
    {open && <div className="rv-project-picker-panel" role="dialog" aria-label="저장할 프로젝트 고르기">
      <div className="rv-project-picker-head"><strong>저장할 프로젝트</strong>
        <button type="button" disabled={unavailable} onClick={() => setReload(value => value + 1)}><Icon name="refresh" />새로고침</button></div>
      {connected && <>
        <label className="rv-project-search"><Icon name="search" />
          <input ref={search} type="search" value={query} placeholder="프로젝트 검색" aria-label="프로젝트 검색" autoComplete="off"
            onChange={event => setQuery(event.target.value)}
            onKeyDown={event => { if (event.key === 'Enter' && shown[0] && !unavailable) { event.preventDefault(); choose(shown[0]); } }} /></label>
        <ul className="rv-project-list" role="listbox" aria-label="프로젝트 목록">
          {shown.map(project => <li key={project.id}>
            <button type="button" role="option" aria-selected={project.id === projectId} disabled={unavailable} onClick={() => choose(project)}>
              <span className="rv-project-list-check">{project.id === projectId && <Icon name="check" />}</span>
              <span className="rv-project-list-name">{project.name}</span>
              <span className="rv-project-list-meta">{projectDate(project.updated_at)}</span>
            </button></li>)}
          {!shown.length && <li className="rv-project-list-empty">{projects.length
            ? `‘${query.trim()}’와 맞는 프로젝트가 없어요. 아래에서 새로 만들 수 있어요.` : '아직 프로젝트가 없어요. 아래에서 새로 만들어 주세요.'}</li>}
        </ul>
        <div className="rv-project-create">
          <label htmlFor="document-project-new">새 프로젝트</label>
          <div><input id="document-project-new" type="text" maxLength={60} value={draft} placeholder="프로젝트 이름" autoComplete="off" disabled={unavailable}
            onChange={event => setDraft(event.target.value)}
            onKeyDown={event => {
              if (event.key !== 'Enter' || event.nativeEvent.isComposing || event.keyCode === 229) return;
              event.preventDefault(); createProject();
            }} />
            <button type="button" className="rv-button primary" disabled={unavailable || !draft.trim()} onClick={() => createProject()}>
              {creating ? '만드는 중…' : '만들고 선택'}</button></div>
        </div>
      </>}
      <ErrorMessage message={error} />
    </div>}
  </div>;
}

export default function DocumentInput({ documentBusy, documentProgress, documentNotice, documentError, documentResumeId, documentInput,
  selectDocument, cancelDocument, cancelLabel = '입력 반영 취소', cancelBusy, documentName, clearDocument, disabled, kind = 'poster', children }) {
  const [now, setNow] = useState(Date.now);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const startedAt = documentProgress?.startedAt;
  const requestStartedAt = documentProgress?.requestStartedAt || startedAt;
  const lastCheckedAt = documentProgress?.lastCheckedAt;
  useEffect(() => {
    if (!documentBusy || !startedAt) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [documentBusy, startedAt, requestStartedAt]);

  const seconds = startedAt ? Math.max(0, Math.floor((now - startedAt) / 1000)) : 0;
  const totalSeconds = requestStartedAt ? Math.max(0, Math.floor((now - requestStartedAt) / 1000)) : seconds;
  const statusAge = lastCheckedAt ? Math.max(0, Math.floor((now - lastCheckedAt) / 1000)) : null;
  const lastChecked = statusAge === null ? '' : statusAge < 3 ? '방금' : statusAge < 60 ? `${statusAge}초 전`
    : `${Math.floor(statusAge / 60)}분 전`;
  const off = documentBusy || disabled;
  const dropActive = dragging && !off;
  const filename = documentProgress?.source || documentName;
  const uploadHint = off ? 'PDF·DOCX 1개 첨부' : 'PDF·DOCX 1개를 끌어 놓거나 파일을 선택하세요.';

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
        <span className="rv-upload-name">{dropActive ? '여기에 PDF·DOCX를 놓으세요.' : filename || uploadHint}</span>
        <input ref={documentInput} type="file" className="sr-only" aria-label="요구사항 문서 선택" disabled={off}
          accept={`application/pdf,.pdf,.docx,${DOCX_MEDIA_TYPE}`}
          onChange={event => { if (event.target.files.length) selectDocument([...event.target.files]); }} />
        <button type="button" className="rv-button secondary rv-compact" disabled={off} onClick={() => documentInput.current?.click()}>
          <Icon name="file" />{filename ? '바꾸기' : '파일 선택'}
        </button>
      </div>
      {documentBusy && documentProgress && <DocumentAnalysisProgress {...{ documentProgress, totalSeconds, lastChecked, cancelLabel,
        documentResumeId, kind }} stageSeconds={seconds} />}
      <div className="rv-form-row rv-form-hint">
        <span>{!documentBusy && documentNotice ? documentNotice : 'PDF 텍스트층 · DOCX 본문 · 최대 10MiB'}</span>
        {documentBusy ? <button type="button" className="rv-button secondary rv-compact" disabled={cancelBusy} onClick={cancelDocument}>{cancelLabel}</button>
          : documentName && clearDocument ? <button type="button" className="rv-button secondary rv-compact" onClick={clearDocument}>첨부 지우기</button>
            : <button type="button" className="rv-button secondary rv-compact" disabled={off} onClick={loadExample}>추출 예시 보기</button>}
      </div>
    </div>
    {!documentBusy && <p className="rv-form-hint">DOCX는 본문 글자만 읽습니다. 이미지와 배치는 별도로 보관한 Word 원본에서 확인해 주세요.</p>}
    {!documentBusy && documentResumeId && kind === 'poster' && <Link className="text-btn" to={'/review/new?framework=' + encodeURIComponent(documentResumeId)}>저장된 문서 분석 보기</Link>}
    <ErrorMessage message={documentError} />
    {children}
  </section>;
}
