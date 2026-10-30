import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Brand } from '../../components/Brand.jsx';
import { Icon } from '../../components/Icon.jsx';
import {
  CONTRACT, STAGES, MODEL_OPTIONS, FILE_TYPES, FILE_LIMITS, STATUS_LABELS, TERMINAL, allowedTypes, fileLimit, fileError, formatBytes,
  stagesFor, parseModel, parseEndpoint, validateHealth, validateRun, formErrors,
  makeManifest, makeFormData, podRequest, sshRequest, modeltestRequest, SIGLIP2, SIGLIP2_MODELS, FILTER_SCOPES, SIMILARITY_STAGES, modelOptions, parseStageModel, preparationLabel, stageTitle, workflowPreset,
} from './modeltest.js';
import './modeltest.css';
import { useModeltestDraft } from './modeltestDraft.js';
import { readSSH, writeSSH, connectionTarget } from './modeltestConnection.js';
import { rememberBoard } from './modeltestBoard.js';
import ModeltestResults from './ModeltestResults.jsx';
import ModeltestTimings, { ModelTimingTable } from './ModeltestTimings.jsx';
import ModeltestWorkflow from './ModeltestWorkflow.jsx';
import ModeltestAgents from './ModeltestAgents.jsx';
import ModeltestLibrary from './ModeltestLibrary.jsx';
import ModeltestPrompt from './ModeltestPrompt.jsx';
import ModeltestHelp from './ModeltestHelp.jsx';
import ModeltestConfirm from './ModeltestConfirm.jsx';
import FrameSampling from './FrameSampling.jsx';
import ImageResize from './ImageResize.jsx';
import { registration, modelChoice } from './modeltestCatalog.js';
import { agentTemplate } from './modeltestAgents.js';
import { elapsedSeconds, formatDuration, latestCompletedStage } from './modeltestResults.js';

function usePreview(file) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    if (!file) { setUrl(''); return; }
    const next = URL.createObjectURL(file); setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [file]);
  return url;
}

function FileThumbnail({ file }) {
  const url = usePreview(file); const [failed, setFailed] = useState(false);
  return failed ? <small>브라우저 미리보기 불가</small> : <img className="mt-file-thumbnail" src={url || undefined} alt={`${file.name} 미리보기`} loading="lazy" decoding="async" onError={() => setFailed(true)} />;
}

function Attachments({ kind, title, files, onChange, onClear, required, multiple, disabled, health, maxFiles = 10, thumbnails = false }) {
  const input = useRef(null); const [error, setError] = useState(''); const [dragging, setDragging] = useState(false);
  const types = allowedTypes(kind, health);
  function select(incoming) {
    if (disabled) return;
    const next = Array.from(incoming);
    if (!next.length) return;
    const bad = next.map(file => fileError(file, kind, health)).find(Boolean);
    if (bad) { setError(bad); return; }
    if (!multiple && next.length !== 1) { setError('검수할 파일은 하나만 선택해 주세요.'); return; }
    if (multiple && files.length + next.length > maxFiles) { setError(`최대 ${maxFiles}개까지 첨부할 수 있습니다.`); return; }
    onChange(multiple ? [...files, ...next] : next); setError('');
  }
  return <section className="mt-attachment" aria-label={title}>
    <div className="mt-label"><h3>{title}</h3>{onClear && <button type="button" className="text-btn" aria-label={`${title} 전체 해제`} disabled={disabled || !files.length} onClick={() => onClear(() => { onChange([]); setError(''); })}>전체 해제 ({files.length})</button>}<span>{required ? '필수' : '선택'}</span></div>
    <input ref={input} type="file" className="sr-only" tabIndex={-1} accept={types.join(',')} multiple={multiple} disabled={disabled || !types.length}
      aria-label={title} onChange={event => { select(event.target.files); event.target.value = ''; }} />
    <button type="button" className={`mt-drop ${dragging ? 'is-dragging' : ''}`} disabled={disabled || !types.length}
      onClick={() => input.current.click()} onDragOver={event => { event.preventDefault(); if (!disabled) setDragging(true); }}
      onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); select(event.dataTransfer.files); }}>
      <Icon name={kind === 'document' ? 'file' : 'upload'} /><strong>{files.length ? '파일 추가·변경' : '클릭하거나 파일을 놓으세요'}</strong>
      <small>{types.length ? types.map(ext => ext.slice(1).toUpperCase()).join(', ') : 'Pod에서 지원하는 형식 없음'} · 파일당 {formatBytes(fileLimit(kind, health))}{multiple ? ` · 최대 ${maxFiles}개` : ''}</small>
    </button>
    {files.length > 0 && <ul className={`mt-file-list ${thumbnails ? 'mt-file-gallery' : ''}`} aria-label={thumbnails ? '대조 이미지 미리보기 목록' : undefined}>{files.map((file, index) => <li key={`${file.name}-${file.lastModified}-${index}`} title={thumbnails ? file.name : undefined}>
      {thumbnails && <FileThumbnail file={file} />}
      {!thumbnails && <div><strong title={file.name}>{file.name}</strong><small>{formatBytes(file.size)}</small></div>}
      <button type="button" disabled={disabled} aria-label={`${file.name} 삭제`} title={thumbnails ? "이미지 삭제" : "파일 삭제"} onClick={() => { onChange(files.filter((_, i) => i !== index)); setError(''); }}>×</button>
    </li>)}</ul>}
    {error && <p className="mt-error" role="alert">{error}</p>}
  </section>;
}

