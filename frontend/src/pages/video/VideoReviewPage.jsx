import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { fetchJson } from '../../api/client.js';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { logoSteps, WorkspaceProjectBar } from '../../layouts/ReviewLayout.jsx';
import { SiteHeader } from '../../components/SiteHeader.jsx';
import { DocumentProjectPicker } from '../review/DocumentInput.jsx';
import ReviewTypePicker, { SecurityPicker } from '../review/ReviewTypePicker.jsx';
import { useLogoJobs } from './LogoFlowParts.jsx';
import VideoInput from './VideoInput.jsx';
import VideoProgress from './VideoProgress.jsx';
import VideoResults from './VideoResults.jsx';
import VideoReport from './VideoReport.jsx';
import { isTerminal } from './videoFormat.js';
import '../../styles/review-editor-v2.css';
import '../../styles/review-progress.css';
import '../../styles/review-results-v2.css';
import '../../styles/image-stage.css';
import './videoReview.css';

// 접수에 필요한 서버 준비 상태를 불러온다. 기존 작업은 내 작업에서 조회한다.
function useVideoStatus() {
  const [status, setStatus] = useState(null);
  const active = useRef(null);
  const refresh = useCallback(async () => {
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    try {
      const ready = await fetchJson('/api/video-reviews/status', { signal: controller.signal });
      if (controller.signal.aborted) return;
      setStatus(ready);
    } catch (error) { if (!controller.signal.aborted) setStatus({ ready: false, detail: error.message }); }
  }, []);
  useEffect(() => { refresh(); return () => active.current?.abort(); }, [refresh]);
  return { status, refresh };
}

