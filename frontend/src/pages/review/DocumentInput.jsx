import React, { useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Link } from 'react-router';
import { Icon } from '../../components/Icon.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { DocumentArt } from '../../components/PickerArt.jsx';
import { loadDocumentProjects, createDocumentProject } from './frameworkClient.js';
import { DOCX_MEDIA_TYPE } from './frameworkDocument.js';
import { formatClock } from './elapsedClock.js';
import { documentSteps } from './progressScan.js';
import posterExample from '../../assets/sample-poster-requirements.pdf?inline';
import videoExample from '../../assets/sample-video-requirements.pdf?inline';

const DOCX_TEXT_ONLY = 'Word 본문 글자만 읽었어요. 이미지와 배치는 Word 원본에서 확인해 주세요.';

// 서버 경고는 '무슨 일인지. 무엇을 하면 되는지.' 두 문장이다(backend requirements_presentation.py). 첫 문장을 앞에, 나머지를 작게 보인다.
function splitWarning(text) {
  const value = String(text);
  const at = value.search(/[.!?]\s/);
  return at < 0 ? [value, ''] : [value.slice(0, at + 1), value.slice(at + 2)];
}

// 분석하는 동안 문서를 읽는 3D 그림(components/PickerArt.jsx의 DocumentArt)과 단계 진행률을 보여 준다.
// 분석 상태는 이 카드에서 자동으로 확인하므로 새 탭으로 여는 링크는 두지 않는다.
// '18초', '2분 05초'처럼 읽는 그대로 적는다.
export function formatElapsed(value) {
  const seconds = Math.max(0, Math.floor(value || 0));
  return seconds < 60 ? `${seconds}초` : `${Math.floor(seconds / 60)}분 ${String(seconds % 60).padStart(2, '0')}초`;
}

// 상태는 1.5초마다 확인한다. 30초 넘게 응답이 없을 때만 기다리는 중이라고 알린다.
const STALE_CHECK_SECONDS = 30;

// 경과 시간은 처음 접수부터 센 값 하나만, 포스터 검수 진행 화면과 같은 '경과 시간 0:44' 모양으로 보인다.
// 단계별 시간과 '마지막 상태 확인'은 평소에 보이지 않는다.
// 진행 표시(2026-11-17): 한 줄 막대 대신 세 단계 선(원문 저장 · 요구사항 추출 · 기준 정리, progressScan.js의 documentSteps)으로 보인다.
// 서버는 단계만 알려 주고 단계 안의 진행률은 알려 주지 않으므로, 끝난 칸은 채우고 진행 중인 칸은 잉크 줄이 흐르게만 한다.
// 낭독기에는 예전과 같은 진행 막대 요소(progress '문서 분석 단계', 화면에서는 숨김)로 읽힌다.
export function DocumentAnalysisProgress({ documentProgress, totalSeconds, lastCheckedSeconds = null, cancelLabel }) {
  const longRunning = totalSeconds >= 5 * 60 && ['queued', 'extracting', 'formatting'].includes(documentProgress?.stage);
  const stale = lastCheckedSeconds !== null && lastCheckedSeconds >= STALE_CHECK_SECONDS;
  const steps = documentSteps(documentProgress.stage);
  const current = steps.find(step => step.status === 'active' || step.status === 'waiting');
  return <div className="rv-document-inline" role="status">
    <span className="rv-document-art" aria-hidden="true"><DocumentArt stage={documentProgress.stage} /></span>
    <p>{documentProgress.message}</p>
    {/* 낭독기와 화면 테스트는 예전과 같은 진행 막대 요소를 읽는다. 눈에 보이는 것은 아래 세 단계 선이다. */}
    <progress className="sr-only" max={100} value={documentProgress.percent} aria-label="문서 분석 단계"
      aria-valuetext={current ? `${current.label} ${current.status === 'waiting' ? '대기 중' : '진행 중'}` : undefined} />
    <div className="rv-document-track" aria-hidden="true">
      {steps.map((step, index) => <span key={step.key} className={`rv-document-step is-${step.status}`}>
        <i /><span><b>{String(index + 1).padStart(2, '0')}</b>{step.short}</span></span>)}
    </div>
    {documentProgress.startedAt && <dl className="rv-document-elapsed"><div><dt>경과 시간</dt>
      <dd role="timer" aria-live="off" aria-label="문서 분석 경과 시간"><time dateTime={`PT${totalSeconds}S`}>{formatClock(totalSeconds)}</time></dd>
    </div></dl>}
    {stale && <small className="rv-document-last-checked">서버 응답을 {formatElapsed(lastCheckedSeconds)}째 기다리고 있어요.</small>}
    {longRunning && <p className="rv-document-long-running">
      5분 넘게 분석하고 있어요. 문서 분량과 서버 대기 순서에 따라 더 걸릴 수 있어요.
      {cancelLabel === '대기 작업 취소'
        ? ' 취소하면 아직 시작하지 않은 서버 작업을 멈춥니다.'
        : ' 입력 반영 취소는 화면 연결만 멈추고, 서버 분석은 계속될 수 있어요.'}
    </p>}
  </div>;
}

