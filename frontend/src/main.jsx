import React, { useEffect, useReducer, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { initialState, workflowReducer, canConfirm, canRun, parseRequirements, evidenceBoxStyle } from './workflow.js';
import './styles.css';

const labels = { input: '자료 입력', criteria: '기준 확인', running: '검수 실행', results: '결과 확인' };
const statuses = { pass: '충족', fail: '위반', review: '확인 필요' };
const stageLabels = { queued: '검수 순서를 기다리고 있어요.', preprocessing: '이미지를 읽기 좋은 크기로 준비하고 있어요.', ocr: '이미지에서 문구와 위치를 읽고 있어요.', review: '확정한 기준과 추출 문구를 비교하고 있어요.', completed: '검수를 완료했어요.', failed: '검수를 완료하지 못했어요.' };
const iconPaths = {
  check: <path d="m5 12 4 4L19 6" />,
  upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5" /></>,
  search: <><circle cx="10" cy="10" r="6" /><path d="m15 15 6 6" /></>,
  list: <><path d="M9 6h12M9 12h12M9 18h12M3 6h1M3 12h1M3 18h1" /></>,
  shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z" /><path d="m8 12 3 3 5-6" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></>,
  lock: <><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" /></>,
  image: <><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8" cy="8" r="1" /><path d="m3 17 6-6 4 4 3-3 5 5" /></>,
  warning: <><path d="m12 3 10 18H2L12 3ZM12 9v5M12 17h.01" /></>,
  grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  home: <><path d="m3 10 9-7 9 7M5 9v12h14V9M9 21v-8h6v8" /></>,
  arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
  file: <><path d="M14 2H5v20h14V7l-5-5Zm0 0v6h5M8 12h8M8 16h8" /></>,
};
function Icon({ name, ...props }) { return <svg viewBox="0 0 24 24" className="icon" aria-hidden="true" {...props}>{iconPaths[name] || iconPaths.check}</svg>; }
function Brand({ onClick }) { return <button className="brand brand-button" onClick={onClick} aria-label="바로검수 홈"><span className="brand-mark"><Icon name="check" /></span>바로검수</button>; }
function ErrorMessage({ message }) { return message ? <p className="error error-box" role="alert"><Icon name="warning" />{message}</p> : null; }
function Heading({ step, title, children }) { return <div className="page-heading"><div className="eyebrow">STEP {String(step).padStart(2, '0')} · {labels[['input', 'criteria', 'running', 'results'][step - 1]]}</div><h1 tabIndex="-1" id="page-title">{title}</h1><p>{children}</p></div>; }
function Summary({ projectName, title, count, countLabel = '선택한 검수 항목', rows, children }) { return <div className="summary-card"><p className="summary-overline">{projectName || '새로운 검수'}</p><h2>{title}</h2><div className="summary-total"><span>{countLabel}</span><strong>{count}<span>개</span></strong></div><div className="summary-rows">{rows.map(([label, value]) => <div className="summary-row" key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>{children}</div>; }

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail || body?.error || `요청 실패 (${response.status})`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  if (!body) throw new Error('서버 응답을 읽을 수 없습니다. 검수 서버 연결을 확인해 주세요.');
  return body;
}

async function validateImage(file, limits = {}) {
  const maxBytes = limits.upload_bytes || 20 * 1024 * 1024;
  const maxPixels = limits.image_pixels || 10_000_000;
  const maxSide = limits.image_side || 8192;
  if (!file) throw new Error('검수할 이미지 1개를 선택해 주세요.');
  if (file.size > maxBytes) throw new Error(`이미지는 ${Math.floor(maxBytes / 1024 / 1024)}MiB 이하로 올려 주세요.`);
  const bytes = new Uint8Array(await file.slice(0, 12).arrayBuffer());
  const isPng = [137, 80, 78, 71, 13, 10, 26, 10].every((b, i) => bytes[i] === b);
  const isJpg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
  const isWebp = String.fromCharCode(...bytes.slice(0, 4)) === 'RIFF' && String.fromCharCode(...bytes.slice(8, 12)) === 'WEBP';
  if (!isPng && !isJpg && !isWebp) throw new Error('JPG, PNG, WEBP 이미지 파일만 사용할 수 있습니다. 확장자와 실제 내용을 확인해 주세요.');
  const decoded = await createImageBitmap(file).catch(() => { throw new Error('이미지를 열 수 없습니다. 손상되지 않은 파일을 선택해 주세요.'); });
  const dimensions = { width: decoded.width, height: decoded.height };
  decoded.close();
  if (dimensions.width * dimensions.height > maxPixels || Math.max(dimensions.width, dimensions.height) > maxSide) {
    throw new Error(`이미지는 ${(maxPixels / 10_000).toLocaleString()}만 픽셀 이하, 한 변 ${maxSide.toLocaleString()}px 이하로 줄여 주세요.`);
  }
  return dimensions;
}

function Landing({ start, sample, busy, error }) {
  return <section aria-label="서비스 소개">
    <header className="landing-nav"><Brand onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })} /><nav className="nav-links" aria-label="메인 메뉴"><a href="#features">서비스 소개</a><a href="#how-it-works">이용 방법</a><button className="btn primary" onClick={start}>검수 시작하기</button></nav></header>
    <div className="landing-hero"><div className="hero-grid"><div className="hero-copy"><span className="pill">이미지 · 포스터 검수 자동화</span><h1>광고물 검수,<br /><em>기준부터 확실하게.</em></h1><p>이미지와 요구사항을 올리고 검수 기준을 확정하세요.<br />문구부터 시각 요소까지, 놓치기 쉬운 항목을<br className="desktop-break" /> 한곳에서 확인할 수 있습니다.</p><div className="hero-actions"><button className="btn primary" onClick={start}>검수 시작하기<Icon name="arrow" /></button><button className="btn secondary" onClick={sample} disabled={busy}>{busy ? '샘플 불러오는 중…' : '샘플로 체험하기'}</button></div><p className="hero-footnote"><Icon name="shield" />확정한 기준과 실제 이미지 근거를 함께 확인하세요.</p><ErrorMessage message={error} /></div>
      <div className="hero-preview" aria-label="김천김밥축제 샘플과 검수 순서"><div className="preview-top"><span>김천김밥축제 · 포스터 검수</span><span className="pill">실행 전</span></div><div className="preview-body"><div className="landing-sample"><img src="/api/sample/image" alt="2026 김천김밥축제 샘플 포스터" /><span>검수 대상 이미지</span></div><div className="preview-list"><div className="preview-label">내가 정하는 검수 기준</div>{[['01', '이미지와 요구사항', '한 줄에 하나씩, 구체적인 조건'], ['02', '검수 기준 확정', '정보와 시각 항목을 직접 선택'], ['03', '문구와 위치 추출', '실제 이미지에서 근거 확인'], ['04', '항목별 결과 검토', '충족 · 위반 · 확인 필요']].map(([num, title, detail]) => <div className="preview-line" key={num}><span className="preview-number">{num}</span><div><strong>{title}</strong><small>{detail}</small></div></div>)}</div></div><div className="preview-note"><Icon name="lock" />기준을 확정한 뒤 검수를 실행합니다.</div><div className="preview-float"><Icon name="search" /><div>작은 오류도, 근거와 함께<small>항목별 결과와 위치를 한눈에</small></div></div></div>
    </div></div>
    <section className="landing-section" id="features"><div className="section-heading"><div><span className="eyebrow">WHY 바로검수</span><h2>반복 확인은 줄이고, 기준은 명확하게.</h2></div><p>납품 전 확인부터 수정 후 재검수까지</p></div><div className="feature-grid">{[['list', '요구사항을 개별 검수 항목으로', '행사명, 기간, 장소 등의 요구사항을 항목별로 정리하고 필요한 기준을 직접 선택합니다.'], ['search', '결과를 이미지 근거와 함께', '이미지에서 읽은 문구와 위치를 확인합니다. 자동 판단이 어려운 시각 요소는 담당자가 검토합니다.'], ['shield', '확정한 기준으로 일관된 검수', '선택한 검수 기준을 버전으로 고정합니다. 기준을 변경하면 다시 확정하고 새로 검수합니다.']].map(([icon, title, text]) => <article key={icon}><div className="feature-icon"><Icon name={icon} /></div><h3>{title}</h3><p>{text}</p></article>)}</div></section>
    <section className="how-section" id="how-it-works"><div className="landing-section"><div className="section-heading"><div><span className="eyebrow">HOW IT WORKS</span><h2>검수 시작까지, 간단한 네 단계.</h2></div><button className="text-btn" onClick={sample} disabled={busy}>샘플로 직접 확인하기</button></div><div className="how-grid">{[['이미지 업로드', '검수할 이미지나 포스터를 올립니다.'], ['요구사항 입력', '확인할 문구와 시각 조건을 입력합니다.'], ['검수 기준 확정', '개별 항목을 검토하고 선택합니다.'], ['결과와 근거 확인', '확정한 기준으로 실제 검수를 실행합니다.']].map(([title, text], i) => <article className="how-item" key={title}><span>0{i + 1}</span><h3>{title}</h3><p>{text}</p></article>)}</div></div></section>
    <footer className="landing-footer"><span className="brand">바로검수</span><span>OCR 근거 기반 검수 · 최종 승인은 담당자가 결정합니다.</span><span>SKN33 · 1TEAM</span></footer>
  </section>;
}