// 끝날 때까지 작업 상태를 주기적으로 다시 읽는다. 없는 기록(404)이면 조회를 멈추고 missing으로 알린다.
function useVideoJob(id, onFinish) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState('');
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    if (!id) { setJob(null); setError(''); setMissing(false); return undefined; }
    const controller = new AbortController();
    let timer;
    async function poll() {
      try {
        const value = await fetchJson(`/api/video-reviews/${encodeURIComponent(id)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setJob(value); setError(''); setMissing(false);
        if (isTerminal(value.status)) onFinish(); else timer = setTimeout(poll, 1500);
      } catch (failure) {
        if (controller.signal.aborted) return;
        setError(failure.message);
        // 일시적인 연결 오류는 다시 확인하고, 없는 기록(404)만 멈춘다.
        if (failure.status === 404) setMissing(true); else timer = setTimeout(poll, 3000);
      }
    }
    setJob(null); setError(''); setMissing(false); poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [id, onFinish]);
  return { job, error, missing, setJob };
}

const steps = { input: '자료 입력', running: '검수 진행', results: '결과 확인', report: '보고서' };
// securityMode('secure'|'general')·reviewMode('text'|'logo'|'both')는 포스터 검수와 같은 키오스크 선택(ReviewTypePicker.jsx).
const blankDraft = () => ({ securityMode: null, reviewMode: null, projectName: '', requirements: '', documentRows: [], documentName: '',
  ad: { kind: 'endorsement', celebrity_exposure: false, title: '', caption: '', platform: '' } });
const securityFromRoute = route => route === 'openai' ? 'general' : route === 'runpod' ? 'secure' : null;

// 동영상을 올려 계획 문서의 0~7단계로 검수하는 화면. 포스터 검수와 같은 자료 입력 → 검수 진행 → 결과 확인 흐름을 따른다.
export default function VideoReviewPage() {
  const [params, setParams] = useSearchParams();
  const id = params.get('id');
  const [draft, setDraft] = useState(blankDraft);
  const [uploading, setUploading] = useState(false);
  const [documentBusy, setDocumentBusy] = useState(false);
  const [projectId, setProjectId] = useState('');
  const [projectStatus, setProjectStatus] = useState({ ready: false, busy: true });
  const projectPicker = useRef(null);
  const [file, setFile] = useState(null);
  const [logos, setLogos] = useState([]);
  const [logoSubmissionWarning, setLogoSubmissionWarning] = useState(null);
  const { status, refresh } = useVideoStatus();
  const { job: loaded, error, missing, setJob } = useVideoJob(id, refresh);
  const logo = useLogoJobs(id);
  // id가 바뀐 직후 이전 작업이 한 번 그려지지 않도록 현재 id의 작업만 쓴다.
  const job = loaded?.id === id ? loaded : null;
  const legacy = job && job.status === 'completed' && !job.coverage;
  const completed = job?.status === 'completed' && !legacy;
  const step = !id ? 'input' : completed ? params.get('view') === 'report' ? 'report' : 'results' : 'running';
  const inputLocked = step === 'input' && (uploading || documentBusy);
  const placeholder = step === 'running' && !job && !error;
  const submissionWarning = id && logoSubmissionWarning?.id === id ? logoSubmissionWarning.message : '';
  // 자료 입력 전에 보안 여부 → 검수 유형을 고른다(포스터와 같은 흐름). 고르기 전에는 단계 표시를 숨긴다.
  const picker = step !== 'input' ? null : !draft.securityMode ? 'security' : !draft.reviewMode ? 'type' : null;
  const logoOnly = step === 'input' && draft.reviewMode === 'logo';
  const shownPicker = useRef(picker);
  useEffect(() => {
    if (shownPicker.current === picker) return;
    shownPicker.current = picker;
    document.getElementById('page-title')?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [picker]);
  const shownStep = useRef(null);

  // 사용자가 단계를 바꾸면 새 화면의 제목으로 초점을 옮겨 화면 낭독기가 바뀐 내용을 읽게 한다.
  // 처음 열 때와 기록을 불러오는 잠깐의 빈 화면에서는 초점을 옮기지 않는다.
  useEffect(() => {
    if (placeholder) return;
    if (shownStep.current && shownStep.current !== step) document.getElementById('page-title')?.focus({ preventScroll: true });
    shownStep.current = step;
  }, [step, placeholder]);

  const openJob = value => setParams({ id: value });
  const backToInput = () => { setParams({}); refresh(); };
  function startOver() { setDraft(blankDraft()); setProjectId(''); setFile(null); setLogos([]); backToInput(); }
  function reuse(revision = false) {
    if (!job) return;
    const criteria = job.criteria || [];
    const extracted = item => typeof item.source === 'string' && item.source !== 'user' && item.source !== item.text;
    setFile(null);
    setLogos([]);
    setProjectId(job.workflow?.project_id || '');
    // 다시 검수는 같은 보안 여부로, 기준을 다시 쓰므로 문구 검수로 연다(로고는 다시 첨부하면 '유형 바꾸기'로 바꿀 수 있다).
    setDraft({ ...blankDraft(), securityMode: securityFromRoute(job.model_route), reviewMode: 'text',
      projectName: job.project_name || '숏폼 검수', parentRunId: revision ? job.id : null,
      requirements: criteria.filter(item => !extracted(item)).map(item => item.text).join('\n'),
      documentRows: criteria.filter(extracted).map((item, index) => ({ ...item, id: `reused-${index}`, selected: true })),
      ad: { ...blankDraft().ad, ...(job.disclosure_input || {}) } });
    backToInput();
  }
  const enabled = key => key === 'input' || key === 'running' && step === 'running' || completed && (key === 'results' || key === 'report');
  const openReport = () => setParams({ id, view: 'report' });
  const openResults = () => setParams({ id });
  function changeStep(key) {
    if (key === step || inputLocked) return;
    if (key === 'input') backToInput();
    else if (key === 'report') openReport();
    else if (key === 'results') openResults();
  }

  return <div className={`rv-workspace rv-video rv-workspace--tool rv-video--${step}`}>
    <a href="#review-content" className="rv-skip">작업 영역으로 이동</a>
    <SiteHeader current="/video" locked={inputLocked} lockReason="처리 중에는 화면을 이동할 수 없습니다." />
    {inputLocked && <span id="mt-lock-reason" className="sr-only">처리 중에는 화면을 이동할 수 없습니다.</span>}
    <WorkspaceProjectBar name={step === 'input' ? draft.projectName : job?.project_name} version={job ? `기준 v${job.criteria_version}` : '작업 중'}
      step={step} steps={picker ? null : logoOnly ? logoSteps : steps} canNavigate={enabled} navigate={changeStep} locked={inputLocked}
      projectEditor={step === 'input' ? { id: 'video-project-name', maxLength: 120, disabled: inputLocked,
        onChange: value => setDraft(previous => ({ ...previous, projectName: value })),
        // 검수 이름 칸에서 Enter를 누르면 그 이름으로 저장할 프로젝트를 만들고 선택한다(로고만 검수는 프로젝트 없음).
        onEnter: logoOnly ? undefined : name => projectPicker.current?.createFromName(name) } : undefined}
      projectTools={step !== 'input' ? undefined : logoOnly
        // 로고만 검수는 동영상 검수 작업 없이 로고 작업만 접수하므로 프로젝트에 저장하지 않는다.
        ? <div className="rv-project-field"><span className="rv-project-field-label">저장할 프로젝트</span>
          <span className="rv-project-none"><Icon name="folder" />로고 검수는 프로젝트 없이 진행돼요</span></div>
        : <DocumentProjectPicker ref={projectPicker} projectId={projectId} setProjectId={setProjectId}
          projectName={draft.projectName} disabled={inputLocked || !!draft.parentRunId} onStatus={setProjectStatus}
          // 저장된 프로젝트를 고르면 그 이름을 검수 이름에도 넣는다(반대 방향: 검수 이름 Enter → 프로젝트 만들기).
          onPick={project => setDraft(previous => ({ ...previous, projectName: project.name.slice(0, 120) }))} />}>
      {step === 'report' && <button type="button" className="rv-button secondary" onClick={openResults}>결과 확인으로 돌아가기</button>}
    </WorkspaceProjectBar>
    <main id="review-content" className="rv-main" tabIndex={-1}>
      {/* 결과 화면은 화면 높이를 꽉 채우므로 로고 접수 경고를 머리글의 로고 요약에 넣는다. */}
      {submissionWarning && step === 'running' && <div className="rv-video-page-alert"><ErrorMessage message={submissionWarning} /></div>}
      {picker === 'security' && <section className="rv-input-page" aria-labelledby="page-title">
        <SecurityPicker disabled={inputLocked} onSelect={value => setDraft(previous => ({ ...previous, securityMode: value }))} /></section>}
      {picker === 'type' && <section className="rv-input-page" aria-labelledby="page-title">
        <ReviewTypePicker disabled={inputLocked} security={draft.securityMode}
          onSelect={value => setDraft(previous => ({ ...previous, reviewMode: value }))}
          onBack={() => setDraft(previous => ({ ...previous, securityMode: null }))} /></section>}
      {step === 'input' && !picker && <VideoInput status={status} draft={draft} setDraft={setDraft}
        busy={uploading} setBusy={setUploading}
        projectId={projectId} projectReady={projectStatus.ready && !projectStatus.busy} documentBusy={documentBusy} setDocumentBusy={setDocumentBusy}
        file={file} setFile={setFile} logos={logos} setLogos={setLogos}
        onSubmitted={(value, warning) => { setLogoSubmissionWarning(warning ? { id: value, message: warning } : null); openJob(value); refresh(); }} />}
      {step === 'running' && (legacy ? <section className="rv-progress-page" aria-labelledby="page-title"><div className="rv-progress-card rv-video-progress-card is-failed">
        <span className="rv-eyebrow">PREVIOUS FORMAT</span><h1 id="page-title" tabIndex={-1}>이 결과는 새 화면으로 보여 줄 수 없어요.</h1>
        <ErrorMessage message="계획 문서 반영 전에 만든 결과라 새 화면으로 보여 줄 수 없습니다. 같은 영상을 다시 검수해 주세요." />
        <div className="rv-progress-actions"><button type="button" className="rv-button primary" onClick={backToInput}>새 동영상으로 다시 검수<Icon name="arrow" /></button></div>
        </div></section> : <VideoProgress job={job} error={error} missing={missing} logo={logo}
          onRestart={() => job?.workflow && job.status === 'failed' ? reuse(true) : backToInput()} />)}
      {step === 'results' && <VideoResults key={job.id} job={job} logo={logo} logoWarning={submissionWarning}
        onNewReview={startOver} onReuse={() => reuse()} onRevise={() => reuse(true)} onOpenRun={openJob} onDecision={setJob} onReport={openReport} />}
      {step === 'report' && <VideoReport job={job} />}
    </main>
  </div>;
}