function projectDate(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return '';
  return `${String(date.getMonth() + 1).padStart(2, '0')}.${String(date.getDate()).padStart(2, '0')} 수정`;
}

// 저장할 프로젝트 고르기. 버튼에 고른 프로젝트 이름을 그대로 보이고, 패널에서는 목록과 검색으로 고르기만 한다.
// 고르면 패널이 닫히고, 바깥을 누르거나 Esc로도 닫힌다. 패널 안의 '새 프로젝트 · 만들고 선택' 칸은 없앴다(2026-11-17).
// ref.createFromName(name): 검수 이름 칸에서 Enter를 누르면 그 이름의 프로젝트를 만들고 선택한다(같은 이름이 있으면 그것을 고른다).
// 새 프로젝트를 만드는 길은 이것 하나다.
// onPick(project): 사람이 목록에서 고르거나 검수 이름 칸의 Enter로 새로 만들어 고른 프로젝트. 화면은 이 이름을 검수 이름에 반영한다
// (목록을 불러올 때 하나뿐이라 저절로 고른 경우는 알리지 않는다).
// 저장할 프로젝트가 준비되지 않아 문서를 첨부할 수 없는 이유. 꺼진 '파일 선택'에 마우스를 올리면 보인다.
// status: DocumentProjectPicker의 onStatus 값({ ready, busy, failed }).
export function projectBlockReason(status) {
  if (status?.ready) return '';
  if (status?.busy) return '저장할 프로젝트를 확인하는 중이에요. 잠시 뒤 다시 눌러 주세요.';
  if (status?.failed) return '저장할 프로젝트 목록을 불러오지 못해 문서를 첨부할 수 없어요. 프로젝트 칸을 열어 새로고침해 주세요.';
  return '먼저 위쪽 「저장할 프로젝트」에서 프로젝트를 고르거나 만들어 주세요.';
}

export function DocumentProjectPicker({ projectId, setProjectId, disabled, onStatus, onPick, ref }) {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [creating, setCreating] = useState(false);
  const [connected, setConnected] = useState(false);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const creation = useRef(null);
  const root = useRef(null);
  const trigger = useRef(null);
  const search = useRef(null);

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
        ? '검수 서버에 같은 계정으로 로그인해야 합니다.' : failure.message);
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [reload, setProjectId]);

  useEffect(() => () => creation.current?.abort(), []);
  useEffect(() => {
    onStatus({ ready: connected && !loading && !creating && !!projectId, busy: loading || creating, failed: !connected && !loading });
  }, [connected, projectId, loading, creating, onStatus]);

  // 패널을 열면 검색칸에 초점을 두고, 바깥 누르기·Esc로 닫는다.
  useEffect(() => {
    if (!open) return undefined;
    setQuery('');
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
    const name = (nameArg || '').trim();
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
    <span className="rv-project-field-badge">저장할 프로젝트</span>
    {/* 이름표가 단추 왼쪽에 붙어 있어, 고르기 창은 이 묶음을 기준으로 단추 바로 아래에 연다. */}
    <div className="rv-project-picker-anchor">
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
      <div className="rv-project-picker-head"><strong>저장할 프로젝트{connected && <span className="rv-project-picker-count" aria-label={`${projects.length}개`}>{projects.length}</span>}</strong>
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
            ? `‘${query.trim()}’와 맞는 프로젝트가 없어요.` : '아직 프로젝트가 없어요.'}</li>}
        </ul>
        {/* 이 창에서는 고르기만 한다. 새 프로젝트는 프로젝트 명 칸의 Enter로 만든다(createFromName). */}
        <p className="rv-project-picker-hint">새로 만들려면 <b>프로젝트 명</b> 칸에 이름을 적고 <kbd>Enter</kbd></p>
      </>}
      <ErrorMessage message={error} />
    </div>}
    </div>
  </div>;
}

