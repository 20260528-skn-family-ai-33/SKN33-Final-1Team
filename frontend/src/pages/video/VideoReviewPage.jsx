import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { fetchJson } from '../../api/client.js';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { WorkspaceFooter, WorkspaceTopbar } from '../../layouts/ReviewLayout.jsx';
import { useWorkspaceReset } from '../../workspace/WorkspaceSession.jsx';
import { useLogoJobs } from './LogoFlowParts.jsx';
import VideoInput from './VideoInput.jsx';
import VideoProgress from './VideoProgress.jsx';
import VideoResults from './VideoResults.jsx';
import { isTerminal } from './videoFormat.js';
import '../../styles/review-editor-v2.css';
import '../../styles/review-progress.css';
import '../../styles/review-results-v2.css';
import '../../styles/image-stage.css';
import './videoReview.css';

// 서버 준비 상태와 최근 동영상 검수 목록을 함께 불러온다.
function useVideoStatus() {
  const [status, setStatus] = useState(null);
  const [jobs, setJobs] = useState([]);
  const active = useRef(null);
  const refresh = useCallback(async () => {
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    try {
      const [ready, list] = await Promise.all([fetchJson('/api/video-reviews/status', { signal: controller.signal }), fetchJson('/api/video-reviews', { signal: controller.signal })]);
      if (controller.signal.aborted) return;
      setStatus(ready); setJobs(list.items || []);
    } catch (error) { if (!controller.signal.aborted) setStatus({ ready: false, detail: error.message }); }
  }, []);
  useEffect(() => { refresh(); return () => active.current?.abort(); }, [refresh]);
  return { status, jobs, refresh };
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
  return { job, error, missing };
}

const steps = [['input', '자료 입력'], ['running', '검수 진행'], ['results', '결과 확인']];
const blankDraft = () => ({ projectName: '숏폼 검수', requirements: '', ad: { kind: 'endorsement', celebrity_exposure: false, title: '', caption: '', platform: '' } });

// 동영상을 올려 계획 문서의 0~7단계로 검수하는 화면. 포스터 검수와 같은 자료 입력 → 검수 진행 → 결과 확인 흐름을 따른다.
export default function VideoReviewPage() {
  const [params, setParams] = useSearchParams();
  const id = params.get('id');
  const navigate = useNavigate();
  const resetWorkspace = useWorkspaceReset();
  const [draft, setDraft] = useState(blankDraft);
  // 고른 영상·원본 로고는 최근 검수를 열어 보고 돌아와도 남는다. 상단 메뉴 이동(작업 초기화)이나 새 검수에서만 비운다.
  const [file, setFile] = useState(null);
  const [logos, setLogos] = useState([]);
  const [logoSubmissionWarning, setLogoSubmissionWarning] = useState(null);
  const { status, jobs, refresh } = useVideoStatus();
  const { job: loaded, error, missing } = useVideoJob(id, refresh);
  const logo = useLogoJobs(id);
  // id가 바뀐 직후 이전 작업이 한 번 그려지지 않도록 현재 id의 작업만 쓴다.
  const job = loaded?.id === id ? loaded : null;
  const legacy = job && job.status === 'completed' && !job.coverage;
  const step = !id ? 'input' : job?.status === 'completed' && !legacy ? 'results' : 'running';
  const stepIndex = steps.findIndex(([key]) => key === step);
  const placeholder = step === 'running' && !job && !error;
  const submissionWarning = id && logoSubmissionWarning?.id === id ? logoSubmissionWarning.message : '';
  const listRunning = jobs.some(item => item.status === 'queued' || item.status === 'running');

  // 자료 입력 화면에서는 진행 중인 최근 검수의 상태가 멈춰 보이지 않도록 목록을 다시 읽는다.
  useEffect(() => {
    if (step !== 'input' || !listRunning) return undefined;
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, [step, listRunning, refresh]);
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
  function startOver() { setDraft(blankDraft()); setFile(null); setLogos([]); backToInput(); }
  function reuse() {
    if (!job) return;
    setDraft({ projectName: job.project_name || '숏폼 검수', requirements: (job.criteria || []).map(item => item.text).join('\n'),
      ad: { ...blankDraft().ad, ...(job.disclosure_input || {}) } });
    backToInput();
  }
  const enabled = key => key === 'input' || (key === 'running' && step === 'running') || (key === 'results' && step === 'results');
  const projectName = job?.project_name || (step === 'input' ? draft.projectName.trim() : '') || '새로운 영상 검수';

  return <div className={`rv-workspace rv-video ${step === 'results' ? 'rv-workspace--results rv-video--results' : 'rv-workspace--tool'}`}>
    <a href="#review-content" className="rv-skip">작업 영역으로 이동</a>
    <WorkspaceTopbar current="/video" />
    <div className="rv-projectbar"><div className="rv-project-identity">
      <button type="button" className="rv-icon-button" aria-label="서비스 메인으로" onClick={() => { resetWorkspace(); navigate('/'); }}><Icon name="grid" /></button>
      <div><span className="rv-eyebrow">내 검수 프로젝트</span><strong>{projectName}</strong></div>
      <span className="rv-version">{job ? `기준 v${job.criteria_version}` : '작업 중'}</span></div>
      <nav className="rv-step-nav" aria-label="검수 단계">{steps.map(([key, label], index) => <button type="button" key={key} disabled={!enabled(key)}
        className={`${key === step ? 'is-active' : ''} ${index < stepIndex ? 'is-done' : ''}`} aria-current={key === step ? 'step' : undefined}
        onClick={() => { if (key === 'input' && step !== 'input') backToInput(); }}>
        <span>{index < stepIndex ? <Icon name="check" /> : String(index + 1).padStart(2, '0')}</span>{label}</button>)}</nav>
    </div>
    <main id="review-content" className="rv-main" tabIndex={-1}>
      {/* 결과 화면은 화면 높이를 꽉 채우므로 로고 접수 경고를 머리글의 로고 요약에 넣는다. */}
      {submissionWarning && step !== 'results' && <div className="rv-video-page-alert"><ErrorMessage message={submissionWarning} /></div>}
      {step === 'input' && <VideoInput status={status} jobs={jobs} refresh={refresh} draft={draft} setDraft={setDraft} currentId={id} onOpen={openJob}
        file={file} setFile={setFile} logos={logos} setLogos={setLogos}
        onSubmitted={(value, warning) => { setLogoSubmissionWarning(warning ? { id: value, message: warning } : null); openJob(value); refresh(); }} />}
      {step === 'running' && (legacy ? <section className="rv-progress-page" aria-labelledby="page-title"><div className="rv-progress-card rv-video-progress-card is-failed">
        <span className="rv-eyebrow">PREVIOUS FORMAT</span><h1 id="page-title" tabIndex={-1}>이 결과는 새 화면으로 보여 줄 수 없어요.</h1>
        <ErrorMessage message="계획 문서 반영 전에 만든 결과라 새 화면으로 보여 줄 수 없습니다. 같은 영상을 다시 검수해 주세요." />
        <div className="rv-progress-actions"><button type="button" className="rv-button primary" onClick={backToInput}>새 동영상으로 다시 검수<Icon name="arrow" /></button></div>
      </div></section> : <VideoProgress job={job} error={error} missing={missing} logo={logo} onRestart={backToInput} />)}
      {step === 'results' && <VideoResults key={job.id} job={job} logo={logo} logoWarning={submissionWarning} onNewReview={startOver} onReuse={reuse} />}
    </main>
    <WorkspaceFooter />
  </div>;
}