function App() {
  const [state, dispatch] = useReducer(workflowReducer, initialState);
  const [landing, setLanding] = useState(true);
  const [health, setHealth] = useState(null);
  const [healthBusy, setHealthBusy] = useState(false);
  const [sampleBusy, setSampleBusy] = useState(false);
  const [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
  const [pollRetry, setPollRetry] = useState(0);
  const [preview, setPreview] = useState('');
  const [dimensions, setDimensions] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [newCriterion, setNewCriterion] = useState('');
  const [newCategory, setNewCategory] = useState('information');
  const [filter, setFilter] = useState('all');
  const [activeEvidence, setActiveEvidence] = useState(null);
  const fileInput = useRef(null);
  const generation = useRef(0);
  const submitting = useRef(false);
  const fileValidation = useRef(0);
  const busy = state.step === 'running' && ['queued', 'running', 'submitting'].includes(state.review?.status);
  const locked = Boolean(state.snapshot);
  const selected = state.criteria.filter(item => item.selected);
  const selectedInfo = selected.filter(item => item.type === 'information').length;
  const versionText = locked ? `v${state.snapshot.version} 확정` : state.version ? `재확정 필요 · 이전 v${state.version}` : '확정 전';

  async function checkHealth() {
    setHealthBusy(true);
    try {
      const body = await fetchJson('/api/health');
      setHealth({ ...body, ...(body.runtime || {}) });
    }
    catch { setHealth({ status: 'offline', ready: false }); }
    finally { setHealthBusy(false); }
  }
  useEffect(() => { checkHealth(); }, []);
  useEffect(() => {
    if (!state.file) { setPreview(''); return; }
    const url = URL.createObjectURL(state.file); setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [state.file]);
  useEffect(() => {
    if (!landing) document.getElementById('page-title')?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [state.step, landing]);
  useEffect(() => {
    const id = state.review?.id;
    if (!id || state.step !== 'running' || state.review.status === 'failed') return;
    let cancelled = false;
    let timer;
    const controller = new AbortController();
    async function poll() {
      try {
        const review = await fetchJson(`/api/reviews/${encodeURIComponent(id)}`, { signal: controller.signal });
        if (cancelled) return;
        setPollError(''); dispatch({ type: 'progress', review });
        if (review.status === 'completed') { setFilter('all'); setActiveEvidence(null); }
        else if (review.status !== 'failed') timer = setTimeout(poll, 1200);
      } catch (err) { if (!cancelled) setPollError(`진행 상태를 가져오지 못했습니다. ${err.message}`); }
    }
    poll();
    return () => { cancelled = true; controller.abort(); clearTimeout(timer); };
  }, [state.review?.id, state.step, pollRetry]);

  function inputChange(field, value) { generation.current += 1; setError(''); dispatch({ type: 'input', field, value }); }
  async function selectFile(files) {
    setError('');
    if (files.length !== 1) { setError('이미지는 한 번에 1개만 선택해 주세요.'); return; }
    const request = ++fileValidation.current;
    try {
      const dims = await validateImage(files[0], health?.limits);
      if (request !== fileValidation.current) return;
      inputChange('file', files[0]); setDimensions(dims);
    } catch (err) { setError(err.message); }
    if (fileInput.current) fileInput.current.value = '';
  }
  async function sample() {
    if (sampleBusy) return;
    setSampleBusy(true); setError('');
    const epoch = ++generation.current;
    try {
      const [metadata, response] = await Promise.all([fetchJson('/api/sample'), fetch('/api/sample/image')]);
      if (!response.ok) throw new Error('샘플 이미지를 불러오지 못했습니다. 검수 서버 연결을 확인해 주세요.');
      const blob = await response.blob();
      const file = new File([blob], '2026_김천_김밥축제.png', { type: blob.type || 'image/png' });
      const dims = await validateImage(file, health?.limits);
      if (epoch !== generation.current) return;
      dispatch({ type: 'sample', file, projectName: metadata.project_name, requirements: metadata.requirements });
      setDimensions(dims); setLanding(false);
    } catch (err) { setError(err.message); }
    finally { setSampleBusy(false); }
  }
  function prepare(event) {
    event?.preventDefault(); setError('');
    if (!state.file) { setError('검수할 이미지를 먼저 선택해 주세요.'); return; }
    if (!state.requirements.trim()) { setError('검수 요구사항을 한 줄에 하나씩 입력해 주세요.'); return; }
    if (parseRequirements(state.requirements).some(item => item.text.length > 500)) { setError('각 검수 항목은 500자 이내로 입력해 주세요. 긴 요구사항은 여러 줄로 나눠 주세요.'); return; }
    if (parseRequirements(state.requirements).length > 100) { setError('한 번에 최대 100개 항목을 입력할 수 있습니다.'); return; }
    dispatch({ type: 'prepare' });
  }
  function confirm() {
    if (!canConfirm(state)) { setError('내용이 있는 검수 항목을 1개 이상 선택해 주세요.'); return; }
    setError(''); dispatch({ type: 'confirm' });
  }
  function unlock() { generation.current += 1; setError(''); setPollError(''); dispatch({ type: 'unlock' }); }
  async function run() {
    if (!canRun(state) || submitting.current) return;
    submitting.current = true; setError(''); setPollError('');
    const epoch = ++generation.current;
    dispatch({ type: 'run', review: { status: 'submitting', stage: 'queued', progress: 0 } });
    const form = new FormData();
    form.append('image', state.file); form.append('criteria', JSON.stringify(state.snapshot.criteria));
    form.append('criteria_version', String(state.snapshot.version)); form.append('project_name', state.projectName || '새로운 검수');
    try {
      const review = await fetchJson('/api/reviews', { method: 'POST', body: form });
      if (epoch === generation.current) dispatch({ type: 'run', review });
    } catch (err) {
      if (epoch === generation.current) dispatch({ type: 'run', review: { status: 'failed', stage: 'failed', progress: 0, error: err.message } });
    } finally { submitting.current = false; }
  }
  function addCriterion(event) {
    event.preventDefault();
    if (!newCriterion.trim()) return;
    if (state.criteria.length >= 100) { setError('검수 항목은 최대 100개까지 추가할 수 있습니다.'); return; }
    dispatch({ type: 'add', id: `custom-${crypto.randomUUID()}`, text: newCriterion, category: newCategory }); setNewCriterion(''); setError('');
  }
  function navigate(step) { setError(''); dispatch({ type: 'navigate', step }); }
  function newReview() { generation.current += 1; dispatch({ type: 'reset' }); setError(''); setPollError(''); setDimensions(null); }

  if (landing) return <Landing start={() => { setError(''); setLanding(false); }} sample={sample} busy={sampleBusy} error={error} />;

  return <div className="app-shell"><aside className="sidebar"><Brand onClick={() => setLanding(true)} /><div className="workspace-name">WORKSPACE</div><div className="workspace-label"><Icon name="grid" />내 검수 프로젝트</div><div className="side-divider" /><p className="side-title">새로운 검수</p><nav className="step-nav" aria-label="검수 단계">{Object.entries(labels).map(([key, label], i) => {
    const enabled = key === 'input' || key === 'criteria' && state.criteria.length > 0 || key === 'running' && state.review && state.review.status !== 'completed' || key === 'results' && state.review?.status === 'completed';
    const done = i < Object.keys(labels).indexOf(state.step);
    return <button key={key} className={`step-button ${key === state.step ? 'current' : done ? 'done' : ''}`} disabled={!enabled || busy && key !== state.step} onClick={() => navigate(key)} aria-current={key === state.step ? 'step' : undefined}><span className="step-number">{done ? <Icon name="check" /> : i + 1}</span>{label}</button>;
  })}</nav><div className="sidebar-bottom"><button className="back-home" onClick={() => setLanding(true)}><Icon name="home" />서비스 메인으로</button><p>확정한 기준으로 검수합니다.<br />자료는 연결된 검수 서버로 전송되어 처리됩니다.</p></div></aside>
    <div className="app-area"><header className="app-topbar"><div className="breadcrumb"><span>내 검수 프로젝트</span><span aria-hidden="true">/</span><span>{labels[state.step]}</span></div><div className="topbar-right"><button className={`connection ${health?.status === 'offline' ? 'offline' : health?.ready ? 'online' : 'pending'}`} onClick={checkHealth} disabled={healthBusy} title="연결 상태 새로 확인"><span className="connection-dot" />{healthBusy || !health ? '연결 확인 중' : health.status === 'offline' ? '서버 연결 필요' : health.ready ? '검수 서버 연결됨' : '서버 연결 · 준비 필요'}</button><span className="user-label">검수 담당자</span><span className="avatar" aria-label="검수 담당자">G</span></div></header>
    <main className="app-main">
    {state.step === 'input' && <section aria-labelledby="page-title"><Heading step={1} title="어떤 광고물을 검수할까요?">이미지와 요구사항을 입력하면 선택 가능한 검수 항목으로 정리합니다.</Heading><div className="work-grid"><div><form className="form-card" onSubmit={prepare}><div className="card-heading"><h2>검수 자료 입력</h2><span className="pill">이미지 · 포스터</span></div><div className="form-body"><div className="field"><div className="field-label"><label htmlFor="project-name">1. 프로젝트 이름<span className="optional">선택</span></label></div><input id="project-name" value={state.projectName} onChange={e => inputChange('projectName', e.target.value)} maxLength={60} placeholder="예: 김천김밥축제 포스터 검수" autoComplete="off" /></div>
    <div className="field"><div className="field-label"><h3>2. 검수할 이미지 <b>*</b></h3><button type="button" className="text-btn" onClick={sample} disabled={sampleBusy}>{sampleBusy ? '불러오는 중…' : '샘플 불러오기'}</button></div><input type="file" ref={fileInput} accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" className="sr-only" aria-label="검수 이미지 선택" onChange={e => selectFile([...e.target.files])} />{state.file ? <div className="uploaded-file"><img className="uploaded-thumb" src={preview} alt="업로드한 검수 이미지 미리보기" /><div className="uploaded-info"><strong>{state.file.name}</strong><p>{dimensions ? `${dimensions.width} × ${dimensions.height}px · ` : ''}{(state.file.size / 1024 / 1024).toFixed(2)}MB</p><div className="uploaded-actions"><button type="button" className="text-btn" onClick={() => fileInput.current.click()}>다른 이미지 선택</button><button type="button" className="text-btn" onClick={() => { inputChange('file', null); setDimensions(null); }}>삭제</button></div></div></div> : <div className={`upload-zone ${dragging ? 'dragover' : ''}`} onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); selectFile([...e.dataTransfer.files]); }}><div className="upload-icon"><Icon name="upload" /></div><strong>이미지를 끌어다 놓으세요</strong><p>검수할 이미지 1개를 올려 주세요.</p><button type="button" className="btn secondary" onClick={() => fileInput.current.click()}>파일 선택</button><small>JPG, PNG, WEBP · 최대 {Math.floor((health?.limits?.upload_bytes || 20 * 1024 * 1024) / 1024 / 1024)}MiB<br />{((health?.limits?.image_pixels || 10_000_000) / 10_000).toLocaleString()}만 픽셀 · 한 변 {(health?.limits?.image_side || 8192).toLocaleString()}px 이하</small></div>}</div>
    <div className="field"><div className="field-label"><label htmlFor="requirements">3. 검수 요구사항 <b>*</b></label><span className="optional">한 줄에 한 항목</span></div><div className="input-hint"><Icon name="info" /><span>문구는 ‘행사명: 김천김밥축제’처럼, 시각 조건은 구체적으로 적어 주세요.</span></div><textarea id="requirements" value={state.requirements} maxLength={6000} rows={8} onChange={e => inputChange('requirements', e.target.value)} placeholder={'행사명: 김천김밥축제\n기간: 2026.10.23 ~ 10.25\n장소: 직지문화공원\n김천시 로고가 포함되어야 합니다.'} aria-describedby="requirements-hint" /><div className="textarea-counter"><span id="requirements-hint">다음 단계에서 각 항목을 수정하거나 제외할 수 있어요.</span><span>{state.requirements.length.toLocaleString()} / 6,000</span></div></div><ErrorMessage message={error} /></div><div className="form-actions"><p><Icon name="lock" />기준을 확정한 뒤 검수가 실행됩니다.</p><button type="submit" className="btn primary" disabled={sampleBusy}>검수 항목 확인하기<Icon name="arrow" /></button></div></form><div className="flow-note"><Icon name="info" />입력한 요구사항을 줄 단위로 정리합니다.</div></div>
    <aside className="summary-column"><Summary projectName="새로운 검수" title="검수 준비 현황" count={parseRequirements(state.requirements).length} countLabel="입력한 요구사항" rows={[["프로젝트", state.projectName || '새로운 검수'], ['검수 이미지', state.file ? '1개 등록' : '미등록'], ['요구사항', state.requirements.trim() ? '입력 완료' : '미입력'], ['검수 기준', versionText]]}><button className="btn white" onClick={prepare} disabled={sampleBusy}>검수 항목 확인하기</button><p className="help">항목을 검토하고 기준을 확정한 뒤 검수를 시작합니다.</p></Summary><div className="side-help"><Icon name="list" /><h3>무엇을 적으면 좋을까요?</h3><p>행사명 · 기간 · 장소 · 필수 문구 같은 정보, 로고 · 색상 · 필수 그림 · 가독성 조건을 입력해 주세요.</p></div></aside></div></section>}

    {state.step === 'criteria' && <section aria-labelledby="page-title"><Heading step={2} title="검수할 항목을 확정해 주세요.">필요한 항목을 선택하고 내용을 확인하세요. 선택한 항목만 검수 기준에 포함됩니다.</Heading>{locked ? <div className="locked-banner"><div><strong><Icon name="lock" /> 검수 기준 v{state.snapshot.version}이 확정되었습니다.</strong><p>검수 시작 시 이 기준 버전을 사용합니다.</p></div><button className="text-btn" onClick={unlock}>기준 다시 편집</button></div> : <div className="criteria-banner"><Icon name="list" /><div><strong>{state.version ? '기준을 수정하고 다시 확정해 주세요.' : '입력한 요구사항을 항목별로 정리했어요.'}</strong><p>{state.version ? '이전 결과는 무효화되었습니다. 재확정 후 새 기준으로 검수할 수 있습니다.' : '문구를 직접 수정할 수 있습니다. 조건이 모호하면 구체적인 값으로 보완해 주세요.'}</p></div></div>}
    <div className="work-grid"><div><div className="form-card"><div className="criteria-toolbar"><span>전체 <b>{state.criteria.length}</b>개 중 <strong>{selected.length}개 선택</strong></span>{!locked && <div className="toolbar-actions"><button className="text-btn" onClick={() => dispatch({ type: 'select', selected: true })}>전체 선택</button><button className="text-btn" onClick={() => dispatch({ type: 'select', selected: false })}>전체 해제</button></div>}</div><div>{state.criteria.map((item, i) => <article className={`criterion-row ${item.selected ? '' : 'excluded'}`} key={item.id}><div className="criterion-head"><label className="criterion-check"><input type="checkbox" checked={item.selected} disabled={locked} onChange={e => dispatch({ type: 'update', id: item.id, patch: { selected: e.target.checked } })} /><span>검수 항목 {String(i + 1).padStart(2, '0')}</span></label><select className="criterion-type" aria-label={`검수 항목 ${i + 1} 유형`} value={item.type} disabled={locked} onChange={e => dispatch({ type: 'update', id: item.id, patch: { type: e.target.value } })}><option value="information">정보 검수</option><option value="visual">시각 검수</option></select></div><textarea aria-label={`검수 항목 ${i + 1} 내용`} value={item.text} maxLength={500} disabled={locked} onChange={e => dispatch({ type: 'update', id: item.id, patch: { text: e.target.value } })} /><div className="criterion-source"><Icon name="file" /><details><summary>요구사항 원문</summary><p>{item.source}</p></details></div></article>)}</div>{!locked && <form className="add-criterion" onSubmit={addCriterion}><input value={newCriterion} onChange={e => setNewCriterion(e.target.value)} maxLength={500} aria-label="추가할 검수 항목" placeholder="필요한 검수 항목 추가" /><select aria-label="추가할 검수 유형" value={newCategory} onChange={e => setNewCategory(e.target.value)}><option value="information">정보 검수</option><option value="visual">시각 검수</option></select><button type="submit" className="btn secondary" disabled={!newCriterion.trim()}>항목 추가</button></form>}<div className="form-actions"><button className="text-btn" onClick={() => navigate('input')}>자료 다시 입력</button>{locked ? <span className="confirmed-label"><Icon name="check" />v{state.snapshot.version} 기준 확정 완료</span> : <button className="btn primary" onClick={confirm} disabled={!canConfirm(state)}>선택한 기준 확정하기</button>}</div></div><ErrorMessage message={error} /></div><aside className="summary-column"><Summary projectName={state.projectName} title="검수 기준 요약" count={selected.length} rows={[["정보 검수", `${selectedInfo}개`], ['시각 검수', `${selected.length - selectedInfo}개`], ['제외한 항목', `${state.criteria.length - selected.length}개`], ['기준 버전', versionText]]}><button className="btn white" onClick={run} disabled={!canRun(state)}>검수 시작하기<Icon name="arrow" /></button><p className="help">{locked ? '확정한 기준 전체로 검수합니다.' : '먼저 선택한 기준을 확정해 주세요.'}</p></Summary><div className="criteria-thumb"><img src={preview} alt="검수 대상 이미지" /><div><strong>{state.file?.name}</strong><p>검수 대상 · 이미지 1개</p></div></div><div className="side-help"><Icon name="lock" /><h3>확정한 기준은 버전으로 고정</h3><p>기준을 변경하면 기존 결과가 무효화됩니다. 다시 확정한 새 버전으로 검수합니다.</p></div></aside></div></section>}

    {state.step === 'running' && <section aria-labelledby="page-title"><Heading step={3} title={state.review?.status === 'failed' ? '검수를 완료하지 못했습니다.' : '확정한 기준으로 검수하고 있어요.'}>선택한 이미지에서 문구와 위치를 읽고, 확정한 기준과 비교합니다.</Heading><div className="work-grid"><div className="form-card run-card"><div className={`run-symbol ${state.review?.status === 'failed' ? 'stopped' : ''}`}><Icon name={state.review?.status === 'failed' ? 'warning' : 'search'} /></div><h2>{stageLabels[state.review?.stage] || '검수 자료를 전달하고 있어요.'}</h2><p aria-live="polite">{state.review?.status === 'failed' ? '오류 내용을 확인하고 같은 기준으로 다시 시도해 주세요.' : '처음 실행할 때는 준비에 시간이 걸릴 수 있습니다.'}</p><div className="progress-track" role="progressbar" aria-label="검수 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={state.review?.progress || 0}><div style={{ width: `${Math.min(100, Math.max(0, state.review?.progress || 0))}%` }} /></div><div className="progress-description"><span>실제 검수 진행 상태</span><span>{state.review?.progress || 0}%</span></div><div className="run-stages">{[['preprocessing', 'file', '이미지와 기준 확인'], ['ocr', 'search', '문구와 위치 읽기'], ['review', 'list', '기준과 추출 문구 비교'], ['completed', 'check', '항목별 결과 정리']].map(([stage, icon, title]) => { const current = ['queued', 'preprocessing', 'ocr', 'review', 'completed'].indexOf(state.review?.stage); const index = ['queued', 'preprocessing', 'ocr', 'review', 'completed'].indexOf(stage); return <div key={stage} className={`run-stage ${current === index ? 'active' : current > index ? 'done' : ''}`}><Icon name={current > index ? 'check' : icon} />{title}</div>; })}</div><ErrorMessage message={typeof state.review?.error === 'string' ? state.review.error : state.review?.error?.message} /><ErrorMessage message={pollError} />{pollError && <button className="btn secondary retry-button" onClick={() => { setPollError(''); setPollRetry(value => value + 1); }}>진행 상태 다시 확인</button>}{state.review?.status === 'failed' && <div className="retry-actions"><button className="btn secondary" onClick={() => navigate('criteria')}>기준 다시 확인</button><button className="btn primary" onClick={run}>같은 기준으로 재시도</button></div>}</div><aside className="summary-column"><Summary projectName={state.projectName} title="확정한 기준으로 실행" count={state.snapshot?.criteria.length || 0} countLabel="검수 대상 항목" rows={[["기준 버전", `v${state.snapshot?.version}`], ['검수 이미지', '1개'], ['실행 상태', state.review?.status === 'failed' ? '실패' : pollError ? '연결 확인 필요' : '진행 중']]}><p className="help">결과에 표시되는 문구와 영역은 이 이미지에서 추출한 근거입니다. 시각 요소는 담당자 확인이 필요할 수 있습니다.</p></Summary></aside></div></section>}

    {state.step === 'results' && state.review?.status === 'completed' && <Results review={state.review} filter={filter} setFilter={value => { setFilter(value); setActiveEvidence(null); }} activeEvidence={activeEvidence} setActiveEvidence={setActiveEvidence} edit={() => navigate('criteria')} newReview={newReview} />}
    </main></div></div>;
}

function Results({ review, filter, setFilter, activeEvidence, setActiveEvidence, edit, newReview }) {
  const results = review.results || [];
  const counts = Object.fromEntries(Object.keys(statuses).map(status => [status, results.filter(row => row.status === status).length]));
  const visible = results.filter(row => filter === 'all' || row.status === filter);
  const image = review.image;
  function exportJson() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(review, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `baro-review-${review.id}.json`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <section aria-labelledby="page-title"><Heading step={4} title="항목별 검수 결과를 확인하세요.">{review.project_name || '새로운 검수'} · 기준 v{review.criteria_version} · 총 {results.length}개 항목</Heading><div className="result-alert"><Icon name={counts.fail || counts.review ? 'warning' : 'check'} /><div><strong>{counts.fail || counts.review ? '담당자 확인이 필요한 항목을 살펴보세요.' : '선택한 정보 기준의 근거를 확인했습니다.'}</strong><p>OCR에서 읽은 문구를 기준과 비교한 결과입니다. ‘확인 필요’ 항목과 최종 승인 여부는 담당자가 검토해 주세요.</p></div></div><div className="result-metrics">{Object.entries(statuses).map(([status, label]) => <div className={`metric ${status}`} key={status}><span>{label}</span><strong>{counts[status]}</strong></div>)}</div><div className="result-grid"><div className="form-card result-image-card"><div className="card-heading"><h2>검수 대상 이미지</h2><span className="pill">기준 v{review.criteria_version}</span></div><div className="result-image-wrapper"><div className="result-canvas"><img src={image?.url} alt="검수 대상 이미지와 선택한 근거 영역" />{activeEvidence?.evidence.map((evidence, i) => { const style = evidenceBoxStyle(evidence.bbox_xyxy, image?.width, image?.height); return style ? <div className="evidence-box" style={style} key={i} title={evidence.text} aria-label={`근거 영역: ${evidence.text}`} /> : null; })}</div></div><div className="result-caption" aria-live="polite">{activeEvidence ? <><strong>선택한 근거</strong><br />{activeEvidence.evidence.map(item => item.text).filter(Boolean).join(' · ')}<button className="text-btn clear-evidence" onClick={() => setActiveEvidence(null)}>표시 해제</button></> : '항목의 ‘위치 보기’를 누르면 이미지에서 해당 영역을 확인할 수 있어요.'}</div></div><div className="form-card"><div className="result-filter" role="group" aria-label="검수 결과 필터">{[['all', '전체', results.length], ['fail', '위반', counts.fail], ['review', '확인 필요', counts.review], ['pass', '충족', counts.pass]].map(([status, label, count]) => <button className={`filter-button ${filter === status ? 'active' : ''}`} aria-pressed={filter === status} key={status} onClick={() => setFilter(status)}>{label}<span>{count}</span></button>)}</div><div className="results-list">{visible.length ? visible.map((result, i) => <article className={`result-item ${activeEvidence?.criterion_id === result.criterion_id ? 'selected-evidence' : ''}`} key={result.criterion_id || i}><div className="result-item-top"><span className={`status-badge ${result.status}`}><Icon name={result.status === 'pass' ? 'check' : 'warning'} />{statuses[result.status] || '확인 필요'}</span><span className="result-category">{result.type === 'visual' ? '시각 검수' : '정보 검수'}</span></div><h3>{result.criterion}</h3><p className="result-reason">{result.reason}</p>{result.observed_text && <div className="observed-text"><span>이미지에서 읽은 문구</span><p>{result.observed_text}</p></div>}{result.evidence?.length > 0 && <div className="evidence-actions"><button className="text-btn" onClick={() => setActiveEvidence({ criterion_id: result.criterion_id, evidence: result.evidence })}><Icon name="search" />위치 보기 · {result.evidence.length}곳</button></div>}</article>) : <div className="empty-results"><Icon name="check" /><p>이 상태에 해당하는 항목이 없습니다.</p></div>}</div></div></div><div className="result-bottom"><p>검수 실행 완료는 최종 승인을 의미하지 않습니다.</p><div className="result-actions"><button className="btn secondary" onClick={exportJson}>결과 JSON 저장</button><button className="btn secondary" onClick={edit}>기준 다시 확인하기</button><button className="btn primary" onClick={newReview}>새 검수 시작하기</button></div></div></section>;
}

createRoot(document.getElementById('root')).render(<App />);