export default function DocumentInput({ documentBusy, documentProgress, documentNotice, documentError, documentResumeId, documentInput,
  selectDocument, cancelDocument, cancelLabel = '입력 반영 취소', cancelBusy, documentName, clearDocument, disabled, disabledReason, kind = 'poster', children }) {
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
  const off = documentBusy || disabled;
  // 꺼진 '파일 선택'·'추출 예시 보기'에 마우스를 올리면 누를 수 없는 이유를 보인다.
  const offReason = !off ? undefined : documentBusy ? '문서를 분석하는 중이에요. 끝난 뒤 다시 첨부할 수 있어요.' : disabledReason || undefined;
  const dropActive = dragging && !off;
  const filename = documentProgress?.source || documentName;
  const uploadHint = off ? 'PDF·DOCX 1개 첨부' : 'PDF·DOCX 1개를 끌어 놓거나 파일을 선택하세요.';
  // documentNotice: 상태 안내 글자이거나, 추출을 마친 뒤의 { summary, warnings }다.
  const notice = !documentBusy && documentNotice ? (typeof documentNotice === 'string' ? { status: documentNotice } : documentNotice) : null;
  const warnings = notice?.summary ? [...(notice.warnings || []), ...(/\.docx$/i.test(filename || '') ? [DOCX_TEXT_ONLY] : [])] : [];
  const canClear = !documentBusy && Boolean(documentName && clearDocument);

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
        <div className="rv-upload-actions">
          <button type="button" className="rv-button secondary rv-compact" disabled={off} title={offReason} onClick={() => documentInput.current?.click()}>
            <Icon name="file" />{filename ? '바꾸기' : '파일 선택'}
          </button>
          {canClear && <button type="button" className="rv-button secondary rv-compact rv-upload-clear" aria-label="첨부 지우기" onClick={clearDocument}>지우기</button>}
        </div>
      </div>
      {documentBusy && documentProgress && <DocumentAnalysisProgress {...{ documentProgress, totalSeconds, cancelLabel }}
        lastCheckedSeconds={statusAge} />}
      {notice?.summary ? <p className="rv-document-summary"><Icon name="check" />{notice.summary}</p>
        : <div className="rv-form-row rv-form-hint">
          <span>{notice?.status || 'PDF 텍스트층 · DOCX 본문 · 최대 10MB'}</span>
          {documentBusy ? <button type="button" className="rv-button secondary rv-compact" disabled={cancelBusy} onClick={cancelDocument}>{cancelLabel}</button>
            : !canClear && <button type="button" className="rv-button secondary rv-compact" disabled={off} title={offReason} onClick={loadExample}>추출 예시 보기</button>}
        </div>}
      {warnings.length > 0 && <div className="rv-document-warnings">
        <p id="rv-document-warnings-title">확인할 점 {warnings.length}</p>
        <ul aria-labelledby="rv-document-warnings-title">{warnings.map((text, index) => {
          const [title, hint] = splitWarning(text);
          return <li key={index}><Icon name="warning" /><span>{title}{hint && <small>{hint}</small>}</span></li>;
        })}</ul>
      </div>}
    </div>
    {!documentBusy && documentResumeId && kind === 'poster' && <Link className="text-btn" to={'/review/new?framework=' + encodeURIComponent(documentResumeId)}>저장된 문서 분석 보기</Link>}
    <ErrorMessage message={documentError} />
    {children}
  </section>;
}