function ModelStage({ stage, index, value, update, health, disabled, extraSimilarity, inInspector = false }) {
  let parsed; let invalid = '';
  if (stage.id !== 'candidate_filter' && value.model.trim()) { try { parsed = parseStageModel(stage.id, value, health); } catch (error) { invalid = error.message; } }
  const registered = registration(value.model, health);
  let promptModel = registered?.model_id; try { if (!promptModel) promptModel = parseModel(value.model).model_id; } catch { /* Unfinished model IDs have no prompt editor. */ }
  const unsupported = health && !health.stages.includes(stage.id);
  const models = modelOptions(health).filter(model => model.stages.includes(stage.id));
  const profile = models.find(model => model.id === (registered ? value.model : parsed?.model_id));
  const modelWarning = registered?.reasons?.join(' · ') || (profile && (!MODEL_OPTIONS.some(model => model.id === profile.id) || profile.id === 'PaddlePaddle/PaddleOCR-VL-1.6-GGUF') ? profile.description : '');
  const id = `mt-model-${stage.id}`;
  return <div id={`mt-stage-${stage.id}`} className={`mt-stage ${value.enabled ? '' : 'is-off'} ${inInspector ? 'is-inspector' : ''}`}>
    {!inInspector && <span className="mt-stage-number">{String(index).padStart(2, '0')}</span>}
    <div className="mt-stage-content">
      <div className="mt-stage-heading"><div className="mt-title-help">{inInspector ? <h3>{stage.title}</h3> : <label className="mt-toggle"><input type="checkbox" checked={value.enabled} disabled={disabled}
        onChange={event => update({ ...value, enabled: event.target.checked })} /><span>{stage.title}</span></label>}<ModeltestHelp label={stage.title}>
          <p>{stage.description}</p>
          {stage.id === 'candidate_filter' ? <p>선정 기준별로 최소 유사도를 적용한 뒤 상위 N개를 선택합니다. 조건을 통과하지 못한 쌍은 XFeat에 전달하지 않습니다. 팀원 실험의 프레임별·영상 전체 범위는 별도 확인이 필요합니다.</p>
            : null}
        </ModeltestHelp></div><Icon name={stage.icon} /></div>
      {stage.id === 'candidate_filter' ? <div className="mt-filter-fields">
        <label className="mt-settings-wide">선정 기준<select aria-label="필터 선정 기준" value={value.filterScope || 'references_per_crop'} disabled={disabled || !value.enabled} onChange={event => update({ ...value, filterScope: event.target.value })}>{Object.entries(FILTER_SCOPES).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        <label>최소 코사인 유사도<input aria-label="필터 최소 코사인 유사도" type="number" min={-1} max={1} step="any" value={value.threshold} disabled={disabled || !value.enabled} placeholder="비워두면 제한 없음" onChange={event => update({ ...value, threshold: event.target.value })} /></label>
        <label>상위 후보 수<input aria-label="필터 상위 후보 수" type="number" min={1} max={10} step={1} value={value.topK} disabled={disabled || !value.enabled} placeholder="비워두면 모두" onChange={event => update({ ...value, topK: event.target.value })} /></label>
      </div> : <>
      <label className="sr-only" htmlFor={`${id}-preset`}>{stage.title} 등록 모델 선택</label>
      <select id={`${id}-preset`} className="mt-model-select" value={profile?.id || ''} disabled={disabled || !value.enabled}
        onChange={event => update({ ...value, ...modelChoice(event.target.value, health) })}>
        <option value="">직접 입력 / 링크 붙여넣기</option>
        {models.map(model => <option key={model.id} value={model.id}>{model.label || model.id}</option>)}
      </select>
      <label className="sr-only" htmlFor={id}>{stage.title} 모델</label>
      <input id={id} value={registered?.model_id || value.model} disabled={disabled || !value.enabled} autoComplete="off" spellCheck={false}
        placeholder="소유자/모델명 또는 Hugging Face 링크" maxLength={512} aria-invalid={value.enabled && !!invalid}
        aria-describedby={`${id}-hint`} onChange={event => update({ ...value, ...modelChoice(event.target.value, health) })} />
      <small id={`${id}-hint`} className={value.enabled && (invalid || unsupported) ? 'mt-error' : ''}>
        {unsupported ? '이 Pod에서 지원하지 않는 단계입니다.' : invalid || modelWarning || profile?.description || (parsed ? `${parsed.model_id}${parsed.revision ? ` @ ${parsed.revision}` : ''} · 입력 형식 확인, 실행 호환성은 Pod에서 확인` : '모델 목록에서 선택하거나 Hugging Face 링크를 입력하세요.')}
      </small>
      {stage.id === 'logo_compare' && extraSimilarity && parsed?.model_id === 'vismatch/xfeat' && <div><label className="mt-similarity-option">
        <input type="checkbox" checked={!!value.imageSimilarity} disabled={disabled || !value.enabled} onChange={event => update({ ...value, imageSimilarity: event.target.checked })} />
        <span>SigLIP2 이미지 코사인 유사도도 계산<small>크롭과 원본 이미지를 추가 모델로 대조합니다.</small></span>
      </label>{value.imageSimilarity && <label>추가 계산 모델<select className="mt-model-select" aria-label="XFeat 추가 유사도 모델" disabled={disabled || !value.enabled} value={value.imageSimilarityModel || SIGLIP2} onChange={event => update({ ...value, imageSimilarityModel: event.target.value })}>{SIGLIP2_MODELS.map(id => <option key={id} value={id}>{id}</option>)}</select></label>}</div>}
      {SIGLIP2_MODELS.includes(parsed?.model_id) && stage.id === 'visual_extract' && <div className="mt-model-options">
        <div className="mt-title-help"><label htmlFor={`${id}-labels`}>비교할 후보 문구 <span>필수 · 한 줄에 하나</span></label></div>
        <textarea id={`${id}-labels`} rows={4} value={value.labels || ''} disabled={disabled || !value.enabled}
          maxLength={6432} placeholder={'a red background\na person holding a product\na blue logo'}
          aria-describedby={`${id}-labels-hint`} onChange={event => update({ ...value, labels: event.target.value })} />
        <small id={`${id}-labels-hint`}>최대 32개, 문구당 200자.</small>
      </div>}
      <ModeltestPrompt key={value.model} kind={stage.id} modelId={promptModel} profile={registered} value={value} disabled={disabled || !value.enabled}
        label={`${stage.title} 프롬프트`} onChange={patch => update({ ...value, ...patch })} />
      </>}
    </div>
  </div>;
}

function JsonView({ value, empty = '아직 반환된 결과가 없습니다.' }) {
  return value === undefined || value === null ? <p className="mt-empty">{empty}</p> : <pre className="mt-json">{JSON.stringify(value, null, 2)}</pre>;
}

export default function ModelTestPage() {
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem('creative-checker-modeltest-theme') === 'dark' ? 'dark' : 'light'; } catch { return 'light'; }
  });
  useEffect(() => { try { localStorage.setItem('creative-checker-modeltest-theme', theme); } catch { /* Theme switching still works without storage. */ } }, [theme]);
  const draft = useModeltestDraft();
  const { flow, setFlow, flows, setFlows } = draft;
  const state = flows[flow]; const boardHistory = useRef({ image: [], video: [], similarity: [] }); const boardRedo = useRef({ image: [], video: [], similarity: [] });
  const [confirmation, setConfirmation] = useState(null);
  const confirmAction = (options, action) => setConfirmation({ ...(typeof options === 'string' ? { message: options, title: '작업 확인', confirmLabel: '확인', danger: true } : options), action });
  const update = patch => {
    if (['stages', 'workflow', 'agentWorkflow'].some(key => patch[key] && patch[key] !== state[key])) {
      rememberBoard(boardHistory.current[flow], state); boardRedo.current[flow] = [];
    }
    setFlows(current => ({ ...current, [flow]: { ...current[flow], ...patch } }));
  };
  function undoBoard() {
    if (locked) return;
    const previous = boardHistory.current[flow].pop();
    if (previous) { rememberBoard(boardRedo.current[flow], state); setFlows(current => ({ ...current, [flow]: { ...current[flow], ...previous } })); }
  }
  function redoBoard() {
    if (locked) return;
    const next = boardRedo.current[flow].pop();
    if (next) { rememberBoard(boardHistory.current[flow], state); setFlows(current => ({ ...current, [flow]: { ...current[flow], ...next } })); }
  }
  const [endpoint, setEndpoint] = useState(''); const [token, setToken] = useState('');
  const [savedSSH] = useState(readSSH);
  const [method, setMethod] = useState('ssh'); const [sshCommand, setSshCommand] = useState(savedSSH.command);
  const [connectionRestored, setConnectionRestored] = useState(false);
  const [connection, setConnection] = useState(null); const [connecting, setConnecting] = useState(false);
  const [connectionError, setConnectionError] = useState('');
  const [libraryOpen, setLibraryOpen] = useState(false);
  const monitorRef = useRef(null); const focusMonitor = useRef(false);
  const resultsRef = useRef(null); const pendingResults = useRef(null);
  const [catalog, setCatalog] = useState({ models: [], families: {} }); const [catalogError, setCatalogError] = useState('');
  async function reloadCatalog() {
    try { const value = await sshRequest('/api/modeltest/ssh/models'); setCatalog(value); setCatalogError(''); }
    catch (error) { setCatalogError(error.message); }
  }
  useEffect(() => { reloadCatalog(); }, []);
  const [connectionOpen, setConnectionOpen] = useState(true); const [restoring, setRestoring] = useState(false);
  const wasReady = useRef(false);
  const [restored, setRestored] = useState(false);
  const [job, setJob] = useState(null); const [manifest, setManifest] = useState(null);
  const [submitting, setSubmitting] = useState(false); const [unknown, setUnknown] = useState(false);
  const [paused, setPaused] = useState(false); const [cancelling, setCancelling] = useState(false);
  const [runError, setRunError] = useState(''); const [showErrors, setShowErrors] = useState(false);
  const [resultTab, setResultTab] = useState('results'); const [selectedStage, setSelectedStage] = useState('');
  const [resultSelections, setResultSelections] = useState({});
  const previewCache = useMemo(() => new Map(), [job?.id]);
  const [startedAt, setStartedAt] = useState(null); const [elapsed, setElapsed] = useState(0);
  const requestRef = useRef(null); const contextRef = useRef(null); const submitLock = useRef(false);
  const videoRef = useRef(null); const [previewError, setPreviewError] = useState('');
  const preview = usePreview(state.media);
  const health = connection?.health;
  const target = connectionTarget(method, method === 'ssh' ? sshCommand : connection?.endpoint || endpoint);
  useEffect(() => {
    const ready = health?.ready === true;
    if (connectionError || !ready) setConnectionOpen(true);
    else if (!wasReady.current) setConnectionOpen(false);
    wasReady.current = ready;
  }, [health?.ready, connectionError]);
  const modelHealth = { ...(health || { ready: false, stages: STAGES.map(stage => stage.id), formats: FILE_TYPES, limits: { ...Object.fromEntries(Object.entries(FILE_LIMITS).map(([key, value]) => [`${key}_bytes`, value])), total_bytes: 600 * 1024 ** 2 }, workflow: { agents: true } }), registered: catalog.models };
  const preparing = health?.preparing === true;
  const terminal = job ? TERMINAL.includes(job.status) : false;
  const running = !!job && !terminal;
  const lockReason = !draft.ready ? '저장된 설정을 불러오는 중이라 편집할 수 없습니다.' : restoring ? '이전 작업을 복원하는 중이라 편집할 수 없습니다.' : submitting ? '자료를 전송하고 작업을 접수하는 중이라 편집할 수 없습니다.' : unknown ? '작업 접수 여부를 확인해야 설정을 바꿀 수 있습니다.' : running ? '검수가 실행 중이라 설정을 바꿀 수 없습니다.' : preparing ? 'Pod 실행환경을 준비하는 중이라 편집할 수 없습니다.' : '';
  const locked = !!lockReason;
  useEffect(() => {
    const keydown = event => {
      if (libraryOpen || confirmation || event.defaultPrevented || event.target.closest('input,textarea,select,[contenteditable=true],[popover]')) return;
      if ((event.ctrlKey || event.metaKey) && !event.altKey) {
        const key = event.key.toLowerCase();
        if (key === 'z' || (key === 'y' && !event.shiftKey)) { event.preventDefault(); if (event.shiftKey || key === 'y') redoBoard(); else undoBoard(); }
      }
    };
    window.addEventListener('keydown', keydown); return () => window.removeEventListener('keydown', keydown);
  }, [flow, locked, libraryOpen, confirmation, state]);
  const errors = formErrors(flow, state, modelHealth);
  const stages = stagesFor(flow).filter(stage => !stage.workflowOnly || state.workflow.enabled); const selected = stages.filter(stage => state.stages[stage.id].enabled);
  const editorMode = flow === 'similarity' ? 'similarity' : state.agentWorkflow.enabled ? 'agents' : state.workflow.enabled ? 'graph' : 'sequential';
  const selectedCount = flow === 'similarity' ? 2 : state.agentWorkflow.enabled ? state.agentWorkflow.agents.filter(agent => agent.enabled).reduce((sum, agent) => sum + agent.nodes.length, 0) : selected.length;
  const completedStages = job?.stages.filter(stage => stage.status === 'completed') || [];
  const failedStages = job?.stages.filter(stage => stage.status === 'failed') || [];
  const activeStages = job?.stages.filter(stage => stage.status === 'running') || [];
  const completed = completedStages.length;
  const progressTotal = manifest ? manifest.stages.length + (manifest.flow === 'video' && manifest.purpose !== 'prepare' ? 1 : 0) : selectedCount + (flow === 'video' ? 1 : 0);
  const resultStage = job?.stages.find(stage => stage.id === selectedStage);
  const resultSelectionKey = `${job?.id}:${manifest?.flow === 'similarity' ? 'similarity' : selectedStage}`;
  const runFileCount = manifest ? Number(!!manifest.files.media) + manifest.files.documents.length + manifest.files.references.length : Number(!!state.media) + state.documents.length + state.references.length;
  const runTitle = submitting ? '자료 전송·접수 중' : unknown ? '접수 여부 확인 필요' : paused && running ? '상태 조회 일시정지' : job?.status === 'queued' ? preparationLabel(job.preparation) : job ? STATUS_LABELS[job.status] : '실행 전';

  function viewResults(stageId) {
    if (stageId) setSelectedStage(stageId);
    setResultTab('results');
    resultsRef.current?.focus({ preventScroll: true });
    resultsRef.current?.scrollIntoView({ behavior: 'instant', block: 'start' });
  }
  useEffect(() => {
    if (!job?.id || pendingResults.current !== job.id) return;
    if (libraryOpen) { setLibraryOpen(false); return; }
    pendingResults.current = null; focusMonitor.current = false;
    viewResults();
  }, [job?.id, libraryOpen]);

  useEffect(() => () => requestRef.current?.abort(), []);
  useEffect(() => { if (job?.status === 'completed') reloadCatalog(); }, [job?.id, job?.status]);
  useEffect(() => {
    if (!draft.ready) return;
    if (draft.savedRun && (!savedSSH.endpoint || draft.savedRun.endpoint === savedSSH.endpoint)) restoreRun(draft.savedRun);
    else if (savedSSH.endpoint) restoreSSH();
  }, [draft.ready]);
  useEffect(() => { setPreviewError(''); }, [state.media]);
  useEffect(() => {
    if (!(submitting || unknown || running || preparing || draft.saving)) return;
    const warn = event => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [submitting, unknown, running, preparing, draft.saving]);
  useEffect(() => {
    if (!startedAt || terminal || (!running && !submitting && !unknown)) return;
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [startedAt, terminal, running, submitting, unknown]);
  useEffect(() => {
    if (!job?.id || terminal || paused) return;
    const context = contextRef.current; const controller = new AbortController(); let timer;
    async function poll() {
      try {
        const next = validateRun(await modeltestRequest(context, `/runs/${job.id}`, { signal: controller.signal }), job.id, context.manifest);
        if (controller.signal.aborted) return;
        setJob(next);
        if (!TERMINAL.includes(next.status)) timer = setTimeout(poll, 2000);
      } catch (error) {
        if (!controller.signal.aborted) { setRunError(`${error.message} 기존 작업의 조회를 재개할 수 있습니다.`); setPaused(true); }
      }
    }
    timer = setTimeout(poll, 1000);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [job?.id, terminal, paused]);

  useEffect(() => {
    if (!preparing || !connection) return;
    const controller = new AbortController(); let timer;
    async function pollSetup() {
      try {
        const next = validateHealth(await modeltestRequest(connection, '/health', { signal: controller.signal }));
        if (controller.signal.aborted) return;
        setConnection(current => ({ ...current, health: next }));
        if (next.preparing) timer = setTimeout(pollSetup, 3000);
      } catch (error) {
        if (!controller.signal.aborted) { setConnectionError(error.message); timer = setTimeout(pollSetup, 5000); }
      }
    }
    timer = setTimeout(pollSetup, 1500);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [preparing, connection?.endpoint]);

  async function restoreRun(record, nextConnection) {
    if (requestRef.current || restoring) return;
    if (!record || !['ssh', 'http'].includes(record.transport) || !/^[\w-]{1,128}$/.test(record.jobId || '') || record.manifest?.contract !== CONTRACT) return;
    if (nextConnection && nextConnection.transport !== record.transport) { setRunError('이전 작업과 같은 연결 방식으로 연결해 주세요.'); return; }
    const context = { ...record, ...(nextConnection || {}) };
    const controller = new AbortController(); requestRef.current = controller; setRestoring(true); setRunError('');
    try {
      const nextHealth = validateHealth(await modeltestRequest(context, '/health', { signal: controller.signal }));
      const next = validateRun(await modeltestRequest(context, `/runs/${record.jobId}`, { signal: controller.signal }), record.jobId, record.manifest);
      if (controller.signal.aborted) return;
      contextRef.current = context; setConnection({ ...context, health: nextHealth }); setMethod(record.transport);
      setManifest(record.manifest); setJob(next); setSelectedStage(next.stages[0]?.id || ''); setResultTab('results');
      setStartedAt(Date.now()); setElapsed(0); setRestored(true); setPaused(false);
      await draft.rememberRun(context, next.id);
    } catch (error) {
      if (!controller.signal.aborted) setRunError(`이전 작업을 자동으로 연결하지 못했습니다. 작업 ID: ${record.jobId}. 같은 Pod에 연결한 뒤 '이전 작업 조회'를 누르세요. ${error.message}`);
    } finally { requestRef.current = null; setRestoring(false); }
  }

  function rememberSSH(value) {
    try { writeSSH(value); }
    catch { setConnectionError('브라우저 제한으로 SSH 명령을 저장하지 못했습니다. 새로고침 후 다시 입력해 주세요.'); }
  }

  async function restoreSSH() {
    const controller = new AbortController(); requestRef.current = controller; setConnecting(true);
    try {
      const context = { transport: 'ssh', endpoint: savedSSH.endpoint };
      const nextHealth = validateHealth(await modeltestRequest(context, '/health', { signal: controller.signal }));
      if (!controller.signal.aborted) { setConnection({ ...context, health: nextHealth }); setConnectionRestored(true); }
    } catch (error) {
      if (!controller.signal.aborted) {
        rememberSSH({ command: sshCommand });
        setConnectionError(`이전 연결 세션을 복원하지 못했습니다. 저장된 명령으로 연결 확인을 눌러 주세요. ${error.message}`);
      }
    } finally { requestRef.current = null; setConnecting(false); }
  }

  async function connect() {
    if (requestRef.current || locked) return;
    const controller = new AbortController(); requestRef.current = controller;
    setConnecting(true); setConnection(null); setConnectionRestored(false); setConnectionError('');
    try {
      if (method === 'ssh') {
        const result = validateHealth(await sshRequest('/api/modeltest/ssh/connections', { method: 'POST', body: JSON.stringify({ command: sshCommand }), headers: { 'Content-Type': 'application/json' }, signal: controller.signal, timeout: 90000 }));
        if (typeof result.connection_id !== 'string' || !/^[\w-]{1,128}$/.test(result.connection_id)) throw new Error('SSH 연결 ID를 확인하지 못했습니다.');
        if (!controller.signal.aborted) {
          const context = { transport: 'ssh', endpoint: `/api/modeltest/ssh/connections/${result.connection_id}`, health: result };
          setConnection(context); rememberSSH({ command: sshCommand, endpoint: context.endpoint });
        }
      } else {
        const base = parseEndpoint(endpoint);
        const result = validateHealth(await podRequest(base, '/health', { token, signal: controller.signal }));
        if (!controller.signal.aborted) { setConnection({ transport: 'http', endpoint: base, token, health: result }); setEndpoint(base); }
      }
    } catch (error) { if (!controller.signal.aborted) setConnectionError(error.message); }
    finally { requestRef.current = null; setConnecting(false); }
  }

  async function preparePod() {
    if (requestRef.current || locked || !connection) return;
    const controller = new AbortController(); requestRef.current = controller; setConnecting(true); setConnectionError('');
    try {
      const result = validateHealth(await modeltestRequest(connection, '/prepare', { method: 'POST', signal: controller.signal }));
      if (!controller.signal.aborted) setConnection(current => ({ ...current, health: result }));
    } catch (error) { if (!controller.signal.aborted) setConnectionError(error.message); }
    finally { requestRef.current = null; setConnecting(false); }
  }

  async function submit(context) {
    if (submitLock.current) return;
    submitLock.current = true; const controller = new AbortController(); requestRef.current = controller;
    setSubmitting(true); setUnknown(false); setRunError(''); setPaused(false);
    try {
      await draft.rememberRun(context);
      const next = validateRun(await modeltestRequest(context, '/runs', {
        method: 'POST', body: context.body, headers: { 'Idempotency-Key': context.requestId },
        signal: controller.signal, timeout: 120000,
      }), undefined, context.manifest);
      if (!controller.signal.aborted) {
        if (next.id !== job?.id) pendingResults.current = next.id;
        setJob(next); setSelectedStage(next.stages[0]?.id || context.manifest.stages[0].id); await draft.rememberRun(context, next.id);
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        const uncertain = !error.status || error.status >= 500 || error.status === 408;
        setUnknown(uncertain);
        setRunError(`${error.message}${uncertain ? ' 접수됐을 수 있으므로 같은 요청 ID로 확인해 주세요.' : ''}`);
      }
    } finally { submitLock.current = false; requestRef.current = null; setSubmitting(false); }
  }

  function start(event) {
    event.preventDefault(); setShowErrors(true);
    if (locked || connecting || submitLock.current || errors.length || !health?.ready) return;
    const config = makeManifest(flow, state, modelHealth);
    if (connection.transport !== 'ssh' && config.stages.some(stage => stage.registered_model)) { setRunError('등록 모델은 현재 SSH 실행기에서 지원합니다. SSH 연결을 선택해 주세요.'); return; }
    launch(config, state);
  }

  function launch(config, attachments) {
    const context = { ...connection, manifest: config, media: attachments.media, body: makeFormData(config, attachments), requestId: crypto.randomUUID() };
    contextRef.current = context; setManifest(config); setJob(null); setStartedAt(Date.now()); setElapsed(0); setRestored(false); setResultTab('results');
    submit(context);
  }

  function changeEditor(mode) {
    if (locked) return;
    if (mode === 'agents') update({ workflow: { ...state.workflow, enabled: false }, agentWorkflow: state.agentWorkflow.agents.length ? { ...state.agentWorkflow, enabled: true } : agentTemplate(flow) });
    else if (mode === 'graph') update({ ...(!state.workflow.edges.length ? workflowPreset(flow, state) : { workflow: { ...state.workflow, enabled: true } }), agentWorkflow: { ...state.agentWorkflow, enabled: false } });
    else update({ workflow: { ...state.workflow, enabled: false }, agentWorkflow: { ...state.agentWorkflow, enabled: false } });
  }

  function closeLibrary() {
    setLibraryOpen(false);
    if (focusMonitor.current) {
      focusMonitor.current = false;
      monitorRef.current?.focus({ preventScroll: true });
      monitorRef.current?.scrollIntoView({ behavior: 'auto', block: 'start' });
    }
  }

  function renderStage(id, inInspector = false) {
    if (id === 'frames') return <FrameSampling sampling={state.sampling} onChange={sampling => update({ sampling })} disabled={locked} />;
    const index = stages.findIndex(stage => stage.id === id); const stage = stages[index];
    if (!stage) return null;
    return <ModelStage stage={stage} inInspector={inInspector} index={index + 1} value={state.stages[stage.id]} health={modelHealth} disabled={locked}
                extraSimilarity={!state.workflow.enabled || !state.stages.logo_similarity?.enabled}
                update={value => update({ stages: { ...state.stages, [stage.id]: value,
                  ...((value.model !== state.stages[stage.id].model || value.modelFingerprint !== state.stages[stage.id].modelFingerprint) && state.stages.candidate_filter ? { candidate_filter: { ...state.stages.candidate_filter, threshold: '' } } : {}),
                  ...(stage.id === 'logo_similarity' && value.enabled ? { logo_compare: { ...state.stages.logo_compare, imageSimilarity: false } } : {}) } })} />;
  }

  function modelAction(model, action) {
    if (locked || connecting || !health?.ready || connection.transport !== 'ssh') return;
    try {
      if (action === 'prepare') {
        const config = { contract: CONTRACT, flow: flow === 'similarity' ? 'image' : flow, purpose: 'prepare', stages: [model ? { id: 'model_prepare', model_id: model.model_id, registered_model: model.id, model_fingerprint: model.fingerprint } : { id: 'disk_lightglue_prepare' }], files: { media: null, documents: [], references: [] } };
        launch(config, { media: null, documents: [], references: [] });
      } else {
        if (errors.length) throw new Error(errors[0]);
        const config = makeManifest(flow, state, modelHealth);
        if (!config.stages.some(stage => stage.registered_model === model.id)) throw new Error('현재 워크플로우 노드에서 이 등록 모델을 선택해 주세요.');
        config.purpose = 'sample'; if (config.sampling) config.sampling.max_frames = 1;
        launch(config, state);
      }
    } catch (error) { setRunError(error.message); setShowErrors(true); }
    focusMonitor.current = true; setLibraryOpen(false);
  }

  async function cancel() {
    if (requestRef.current || !running) return;
    const controller = new AbortController(); requestRef.current = controller; setCancelling(true); setRunError(''); setPaused(true);
    try {
      const context = contextRef.current;
      const next = validateRun(await modeltestRequest(context, `/runs/${job.id}/cancel`, { method: 'POST', signal: controller.signal }), job.id, context.manifest);
      if (!controller.signal.aborted) { setJob(next); setPaused(false); }
    } catch (error) { if (!controller.signal.aborted) setRunError(`${error.message} 취소 여부는 상태 조회로 다시 확인해 주세요.`); }
    finally { requestRef.current = null; setCancelling(false); setPaused(false); }
  }

  function download() {
    const blob = new Blob([JSON.stringify({ request_id: contextRef.current?.requestId, config: manifest, result: job }, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob); const anchor = document.createElement('a');
    anchor.href = url; anchor.download = `modeltest-${job?.id || 'request'}.json`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return <div className="mt-shell" data-theme={theme}>
    <header className="mt-topbar"><Brand disabled={locked} title={lockReason || undefined} aria-describedby={locked ? 'mt-lock-reason' : undefined} onClick={() => window.location.assign('/')} /><span className="mt-product-label">모델 테스트</span>
      <div className="mt-theme-switch" role="group" aria-label="화면 테마">{[['light', '라이트'], ['dark', '다크']].map(([value, title]) => <button type="button" key={value} aria-pressed={theme === value} onClick={() => setTheme(value)}>{title}</button>)}</div>
      <button type="button" className="btn secondary small" aria-haspopup="dialog" aria-controls="mt-model-library" aria-expanded={libraryOpen} onClick={() => setLibraryOpen(true)}>모델 관리</button>
      <button type="button" className="text-btn mt-home" disabled={locked} title={lockReason || undefined} aria-describedby={locked ? 'mt-lock-reason' : undefined} onClick={() => window.location.assign('/')}>서비스 홈</button></header>
    <main className="mt-main">
      <div className="mt-heading"><div><h1>검수 모델 테스트</h1><p>자료와 모델을 바꿔가며, 추출부터 대조까지 한 번에 확인하세요.</p></div></div>

      <details className="mt-connection-wrap" open={connectionOpen} onToggle={event => setConnectionOpen(event.currentTarget.open)}>
        <summary><span>RunPod 연결</span><span className={`mt-connection-state ${health?.ready && !connectionError ? 'is-ready' : ''}`}><i />{connectionError ? '연결 오류' : connecting ? 'Pod 확인 중' : preparing ? '실행환경 준비 중' : health?.ready ? 'Pod 연결됨' : health ? 'Pod 준비 필요' : 'Pod 연결 전'}</span><span className="mt-connection-target" title={target}>{method === 'ssh' ? 'SSH' : 'HTTP'}{target && ` · ${target}`}</span></summary>
      <section className="mt-connection" aria-labelledby="mt-connection-title">
        <div className="mt-connection-method"><div className="mt-connection-label"><Icon name="grid" /><h2 id="mt-connection-title">RunPod 연결</h2><ModeltestHelp label="RunPod 연결">
            {method === 'ssh' ? <>{!connection && <><p>SSH over exposed TCP와 ssh.runpod.io 기본 SSH를 구분합니다. 개인키는 노트북에 두고 경로만 입력하세요. 공개 HTTP 포트는 필요하지 않습니다.</p><p>SSH 명령과 연결 설정은 이 브라우저에 저장됩니다. 개인키 내용은 저장하지 않습니다.</p></>}<p>환경 준비는 Pod의 전용 가상환경에 패키지를 설치합니다. 모델 가중치는 검수 실행 시 다운로드합니다.</p></> : <p>{CONTRACT} API의 준비 상태를 확인합니다. 토큰은 이 화면의 메모리에만 보관합니다.</p>}
          </ModeltestHelp></div>
          <div className="mt-method-buttons" aria-label="RunPod 연결 방식">{[['ssh', 'SSH 명령'], ['http', 'HTTP API']].map(([id, label]) => <button type="button" key={id} disabled={locked || connecting} aria-pressed={method === id}
            onClick={() => { setMethod(id); setConnection(null); setConnectionError(''); }}>{label}</button>)}</div></div>
        {method === 'ssh' ? <div className="mt-ssh-command"><label htmlFor="mt-ssh-command">RunPod Connect의 SSH 명령</label><input id="mt-ssh-command" value={sshCommand} disabled={locked || connecting} autoComplete="off" spellCheck={false} maxLength={2048}
          placeholder={'ssh root@IP주소 -p 포트 -i ~/.ssh/id_ed25519'} onChange={event => { setSshCommand(event.target.value); setConnection(null); setConnectionError(''); rememberSSH({ command: event.target.value }); }} /></div>
          : <><div className="mt-endpoint"><label htmlFor="mt-endpoint">Pod HTTP 서비스 주소</label><input id="mt-endpoint" type="url" value={endpoint} disabled={locked || connecting}
          placeholder="https://pod-id-8000.proxy.runpod.net" spellCheck={false} autoComplete="off"
          onChange={event => { setEndpoint(event.target.value); setConnection(null); setConnectionError(''); }} /></div>
        <div className="mt-token"><label htmlFor="mt-token">Pod API 토큰 <span>선택</span></label><input id="mt-token" type="password" value={token} disabled={locked || connecting}
          placeholder="Pod API에 설정한 토큰" autoComplete="off" onChange={event => { setToken(event.target.value); setConnection(null); setConnectionError(''); }} /></div></>}
        <button type="button" className="btn secondary" disabled={locked || connecting || !(method === 'ssh' ? sshCommand.trim() : endpoint.trim())} onClick={connect}>{connecting ? '연결 확인 중…' : '연결 확인'}</button>
        {connectionError && <p className="mt-error mt-connection-hint" role="alert">{connectionError}</p>}
        {method === 'ssh' && connectionRestored && <p className="mt-connection-hint">이전 로컬 세션을 복원했습니다. 현재 Pod 상태를 갱신하려면 연결 확인을 누르세요.</p>}
        {method === 'ssh' && health && <div className="mt-ssh-readiness"><p role="status">{preparing ? 'Pod의 전용 가상환경에 패키지를 준비하고 있습니다. 잠시 기다려 주세요.' : health.detail}</p>
          {!health.ready && !preparing && health.can_prepare && <button type="button" className="btn secondary small" disabled={connecting} onClick={preparePod}>Pod 실행환경 준비</button>}
          {health.cuda === false && <small>CUDA GPU와 PyTorch가 포함된 Pod가 필요합니다.</small>}</div>}
        {method === 'http' && health && !health.ready && <p className="mt-error mt-connection-hint" role="status">Pod에는 연결됐지만 검수 준비가 끝나지 않았습니다. 준비 후 다시 연결해 주세요.</p>}
      </section>
      </details>
      <div className="mt-draft-status"><p role="status">{draft.saving ? '첨부·설정 저장 중… 저장 완료 후 새로고침해 주세요.' : draft.storage}</p>
        <button type="button" className="text-btn" disabled={locked} onClick={() => confirmAction({ title: '저장 데이터 지우기', message: '이 브라우저에 저장한 첨부 파일과 모델 설정을 모두 지울까요? 실행 결과와 Pod 작업은 유지됩니다.', confirmLabel: '지우기', danger: true }, () => { boardHistory.current = { image: [], video: [], similarity: [] }; boardRedo.current = { image: [], video: [], similarity: [] }; draft.clearDraft(); })}>저장된 첨부·설정 지우기</button>
      </div>
      {draft.savedRun && !job && <div className="mt-restore-run"><p>{restoring ? '이전 검수 작업 상태를 확인하고 있습니다.' : `이전 작업 ID: ${draft.savedRun.jobId}`}</p>
        <button type="button" className="btn secondary small" disabled={locked || connecting} onClick={() => restoreRun(draft.savedRun, connection)}>이전 작업 조회</button></div>}

      <ModeltestLibrary confirmAction={confirmAction} open={libraryOpen} onClose={closeLibrary} catalog={catalog} reload={reloadCatalog} error={catalogError} disabled={locked || connecting} canRun={health?.ready && connection?.transport === 'ssh'} onAction={modelAction} />
      <form className="mt-execution-form" onSubmit={start} noValidate>
        <div className="mt-workspace-bar"><div className="mt-flow-switch" aria-label="검수 유형">
          <button type="button" aria-pressed={flow === 'image'} disabled={locked} onClick={() => { setFlow('image'); setShowErrors(false); }}><Icon name="image" />이미지 검수</button>
          <button type="button" aria-pressed={flow === 'video'} disabled={locked} onClick={() => { setFlow('video'); setShowErrors(false); }}><span aria-hidden="true">▶</span>영상 검수</button>
          <button type="button" aria-pressed={flow === 'similarity'} disabled={locked} onClick={() => { setFlow('similarity'); setShowErrors(false); }}><Icon name="search" />유사도 테스트</button>
        </div><p>{flow === 'similarity' ? '기준 이미지 1장 · 대조 이미지 최대 100장' : flow === 'image' ? '포스터 + 문서 필수' : '영상 필수 · 문서·원본 로고 선택'}<span>{selectedCount}개 모델 단계 선택</span></p></div>

        {flow !== 'similarity' && <div className="mt-editor-mode" role="group" aria-label="워크플로우 편집 방식">{[['sequential', '순차'], ['graph', '그래프'], ['agents', '에이전트']].map(([mode, title]) => <button type="button" key={mode} aria-pressed={editorMode === mode} disabled={locked} onClick={() => changeEditor(mode)}>{title}</button>)}</div>}
        {flow === 'similarity' && <section className="mt-panel mt-similarity-plan" aria-label="유사도 테스트 모델"><div className="mt-panel-heading"><h2>이미지 전체 대조</h2><ModeltestHelp label="유사도 테스트 모델"><p>로고 추출 없이 기준 이미지와 각 대조 이미지 전체를 비교합니다. SigLIP2 이후 XFeat를 실행하며 모든 대조 이미지에 두 측정을 적용합니다.</p><p>두 모델의 기존 전처리는 유지됩니다. SigLIP2의 모델 입력 해상도와 XFeat의 크기 정규화는 다운사이징한 이미지에 적용됩니다.</p></ModeltestHelp></div><div className="mt-panel-body mt-similarity-models">{SIMILARITY_STAGES.map(stage => <div key={stage.id}><strong>{stage.title}</strong><small>{stage.id === 'logo_similarity' ? registration(state.stages.logo_similarity.model, modelHealth)?.model_id || state.stages.logo_similarity.model : stage.model}</small></div>)}</div></section>}
        {editorMode === 'agents' && <ModeltestAgents confirmAction={confirmAction} flow={flow} state={state} update={update} disabled={locked} health={modelHealth} onUndo={undoBoard} canUndo={!!boardHistory.current[flow].length} onRedo={redoBoard} canRedo={!!boardRedo.current[flow].length} />}
        {editorMode === 'graph' && <ModeltestWorkflow key={flow} flow={flow} state={state} update={update} disabled={locked} onUndo={undoBoard} canUndo={!!boardHistory.current[flow].length} onRedo={redoBoard} canRedo={!!boardRedo.current[flow].length} renderStage={renderStage} />}
          {editorMode === 'sequential' && <section className="mt-panel mt-pipeline" aria-labelledby="mt-pipeline-title">
            <div className="mt-panel-heading"><div className="mt-title-help"><h2 id="mt-pipeline-title">검수 파이프라인</h2></div><span>{stages.length}단계</span></div>
            <div className="mt-pipeline-scroll" role="region" aria-label="검수 단계 설정 목록" tabIndex={0}>
            {stages.map(stage => <React.Fragment key={stage.id}>{renderStage(stage.id)}</React.Fragment>)}
            </div>
          </section>}

        {flow === 'similarity' && <section className="mt-panel mt-similarity-plan"><div className="mt-panel-body"><label>이미지 유사도 모델<select className="mt-model-select" aria-label="유사도 테스트 모델 선택" disabled={locked} value={modelOptions(modelHealth).some(model => model.id === state.stages.logo_similarity.model) ? state.stages.logo_similarity.model : ''} onChange={event => update({ stages: { ...state.stages, logo_similarity: { ...state.stages.logo_similarity, ...modelChoice(event.target.value, modelHealth) } } })}><option value="">직접 입력 / 링크 붙여넣기</option>{modelOptions(modelHealth).filter(model => model.stages.includes('logo_similarity')).map(model => <option key={model.id} value={model.id}>{model.label || model.id}</option>)}</select></label><label>모델 ID 또는 Hugging Face 링크<input className="mt-model-select" aria-label="유사도 테스트 모델" maxLength={512} disabled={locked} value={registration(state.stages.logo_similarity.model, modelHealth)?.model_id || state.stages.logo_similarity.model} onChange={event => update({ stages: { ...state.stages, logo_similarity: { ...state.stages.logo_similarity, ...modelChoice(event.target.value, modelHealth) } } })} /></label></div></section>}

        <div className="mt-run-layout">
          <section className="mt-panel mt-materials" aria-labelledby="mt-materials-title">
            <div className="mt-panel-heading"><h2 id="mt-materials-title">검수 자료</h2><Icon name="upload" /></div>
            <div className="mt-panel-body">
              <Attachments key={flow} kind={flow === 'similarity' ? 'image' : flow} title={flow === 'similarity' ? '기준 이미지' : flow === 'image' ? '포스터 이미지' : '검수 영상'} required files={state.media ? [state.media] : []}
                onChange={files => update({ media: files[0] || null })} disabled={locked} health={health} />
              {flow === 'video' && editorMode !== 'graph' && <FrameSampling sampling={state.sampling} onChange={sampling => update({ sampling })} disabled={locked} agentMode={state.agentWorkflow.enabled} />}
              {preview && <div className="mt-preview">{flow !== 'video'
                ? <img src={preview} alt={flow === 'similarity' ? '첨부한 기준 이미지' : '첨부한 포스터'} onError={() => setPreviewError('이 형식은 브라우저 미리보기를 지원하지 않을 수 있습니다. Pod 지원 형식을 확인해 주세요.')} />
                : <video ref={videoRef} src={preview} controls preload="metadata" onError={() => setPreviewError('브라우저에서 재생할 수 없는 코덱입니다. 영상 처리는 Pod의 지원 여부에 따릅니다.')} />}
                {previewError && <p className="mt-hint">{previewError}</p>}</div>}
              {flow === 'similarity' && <>
                <ImageResize label="기준 이미지" value={state.resize} onChange={resize => update({ resize })} disabled={locked || !state.media} />
                <ImageResize label="대조 이미지" value={state.referenceResize} onChange={referenceResize => update({ referenceResize })} disabled={locked} />
                <Attachments kind="reference" title="대조 이미지" multiple required maxFiles={100} thumbnails files={state.references} onChange={references => update({ references })}
                  onClear={clear => confirmAction({ title: '대조 이미지 전체 해제', message: `첨부한 대조 이미지 ${state.references.length}장을 모두 해제할까요? 기준 이미지와 실행 결과는 유지됩니다.`, confirmLabel: '전체 해제', danger: true }, clear)} disabled={locked} health={health} />
              </>}
              {flow !== 'similarity' && <Attachments kind="document" title="발주서·계약서" multiple required={flow === 'image'} files={state.documents}
                onChange={documents => update({ documents })} disabled={locked} health={health} />}
              {flow === 'video' && <Attachments kind="reference" title="원본 로고·레퍼런스" multiple files={state.references}
                onChange={references => update({ references })} disabled={locked} health={health} />}
              {flow !== 'similarity' && <details key={`conditions-${flow}`} className="mt-conditions" open={state.requirementsOpen} onToggle={event => { if (event.currentTarget.open !== state.requirementsOpen) update({ requirementsOpen: event.currentTarget.open }); }}><summary>조건 직접 입력 <span>선택</span></summary>
                <div className="mt-title-help"><label htmlFor="mt-requirements">추가 검수 조건</label><ModeltestHelp label="조건 직접 입력"><p>문서에서 추출한 조건과 함께 사용합니다. 문서가 없으면 대조할 조건을 직접 입력하세요.</p></ModeltestHelp></div><textarea id="mt-requirements" value={state.requirements} rows={4} maxLength={12000} disabled={locked}
                  placeholder={'예: 행사 날짜가 11월 5일로 표시되어야 합니다.\n브랜드 로고가 화면 오른쪽 위에 있어야 합니다.'}
                  onChange={event => update({ requirements: event.target.value })} />
              </details>}
            </div>
          </section>

          <aside ref={monitorRef} className="mt-monitor" aria-label="실행 모니터" tabIndex={-1}>
            <section className="mt-run-card"><div className="mt-run-heading"><Icon name="grid" /><h2>실행 모니터</h2></div>
              <div className="mt-run-status" aria-live="polite"><span className={`mt-state-dot ${running ? 'is-running' : ''}`} />{runTitle}</div>
              {locked && <p id="mt-lock-reason" className="mt-lock-reason" role="status">{lockReason}</p>}
              {job?.status === 'queued' && job.preparation?.total_bytes > 0 && <p className="mt-preparation-note">{formatBytes(job.preparation.bytes_sent || 0)} / {formatBytes(job.preparation.total_bytes)} 전송</p>}
              {job?.preparation?.phase === 'ready' && <p className="mt-preparation-note">실행 준비 {job.preparation.elapsed_seconds}초
                {Number.isFinite(job.preparation.connection_seconds) && ` · 연결 ${job.preparation.connection_seconds}초`}
                {Number.isFinite(job.preparation.transfer_seconds) && ` · 전송 ${job.preparation.transfer_seconds}초`}
                {Number.isFinite(job.preparation.startup_seconds) && ` · 실행 ${job.preparation.startup_seconds}초`}</p>}
              <div className="mt-metrics"><button type="button" disabled={!job || !completed} aria-label={`완료 단계 ${completed}개 · 마지막 완료 단계 결과 보기`} onClick={() => viewResults(latestCompletedStage(completedStages)?.id)}><strong>{job ? completed : 0}<small> / {progressTotal}</small></strong><span>완료 단계</span></button><div><strong>{Math.floor(elapsed / 60)}<small>분 {elapsed % 60}초</small></strong><span>{restored ? '조회 재개 후 경과' : '접수 후 경과'}</span></div></div>
              <progress value={completed} max={Math.max(progressTotal, 1)} aria-label="완료한 검수 단계" />
              <dl className="mt-run-summary"><div><dt>{manifest ? '실행한 자료' : '입력 자료'}</dt><dd>{runFileCount}개</dd></div>
                <div><dt>선택 단계</dt><dd>{manifest ? manifest.stages.length : selectedCount}개</dd></div></dl>
              {!locked && <button type="submit" className="btn primary mt-run-button" disabled={connecting || !health?.ready}><Icon name="arrow" />검수 실행</button>}
              {submitting && <button className="btn primary mt-run-button" disabled>전송·접수 중…</button>}
              {unknown && !submitting && <><button type="button" className="btn primary mt-run-button" onClick={() => submit(contextRef.current)}>같은 요청으로 접수 확인</button>
                <button type="button" className="mt-subtle-button" onClick={() => confirmAction({ title: '접수 확인 종료', message: 'Pod에 접수된 작업이 남아 있을 수 있습니다. Pod에서 요청 ID를 확인한 후 새 설정으로 돌아갈까요?', confirmLabel: '종료', danger: false }, () => { setUnknown(false); setRunError('접수 확인을 종료했습니다. Pod 작업은 취소하지 않았습니다.'); })}>접수 확인 종료</button></>}
              {running && <div className="mt-run-actions"><button type="button" className="btn secondary" onClick={() => { setPaused(!paused); setRunError(''); }}>{paused ? '상태 조회 재개' : '상태 조회 일시정지'}</button>
                {health?.cancel === true && <button type="button" className="btn mt-stop-button" onClick={() => confirmAction({ title: '검수 강제 중단', message: '이 검수 작업을 강제로 중단할까요? 완료된 결과는 남고 Pod 자체는 계속 실행됩니다.', confirmLabel: '중단', danger: true }, 'cancel')} disabled={cancelling || job.status === 'queued'}>{cancelling ? '중단 확인 중…' : '검수 강제 중단'}</button>}
                </div>}
              {job && <a className="mt-results-link" href="#mt-results-title" onClick={event => { event.preventDefault(); viewResults(); }}>결과 보기</a>}
              {running && <p className="mt-monitor-note">조회 일시정지나 화면 닫기는 Pod의 계산을 중단하지 않습니다. 검수 강제 중단은 이 작업만 종료하며 Pod는 유지합니다.{job.status === 'queued' && ' 자료 전송 후 Pod 실행이 시작되면 중단할 수 있습니다.'}</p>}
            </section>
            {runError && <p className="mt-alert" role="alert">{runError}</p>}
            {job?.status === 'failed' && <p className="mt-alert" role="alert">{typeof job.error === 'string' ? job.error : 'Pod의 검수 작업이 실패했습니다. 단계별 결과를 확인해 주세요.'}</p>}
            <section className="mt-panel mt-checklist"><div className="mt-panel-heading"><h2>{job ? '실행 요약' : '실행 준비'}</h2></div>
              {job ? <div className="mt-status-summary">
                {failedStages.length > 0 && <div className="mt-status-totals">
                  <button type="button" aria-label={`실패 단계 ${failedStages.length}개 · 첫 실패 단계 결과 보기`} onClick={() => viewResults(failedStages[0]?.id)}>실패 <strong>{failedStages.length}개</strong></button>
                </div>}
                {running && <><p className="mt-hint">현재 실행 중</p>
                {activeStages.length ? activeStages.map(stage => <button type="button" className="mt-active-stage" key={stage.id} onClick={() => viewResults(stage.id)} aria-label={`${stageTitle(stage)} 실행 중 · 결과 보기`}>{stageTitle(stage)}</button>) : <p className="mt-hint">실행 중인 단계 없음</p>}</>}
              </div> : <ul className="mt-readiness">
                {[[!!health?.ready, 'Pod 연결'], [!!state.media, flow === 'similarity' ? '기준 이미지 첨부' : flow === 'image' ? '이미지 첨부' : '영상 첨부'], [flow === 'similarity' ? state.references.length > 0 && state.references.length <= 100 : flow === 'video' || !!state.documents.length, flow === 'similarity' ? '대조 이미지 첨부' : flow === 'image' ? '문서 첨부' : '문서·레퍼런스 선택'], [!errors.length, '모델·실행 조건']].map(([ready, label]) => <li key={label}><span>{label}</span><span className={ready ? 'mt-ready' : ''}>{ready ? '준비됨' : '확인 필요'}</span></li>)}
              </ul>}
              {showErrors && errors.length > 0 && <ul className="mt-validation" role="alert">{errors.map((error, index) => <li key={index}>{error}</li>)}</ul>}
            </section>

          </aside>
        </div>
      </form>

      <section className="mt-panel mt-results" aria-labelledby="mt-results-title"><div className="mt-panel-heading"><div><div className="mt-title-help"><h2 ref={resultsRef} id="mt-results-title" tabIndex={-1}>실행 결과</h2></div>{job && <p>작업 {job.id}</p>}</div>
        <button type="button" className="btn secondary small" disabled={!manifest} onClick={download}>JSON 내려받기</button></div>
        <div className="mt-result-tabs" aria-label="결과 보기">{[['results', '단계별 결과'], ['timing', '소요 시간'], ['debug', '디버그']].map(([id, title]) => <button type="button" key={id} aria-pressed={resultTab === id} onClick={() => setResultTab(id)}>{title}</button>)}</div>
        <div className="mt-result-body">
          {job?.diagnostics && job.status !== 'completed' && <details className="mt-stage-timing"><summary>종료 진단 · 코드 {job.diagnostics.returncode}{job.diagnostics.signal && ` · ${job.diagnostics.signal}`}</summary>
            <p className="mt-hint">Pod 모델 프로세스의 종료 기록입니다. 강제 종료 신호만으로 메모리 부족을 확정할 수는 없습니다.</p>
            {job.diagnostics.log_tail && <pre className="mt-json" tabIndex={0} aria-label="Pod 실행 오류 로그">{job.diagnostics.log_tail}</pre>}
          </details>}
          {resultTab === 'debug' ? <div className="mt-debug">
            <section aria-labelledby="mt-debug-request"><h3 id="mt-debug-request">실행 설정</h3>{contextRef.current && <p className="mt-hint">요청 ID: {contextRef.current.requestId}</p>}<JsonView value={manifest} /></section>
            <section aria-labelledby="mt-debug-response"><h3 id="mt-debug-response">전체 응답</h3><JsonView value={job} /></section>
          </div>
              : resultTab === 'timing' ? (job ? <ModeltestTimings job={job} now={Date.now()} /> : <p className="mt-empty">실행 후 단계별·모델별 소요 시간이 표시됩니다.</p>)
              : !job ? <div className="mt-result-empty"><Icon name="search" /><h3>첫 검수 결과를 기다리고 있어요</h3><p>Pod 연결 → 자료 첨부 → 모델 지정 후 검수를 실행해 주세요.</p></div>
                : <div className="mt-stage-results"><nav aria-label="결과 단계 선택">{job.stages.map(stage => <button type="button" key={stage.id} aria-pressed={selectedStage === stage.id} onClick={() => setSelectedStage(stage.id)}>{stageTitle(stage)}{stage.agent_id && <small className="mt-stage-time">{job.agents?.find(agent => agent.id === stage.agent_id)?.name}</small>}</button>)}</nav>
                  <div className="mt-stage-output"><p className="mt-hint">{resultStage ? STATUS_LABELS[resultStage.status] : '결과를 볼 단계를 선택하세요.'}{typeof resultStage?.model_id === 'string' ? ` · ${resultStage.model_id}` : ''}</p>
                    {resultStage && <details className="mt-stage-timing"><summary>단계 소요 시간 {formatDuration(elapsedSeconds(resultStage))}{(resultStage.status === 'running' || resultStage.timing_estimated) && ' · 추정'}</summary><ModelTimingTable stages={[resultStage]} now={Date.now()} /></details>}
                    {typeof resultStage?.error === 'string' && <p className="mt-error">{resultStage.error}</p>}
                    {typeof resultStage?.detail === 'string' && <p className="mt-hint" role="status">{resultStage.detail}</p>}
                    {manifest && <ModeltestResults key={`${job.id}-${selectedStage}`} job={job} stage={resultStage} manifest={manifest} context={contextRef.current} cache={previewCache}
                      selection={resultSelections[resultSelectionKey] || {}}
                      onSelect={patch => setResultSelections(previous => ({ ...previous, [resultSelectionKey]: { ...previous[resultSelectionKey], ...patch } }))}
                      onSeek={manifest.flow === 'video' && flow === 'video' && state.media === contextRef.current?.media ? seconds => {
                        if (videoRef.current) { videoRef.current.pause(); videoRef.current.currentTime = seconds; videoRef.current.scrollIntoView({ behavior: 'auto', block: 'center' }); }
                      } : null} />}
                  </div></div>}
        </div>
      </section>
      <p className="mt-result-guidance">임계값·유사도는 검수 통과 확률이 아니며, 이미지 코사인 및 특징점 지표는 일치 확률이 아닙니다. 실행 성공은 품질 검증을 뜻하지 않습니다. 실행 환경과 품질은 별도 확인이 필요합니다.</p>
      <details className="mt-api-help mt-page-help"><summary>페이지 도움말</summary><h3>Pod 연결과 작업 복원</h3>{method === 'ssh' ? <><p>노트북의 로컬 백엔드가 OpenSSH로 접속합니다. 최초 접속은 터미널에서 서버 지문을 확인하고, 암호화된 키는 ssh-agent에 등록해 주세요.</p><p>TCP SSH는 일반 데이터 통신을, 기본 SSH는 터미널을 통한 전송을 사용합니다. 큰 영상에는 TCP SSH를 권장합니다.</p><p>OWLv2는 로고 추출, SigLIP2는 후보 문구별 시각 유사도, XFeat는 로고 대조, Qwen3.5는 문서·이미지 분석에 연결됩니다. 기존 Transformers 생성 모델과 Grounding DINO도 지원 범위에서 사용할 수 있습니다.</p><p>실행 ID로 조회를 복원하며, 백엔드를 재시작했다면 같은 Pod에 연결 후 이전 작업을 조회하세요.</p></>
              : <><p>HTTP 방식은 전용 검수 API가 필요합니다. 기존 OCR 워커나 일반 채팅 API에는 직접 연결되지 않습니다.</p><p>GET /api/modeltest/health<br />POST /api/modeltest/runs<br />GET /api/modeltest/runs/:id</p><p>Pod에서 현재 화면의 출처({window.location.origin})를 CORS로 허용해야 합니다.</p><p>RunPod 계정 API 키가 아닌 Pod 검수 API 토큰을 입력하세요.</p></>}<h3>검수 자료와 결과</h3><p>필요한 단계를 선택하고 모델을 지정하세요.</p><p>소유자 없는 모델명은 받지 않습니다. 연결 후에는 Pod가 제공한 모델도 선택할 수 있습니다.</p><p>{method === 'ssh' ? '실행하면 자료·검수 스크립트를 Pod로 전송하고 선택한 모델을 다운로드·실행합니다.' : '실행하면 첨부 자료와 모델 설정을 연결된 Pod로 전송합니다.'}</p><p>실행한 모델의 출력과 요청 설정을 확인합니다. 프레임을 선택하면 로고 위치와 원본 대조 결과를 볼 수 있습니다.</p><p>실행 설정은 실행 당시 설정입니다. 파일 내용과 인증 토큰은 포함하지 않습니다.</p><p>모델·revision·처리 설정이 같은 벡터만 비교합니다.</p><p>유사도, 재정렬 점수, 분류 점수는 서로 다른 지표입니다.</p><p>새 모델은 상단 모델 관리에서 등록하세요. 등록 모델은 선택 상자로 설정을 유지합니다.</p><p>입력한 모델의 로딩·추론은 Pod에서 처리합니다. 프레임을 선택하면 로고 위치와 원본 대조 결과를 볼 수 있습니다.</p></details>
      <footer className="mt-footer"><span>크리에이티브체커 · 모델 테스트</span></footer>
    </main>
    <ModeltestConfirm request={confirmation} onResolve={accepted => { setConfirmation(null); if (accepted) { if (confirmation.action === 'cancel') cancel(); else confirmation.action(); } }} />
  </div>;
}
