import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router';
import ReviewLayout from '../../layouts/ReviewLayout.jsx';
import InputPage from './InputPage.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import ImageStage from '../../components/ImageStage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { usePaged } from '../../components/Pager.jsx';
import ResultsPage from './ResultsPage.jsx';
import { frameworkResults } from './frameworkResults.js';
import { criterionLine } from './criterionLabel.js';
import ReviewProgress from './ReviewProgress.jsx';
import FrameworkRunStatus from './FrameworkRunStatus.jsx';
import { frameworkProgress, processingStatuses } from './frameworkProgress.js';
import CriteriaAmendment from './CriteriaAmendment.jsx';
import CriteriaWorkspace from './CriteriaWorkspace.jsx';
import CriteriaPanel from './CriteriaPanel.jsx';
import FrameworkReport from './FrameworkReport.jsx';
import PosterTextEditor from './PosterTextEditor.jsx';
import { posterPlaceCandidates } from './posterMapQr.js';
import { incompleteOutputPages, unreadTextPages } from './criteriaWarnings.js';
import RevisionHistory from './RevisionHistory.jsx';
import PosterEvidenceDialog from './PosterEvidenceDialog.jsx';
import { frameworkPrefix, frameworkJson, requirePosterReview } from './frameworkClient.js';
import { useWorkspaceReset } from '../../workspace/WorkspaceSession.jsx';
import '../../styles/review-editor-v2.css';
import { useAuth } from '../../auth/AuthProvider.jsx';
import { isAnonymousError } from '../../auth/helpers.js';
import { PosterLogoCard, usePosterLogoJobs } from './PosterLogoParts.jsx';
import { previousLogoJob, submitPosterLogoJob } from './posterLogoFlow.js';
import './posterLogo.css';

// 로고 접수 실패 안내는 새 실행 화면으로 이동한 뒤에도 보이도록 실행 번호별로 잠시 보관한다.
const logoNoticeKey = runId => 'poster-logo-notice:' + runId;
function readLogoNotice(runId) {
  try { return sessionStorage.getItem(logoNoticeKey(runId)) || ''; } catch { return ''; }
}

const running = processingStatuses;
const actionStatus = {
  start: '기준을 승인하고 있어요.',
  'start-skip': '기준을 승인하고 있어요.',
  'decide-start': '포스터 검수를 접수하고 있어요.',
};
// 검수 항목 목록은 CRITERIA_PER_PAGE개씩 쪽으로 넘겨 본다.
const CRITERIA_PER_PAGE = 6;
// 기준 번호(c001 같은 내부 번호) 대신 화면에 적을 이름
function criterionTitle(criterion) {
  if (!criterion) return '검수 항목';
  const line = criterionLine(criterion);
  return line.label ? [line.label, line.expected].filter(Boolean).join(' ') : criterion.text;
}
function command(path, body, key, signal) {
  return frameworkJson(path, { method: 'POST', signal, body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json', ...(key ? { 'Idempotency-Key': key } : {}) } });
}
// 목록에서 체크를 푼 기준과, 값이 정해지지 않아 빼고 시작한 기준에 남기는 제외 사유.
// 첫 문구는 서버가 직접 입력을 더하면서 고르지 않은 문서 기준을 뺄 때 쓰는 문구와 같다(criteria_review.py).
const NOT_SELECTED = '자료 입력에서 검수 대상으로 선택하지 않았습니다.';
const UNRESOLVED_SKIPPED = '담당자가 확인 필요 항목을 제외하고 검수를 시작했습니다.';

// 포스터 검수의 자료 입력 화면과, 접수된 검수 한 건(실행)의 진행 · 결과 · 보고서 화면.
// 자료 입력은 한 화면이다: 실행이 아직 없을 때(runId 없음)부터 문서를 추출해 초안이 생긴 뒤(승인 전)까지 같은 InputPage를 그리고,
// 초안의 검수 항목은 그 화면의 한 칸으로 들어간다. '검수 시작하기'가 기준 승인과 검수 접수까지 한다.
// input(선택): 자료 입력 한 화면에 필요한 것. ReviewPage가 넘긴다.
//   page InputPage 속성 · layout 머리글(검수 이름·저장할 프로젝트)과 준비 창 · file 고른 포스터 · typed 직접 입력한 요구사항
//   addTyped(text, { runId, criterionIds }) 직접 입력을 분석해 이 실행의 기준에 더한다(성공하면 true)
//   fresh 방금 이 화면에서 만든 실행 · autoStart 직접 입력만으로 시작해, 기준이 준비되면 바로 접수 · setProject(id) 이 실행의 프로젝트로 맞춤
export default function FrameworkReviewPage({ runId, initialFile, projectName, openRun, onInput, onNew, referenceLogos,
  logoDetector = 'runpod', health, reviewStarting = false, input = null }) {
  const { user } = useAuth();
  const path = '/runs/' + encodeURIComponent(runId);
  const [run, setRun] = useState(null);
  const [overview, setOverview] = useState(null);
  const [loadState, setLoadState] = useState('checking');
  const [shownRunId, setShownRunId] = useState(runId);
  const initialStart = useRef(reviewStarting);
  const autoStarted = useRef(false);
  // 승인 뒤 포스터 등록·수정본 재검수에서 이 화면이 직접 받은 포스터. 승인 전 초안에서는 자료 입력의 포스터(input.file)를 쓴다.
  const [pickedFile, setFile] = useState(initialFile);
  const [excluded, setExcluded] = useState([]);
  const [amendment, setAmendment] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(null);
  const [reload, setReload] = useState(0);
  const [showCriteria, setShowCriteria] = useState(false);
  const [reportTarget, setReportTarget] = useState(null);
  const [activeId, setActiveId] = useState(null);
  const [detailOpen, setDetailOpen] = useState(true);   // 고른 항목의 상세를 그 항목 아래에 펼쳤는가
  const [quoteOpen, setQuoteOpen] = useState(false);    // 펼친 상세에서 원문 전체를 보는가
  const [filter, setFilter] = useState('all');
  const [activeEvidence, setActiveEvidence] = useState(null);
  const [preview, setPreview] = useState(null);
  // 기준 승인 뒤 검은 미리보기 칸에 포스터를 넣는다(파일 선택·끌어다 놓기).
  const posterInput = useRef(null);
  const [posterDragging, setPosterDragging] = useState(false);
  const [posterError, setPosterError] = useState('');
  const [imageSize, setImageSize] = useState(null);
  const [editingPoster, setEditingPoster] = useState(null);
  const [revisions, setRevisions] = useState(null);
  const [retrieval, setRetrieval] = useState(null);
  const [evidenceOpened, setEvidenceOpened] = useState(false);
  const [chainRuns, setChainRuns] = useState({});
  const [logoNotice] = useState(() => readLogoNotice(runId));
  // 실행 조회가 계정 권한으로 허용된 뒤에만 관련 로고 작업을 요청한다.
  const logoJobs = usePosterLogoJobs(loadState === 'ready' ? runId : null);
  const [secureMaterial, setSecureMaterial] = useState(false);
  const active = useRef(null);
  useWorkspaceReset(() => active.current?.abort());
  const keys = useRef(new Map());
  const uploaded = useRef(null);
  const amendmentForm = useRef(null);
  // 자료 입력 화면은 실행이 바뀌어도 같은 화면으로 남는다(문서를 추출했거나 다른 문서로 바꿨을 때). 앞 실행의 내용과 선택은 그리기 전에 버린다.
  if (shownRunId !== runId) {
    setShownRunId(runId);
    setRun(null); setOverview(null); setLoadState('checking');
    setExcluded([]); setAmendment(null); setActiveId(null); setError('');
  }
  const latest = overview?.latest_version;
  const status = run?.execution_status;
  const sourceDraft = status === 'waiting_criteria' && !run.baseline;
  // 승인 전 초안은 자료 입력 한 화면으로 그린다(포스터·직접 입력·문서 첨부와 검수 항목이 한곳에 있다).
  const draftInput = sourceDraft && Boolean(input);
  const file = draftInput ? input.file : pickedFile || (status === 'waiting_asset' && !run?.baseline && input?.file) || null;
  const criteria = latest?.version.criteria || run?.criteria?.criteria || [];
  const unreadPages = unreadTextPages(run?.criteria?.warnings);
  const incompletePages = incompleteOutputPages(run?.criteria?.warnings);
  const reviewScope = [
    criteria.some(item => item.kind !== 'visual') && '문구·값',
    criteria.some(item => item.kind === 'visual') && '시각 조건',
  ].filter(Boolean).join(' · ');
  const selected = criteria.find(item => item.criterion_id === (amendment?.criterion_id || activeId)) || criteria[0];
  const criteriaPages = usePaged(criteria, CRITERIA_PER_PAGE);
  const imageUrl = run?.baseline && run.context.asset_version_id
    ? frameworkPrefix + '/images/' + encodeURIComponent(run.context.asset_version_id) + '/content' : null;
  const image = useMemo(() => ({ url: imageUrl, ...(imageSize?.url === imageUrl ? imageSize : {}) }), [imageUrl, imageSize]);
  const resultView = useMemo(() => run ? frameworkResults(run, image, projectName) : null, [run, image, projectName]);
  const hasResults = !!run?.validated;
  const evidenceId = run?.evidence?.context_id;
  const processing = loadState === 'ready' && running.has(status);
  const progress = processing ? frameworkProgress(run) : null;
  const stageStatus = actionStatus[pending] || (progress && !error
    ? run ? progress.message : '검수 상태를 불러오고 있어요.'
    : null);
  const showingReport = !processing && hasResults && !showCriteria && Boolean(reportTarget);
  const step = processing ? 'running' : showingReport ? 'report' : hasResults && !showCriteria ? 'results' : 'criteria';
  const finalApproved = status === 'completed' && run?.human_decision === 'final_approved';
  const exceptionApproved = status === 'completed' && run?.human_decision === 'exception_approved';
  const canReturnToInput = !busy && !processing && Boolean(onInput);
  const canRevise = status === 'waiting_review' || status === 'waiting_revision';
  const canEditPoster = hasResults && Boolean(imageUrl) && (canRevise || status === 'completed');
  // 문구 수정은 포스터 일부를 OpenAI 이미지 편집으로 보낸다. 보안 자료 실행은 서버가 거절하므로 버튼도 막는다.
  const canEditText = canEditPoster && health?.poster_text_edit_ready === true && !secureMaterial;
  const textEditBlock = secureMaterial ? '보안 자료는 외부 이미지 편집(OpenAI)으로 보내지 않아 문구 수정을 쓸 수 없어요.'
    : !canEditText ? '문구 수정 서버 준비 상태를 확인해 주세요.' : undefined;

  useEffect(() => () => active.current?.abort(), []);
  // 이 실행이 보안 자료인지 서버에 묻는다. 조회가 안 되면(이전 서버) 버튼은 그대로 두고, 실제 요청은 서버가 다시 막는다.
  useEffect(() => {
    if (!canEditPoster) return undefined;
    const controller = new AbortController();
    frameworkJson(path + '/poster-text-edits', { signal: controller.signal })
      .then(body => { if (!controller.signal.aborted) setSecureMaterial(body?.allowed === false); })
      .catch(() => {});
    return () => controller.abort();
  }, [path, canEditPoster]);
  useEffect(() => {
    if (!evidenceId?.startsWith('rag-')) return undefined;
    const controller = new AbortController();
    frameworkJson(path + '/evidence-context', { signal: controller.signal })
      .then(body => { if (!controller.signal.aborted) setRetrieval(body.retrieval); })
      .catch(() => {});
    return () => controller.abort();
  }, [path, evidenceId]);
  useEffect(() => {
    document.getElementById(step === 'running' ? 'review-process-title' : 'page-title')?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
    // 자료 입력 화면에서 검수 항목이 나타나는 것은 단계가 바뀌는 것이 아니다(step이 그대로라 입력하던 칸의 초점을 뺏지 않는다).
  }, [step]);
  useEffect(() => {
    if (!file) { setPreview(null); return; }
    const url = URL.createObjectURL(file);
    setPreview({ file, url });
    return () => URL.revokeObjectURL(url);
  }, [file]);
  useEffect(() => {
    if (!imageUrl) return;
    const img = new Image();
    img.onload = () => setImageSize({ url: imageUrl, width: img.naturalWidth, height: img.naturalHeight });
    img.onerror = () => setImageSize({ url: imageUrl, unavailable: true });
    img.src = imageUrl;
    return () => { img.onload = null; img.onerror = null; };
  }, [imageUrl]);
  useEffect(() => { setEditingPoster(null); keys.current = new Map(); autoStarted.current = false; }, [runId]);
  useEffect(() => {
    if (!runId) return undefined;   // 아직 실행이 없다(자료 입력 중)
    const controller = new AbortController();
    let timer;
    let hasSnapshot = false;
    async function read() {
      try {
        const snapshot = await frameworkJson(path, { signal: controller.signal });
        const info = !snapshot.baseline && ['waiting_criteria', 'waiting_asset'].includes(snapshot.execution_status)
          ? await frameworkJson(path + '/criteria-review', { signal: controller.signal }) : null;
        if (controller.signal.aborted) return;
        hasSnapshot = true;
        initialStart.current = false;
        setRun(snapshot); setOverview(info); setError(''); setLoadState('ready');
        if (running.has(snapshot.execution_status)) timer = setTimeout(read, 1000);
      } catch (err) {
        if (controller.signal.aborted || isAnonymousError(err)) return;
        initialStart.current = false;
        if ([403, 404].includes(err.status)) {
          setRun(null); setOverview(null); setError(''); setLoadState('denied');
          return;
        }
        setError('검수 상태를 확인하지 못했습니다. 연결 상태를 확인한 뒤 다시 시도해 주세요.');
        setLoadState(hasSnapshot ? 'ready' : 'unavailable');
      }
    }
    setLoadState('checking');
    read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [path, reload]);

  // 초안을 열면 저장할 프로젝트 칸을 이 실행의 프로젝트로 맞춘다(직접 입력을 더할 때 같은 프로젝트에 저장된다).
  const draftProject = draftInput ? run.context.project_id : null;
  const setDraftProject = input?.setProject;
  useEffect(() => { if (draftProject) setDraftProject?.(draftProject); }, [draftProject, setDraftProject]);
  // 직접 입력만으로 시작한 경우: 기준이 준비되면 묻지 않고 승인·접수까지 이어 간다. 값이 빈 기준이 있으면 startDraft가 멈추고 그 항목을 보여 준다.
  useEffect(() => {
    if (!input?.autoStart || autoStarted.current || loadState !== 'ready' || !draftInput || !overview || !file) return;
    autoStarted.current = true;
    perform(signal => startDraft(signal), 'start');
  });

  // 기준 확인 화면에는 이전 기준과의 차이 · 이미지 미판독 · 원문 검증 제외 후보 안내를 두지 않는다.
  // 수정본 검수 이력(관계 조회)은 부가 안내다. 실패해도 검수 흐름을 막지 않는다.
  const posterRun = Boolean(run?.baseline) && !processing;
  const humanDecision = run?.human_decision;
  useEffect(() => {
    if (!posterRun) { setRevisions(null); return; }
    const controller = new AbortController();
    frameworkJson(path + '/revision-history', { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setRevisions(value); }).catch(() => {});
    return () => controller.abort();
  }, [path, posterRun, status, humanDecision]);
  // 판정 근거 창을 처음 열 때 같은 수정본 이력의 다른 검수 결과를 읽는다(가까운 8건까지). 못 읽은 검수는 버전별 판정에서 빠진다.
  const history = revisions?.run_id === runId ? revisions : null;
  const chainKey = evidenceOpened ? (history?.entries || []).filter(entry => !entry.current).map(entry => entry.run_id).slice(-8).join('\n') : '';
  useEffect(() => {
    setChainRuns(value => (Object.keys(value).length ? {} : value));
    if (!chainKey) return undefined;
    const controller = new AbortController();
    Promise.all(chainKey.split('\n').map(id => frameworkJson('/runs/' + encodeURIComponent(id), { signal: controller.signal })
      .then(snapshot => [id, snapshot]).catch(() => null)))
      .then(found => { if (!controller.signal.aborted) setChainRuns(Object.fromEntries(found.filter(Boolean))); });
    return () => controller.abort();
  }, [chainKey]);

  function requestKey(name) {
    if (!keys.current.has(name)) keys.current.set(name, crypto.randomUUID());
    return keys.current.get(name);
  }
  // key는 누른 버튼 이름이다. 처리하는 동안 그 버튼에만 회전 표시(aria-busy)를 띄운다.
  async function perform(action, key = null) {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller; setBusy(true); setPending(key); setError('');
    try { await action(controller.signal); }
    catch (err) { if (!controller.signal.aborted) setError(err.code && err.code !== 'http_error' ? err.message + ' (' + err.code + ')' : err.message); }
    finally { if (!controller.signal.aborted) { active.current = null; setBusy(false); setPending(null); } }
  }
  const working = key => (busy && pending === key) || undefined;
  // 비활성 버튼에 마우스를 올리면 누를 수 없는 이유를 알려 준다.
  const posterBlock = !file ? '먼저 검수할 포스터 파일을 선택해 주세요.' : undefined;
  // 미리보기 칸에서 고르거나 끌어다 놓은 포스터. 한 장의 JPG·PNG·WEBP만 받고, 크기 제한은 등록할 때 서버가 확인한다.
  const posterTypes = ['image/png', 'image/jpeg', 'image/webp'];
  function pickPoster(files) {
    if (posterInput.current) posterInput.current.value = '';
    if (files.length !== 1) { setPosterError('포스터는 한 번에 1장만 넣어 주세요.'); return; }
    if (!posterTypes.includes(files[0].type)) { setPosterError('JPG · PNG · WEBP 이미지만 등록할 수 있어요.'); return; }
    setPosterError(''); setFile(files[0]);
  }
  // 기준 버전을 새로 저장한다. drop: 목록에서 체크를 푼 기준 · unresolved: 값이 정해지지 않아 빼고 시작하는 기준 · amend: 기준 보완 한 건.
  // 체크를 푼 기준은 검수를 시작할 때 한꺼번에 저장한다. 미리 저장하면 다시 체크할 수 없다(저장한 제외는 되돌리지 못한다).
  async function saveDraftVersion(signal, base, { drop = [], unresolved = [], amend = null } = {}) {
    const exclude = (id, reason, reference) => ({ action: 'exclude', criterion_id: id, reason, source: { reference, quote: reason } });
    const body = { parent_version_id: base?.version.criteria_version_id || null,
      expected_base_sha256: base?.sha256 || overview.source_draft_sha256, purpose: 'human_review',
      edits: [...drop.map(id => exclude(id, NOT_SELECTED, '문서 검수 항목 선택')),
        ...unresolved.filter(id => !drop.includes(id)).map(id => exclude(id, UNRESOLVED_SKIPPED, '담당자 검수 시작 선택')),
        ...(amend ? [amend] : [])] };
    const version = await command(path + '/criteria-versions', body, requestKey(JSON.stringify(body)), signal);
    setOverview(current => ({ ...current, latest_version: version }));
    return version;
  }
  // 기준 보완 한 건을 저장한다. 목록의 체크 상태는 건드리지 않는다.
  async function saveAmendment(signal) {
    if (!amendment || !amendmentForm.current.reportValidity()) return;
    await saveDraftVersion(signal, latest, { amend: amendment });
    setAmendment(null);
  }
  async function approveVersion(signal, version) {
    const body = { criteria_version_id: version.version.criteria_version_id, expected_sha256: version.sha256,
      purpose: 'human_review', reviewed_all_criteria: true, reason: '표시된 기준 전체와 원문을 확인하고 승인했습니다.' };
    await command(path + '/criteria-approvals', body, requestKey(JSON.stringify(body)), signal);
  }
  async function registerPoster(signal, version = latest, navigate = true) {
    if (!file) return;
    await requirePosterReview({ signal });
    // 같은 파일을 같은 프로젝트에 이미 올렸으면 다시 올리지 않는다(자료 입력 화면은 문서를 바꿔도 그대로 남으므로 프로젝트도 함께 본다).
    if (uploaded.current?.file !== file || uploaded.current.project !== run.context.project_id) {
      const form = new FormData(); form.append('image', file);
      const image = await frameworkJson('/projects/' + encodeURIComponent(run.context.project_id) + '/images',
        { method: 'POST', body: form, signal });
      uploaded.current = { file, id: image.id, project: run.context.project_id };
    }
    const revision = status === 'waiting_revision';
    const baseline = revision ? run.baseline : { criteria_version_id: version.version.criteria_version_id, criteria_sha256: version.sha256 };
    const body = { criteria_version_id: baseline.criteria_version_id, expected_criteria_sha256: baseline.criteria_sha256,
      asset_version_id: uploaded.current.id };
    const child = await command(path + (revision ? '/poster-revisions' : '/poster-reviews'), body, requestKey(JSON.stringify(body)), signal);
    await attachPosterLogos(child.context.run_id, revision, signal);
    if (navigate && !signal.aborted) openRun(child.context.run_id);
    return child;
  }
  // 원본 로고를 첨부했으면 같은 포스터로 포스터 로고 작업을 만든다. 수정본은 이전 실행의 원본 로고·기준 문구를 다시 쓴다.
  // 로고 작업 접수가 실패해도 포스터 검수는 그대로 진행한다.
  // 위치 후보 탐지 경로는 보안 여부를 따른다(일반 자료 openai, 보안 자료 runpod). 수정본은 이전 작업의 경로를 쓴다.
  async function attachPosterLogos(childRunId, revision, signal) {
    try {
      const previous = revision ? await previousLogoJob(runId, { signal }) : null;
      const logos = previous ? previous.logos : referenceLogos || [];
      if (!logos.length) return;
      await submitPosterLogoJob({ poster: file, logos, title: (projectName || '포스터') + ' 로고 검수', runId: childRunId,
        detector: previous ? previous.detector : logoDetector, signal });
    } catch {
      // 실패 원인(예: Not Found)은 화면에 보이지 않고 안내 문구만 남긴다.
      if (signal.aborted) return;
      try {
        sessionStorage.setItem(logoNoticeKey(childRunId),
          '포스터 로고 검수를 접수하지 못했어요. 기준 문구 검수는 그대로 진행합니다');
      } catch { /* 브라우저 저장소를 쓸 수 없으면 안내만 생략한다 */ }
    }
  }
  // 자료 입력 한 화면의 '검수 시작하기': (직접 입력 분석 →) 기준 버전 저장 → 승인 → 포스터 등록 → 검수 접수.
  // skipUnresolved: 값이 정해지지 않은 '확인 필요' 항목을 이번 검수에서 빼고 진행한다.
  async function startDraft(signal, skipUnresolved = false) {
    if (!file) return;
    await requirePosterReview({ signal });
    if (amendment && !amendmentForm.current.reportValidity()) return;
    let version = latest;
    let drop = excluded;
    if (input.typed) {
      // 직접 입력을 분석해 기준에 더한다. 문서에서 나온 기준 가운데 체크를 푼 것은 이 요청에서 함께 빠진다.
      const fromDocument = criteria.filter(item => item.source.kind !== 'direct_input').map(item => item.criterion_id);
      const kept = fromDocument.filter(id => !excluded.includes(id));
      const added = await input.addTyped(input.typed, { runId, criterionIds: kept.length === fromDocument.length ? null : kept });
      if (!added || signal.aborted) return;
      const fresh = await frameworkJson(path + '/criteria-review', { signal });
      setOverview(fresh);
      version = fresh.latest_version;
      drop = excluded.filter(id => version.version.criteria.some(item => item.criterion_id === id));
      setExcluded(drop);
    }
    if (!version || drop.length || amendment) {
      version = await saveDraftVersion(signal, version, { drop, amend: amendment });
      setExcluded([]); setAmendment(null);
    }
    let unresolved = Object.keys(version.approval_blockers);
    if (unresolved.length && skipUnresolved) {
      version = await saveDraftVersion(signal, version, { unresolved });
      unresolved = Object.keys(version.approval_blockers);
    }
    // 값이 정해지지 않은 기준이 남으면 승인하지 않고 멈춘다. 화면이 그 항목을 짚어 준다.
    if (unresolved.length) { showCriterion(unresolved[0], version.version.criteria); return; }
    await approveVersion(signal, version);
    try {
      const child = await registerPoster(signal, version, false);
      try {
        await command('/runs/' + encodeURIComponent(child.context.run_id) + '/approvals', {
          context: child.context, actor_id: child.context.actor_id, decision: 'approve_criteria', reason: '',
        }, null, signal);
      } finally {
        if (!signal.aborted) openRun(child.context.run_id, true);
      }
    } catch (failure) {
      // 기준은 이미 승인됐다. 포스터 등록에서 멈췄으면 서버 상태(승인됨 · 포스터 대기)를 다시 읽어 그 화면에서 이어 가게 한다.
      if (!signal.aborted) setReload(count => count + 1);
      throw failure;
    }
  }
  // 승인한 기준과 등록한 포스터로 검수를 접수한다. 보고서 화면의 담당자 승인(최종 승인·수정 요청·예외 승인)은
  // 2026-11-17 베타 피드백으로 없앴다. 담당자는 보고서의 확인 필요 항목마다 코멘트만 남긴다(FrameworkReport).
  async function startReview(signal) {
    await requirePosterReview({ signal });
    const snapshot = await command(path + '/approvals', { context: run.context, actor_id: run.context.actor_id,
      decision: 'approve_criteria', reason: '' }, null, signal);
    setRun(snapshot);
    if (running.has(snapshot.execution_status)) setReload(n => n + 1);
  }

  const registeringPoster = status === 'waiting_asset';
  const pickingPoster = registeringPoster && !imageUrl;
  const readyToStart = status === 'waiting_criteria' && Boolean(run?.baseline);
  const hasCriteriaActions = registeringPoster || readyToStart;
  const imageActions = imageUrl && <div className="rv-result-image-actions" role="group" aria-label="검수 이미지 작업">
    {canEditPoster && <>
      <button type="button" className="rv-button primary" disabled={busy || image.unavailable || !canEditText}
        title={textEditBlock} onClick={() => setEditingPoster('text')}>
        <Icon name={secureMaterial ? 'lock' : 'pencil'} />{secureMaterial ? '문구 수정 불가 · 보안 자료' : '포스터 문구 수정'}</button>
      <button type="button" className="rv-button secondary" disabled={busy || image.unavailable || secureMaterial}
        title={secureMaterial ? '보안 자료는 QR 생성을 쓸 수 없어요.' : undefined} onClick={() => setEditingPoster('qr')}>
        {secureMaterial && <Icon name="lock" />}{secureMaterial ? 'QR 생성 불가 · 보안 자료' : 'QR 생성'}</button>
    </>}
    <a className="rv-button secondary" href={imageUrl} target="_blank" rel="noopener noreferrer"
      title="근거 박스 없이 원본 이미지 새 탭에서 열기">원본</a>
  </div>;
  const actionError = error && <>
    <ErrorMessage message={error} />
    <button disabled={busy} onClick={() => { setExcluded([]); setError(''); setReload(n => n + 1); }}>서버 상태 다시 확인</button>
  </>;

  // 목록의 순번(01부터). 확인 필요 단추와 목록이 같은 번호를 쓴다.
  const criterionNumber = id => String(criteria.findIndex(item => item.criterion_id === id) + 1).padStart(2, '0');
  // 다른 쪽에 있는 항목을 고르면 그 쪽으로 넘긴 뒤 보여 준다.
  function showCriterion(id, list = criteria) {
    const index = list.findIndex(item => item.criterion_id === id);
    if (index < 0) return;
    criteriaPages.setPage(Math.floor(index / CRITERIA_PER_PAGE));
    setActiveId(id);
    setDetailOpen(true);
    setQuoteOpen(false);
    requestAnimationFrame(() => document.getElementById('rv-criterion-' + id)?.scrollIntoView({ block: 'nearest' }));
  }
  // 쪽을 넘기면 그 쪽의 첫 항목을 고른다(보완하는 중에는 고른 항목을 바꾸지 않는다).
  function turnCriteriaPage(page) {
    criteriaPages.setPage(page);
    if (!amendment) { setActiveId(criteria[page * CRITERIA_PER_PAGE]?.criterion_id || null); setQuoteOpen(false); }
  }
  // 항목을 누르면 그 항목 아래에 상세를 펼치고, 펼친 항목을 다시 누르면 접는다(보완 중인 항목은 접지 않는다).
  function toggleCriterion(id) {
    if (selected?.criterion_id === id && detailOpen) { if (!amendment) setDetailOpen(false); return; }
    setActiveId(id);
    setDetailOpen(true);
    setQuoteOpen(false);
  }

  function retryRunStatus() {
    setRun(null); setOverview(null); setError(''); setLoadState('checking');
    setReload(value => value + 1);
  }

  // 검수 항목 목록과 고른 항목의 상세(CriteriaPanel). 자료 입력 한 화면(승인 전 초안)과 승인 뒤 기준 확인 화면이 함께 쓴다.
  function renderCriteriaPanel() {
    return <CriteriaPanel criteria={criteria} excluded={excluded} selected={selected}
      detailOpen={detailOpen} onOpen={toggleCriterion} quoteOpen={quoteOpen} onQuote={() => setQuoteOpen(value => !value)}
      onToggle={(id, included) => setExcluded(ids => included ? ids.filter(other => other !== id) : [...ids, id])}
      paged={criteriaPages} onPage={turnCriteriaPage} editable={sourceDraft} busy={busy}
      amendingId={amendment?.criterion_id} blocked={latest?.approval_blockers}
      // 기준 확인 화면에는 이전 기준과의 차이 · 이미지 미판독 · 원문 검증 제외 후보 안내를 두지 않는다.
      notices={(unreadPages.length > 0 || incompletePages.length > 0) && <section className="rv-relation-notice" role="status" aria-label="문서 분석 범위 안내">
        <div className="rv-relation-notice-head"><Icon name="warning" /><div>
          <strong>원문과 기준을 대조해 주세요.</strong>
          {unreadPages.length > 0 && <p>{unreadPages.join(', ')}쪽은 텍스트를 읽지 못했어요.</p>}
          {incompletePages.length > 0 && <p>{incompletePages.join(', ')}쪽은 일부 분석 응답을 받지 못했어요.</p>}
        </div></div>
      </section>}
      detailActions={sourceDraft && selected && (amendment
        ? <form ref={amendmentForm} onSubmit={e => { e.preventDefault(); perform(saveAmendment, 'save'); }}>
          <CriteriaAmendment value={amendment} onChange={setAmendment} disabled={busy} />
          <div className="rv-criteria-amend-actions">
            <button type="submit" className="rv-button primary" disabled={busy} aria-busy={working('save')}>보완 내용 저장</button>
            <button type="button" className="rv-button secondary" disabled={busy} onClick={() => setAmendment(null)}>보완 취소</button>
          </div>
        </form>
        : <button type="button" className="rv-button secondary rv-compact rv-framework-amend" disabled={busy || excluded.includes(selected.criterion_id)} onClick={() => {
          const c = selected;
          const kind = c.kind === 'unsupported' ? (c.details?.target === 'poster_visual' ? 'visual' : 'text') : c.kind;
          setAmendment({ action: 'amend', criterion_id: c.criterion_id, reason: '',
            source: { reference: c.value_source?.reference || '', quote: c.value_source?.quote || '' },
            resolution: { kind, operator: c.operator === 'manual' ? (kind === 'number' ? 'equals' : kind === 'visual' ? 'present' : 'contains') : c.operator,
              expected: c.expected, unit: c.unit || null, required: c.required,
              target: c.details?.target === 'unspecified' ? '' : c.details?.target || '', field: c.details?.field === 'unspecified' ? '' : c.details?.field || '' } });
        }}><Icon name="pencil" />기준 보완</button>)}
      footer={latest?.version.exclusions.length > 0 && <details className="rv-criteria-add"><summary>제외한 기준 {latest.version.exclusions.length}개</summary>
        <ul>{latest.version.exclusions.map(c => <li key={c.criterion_id}>{c.criterion_id.startsWith('input-') ? '직접 입력'
          : criterionTitle((run?.criteria?.criteria || []).find(item => item.criterion_id === c.criterion_id))} · {c.reason}</li>)}</ul></details>} />;
  }

  // ── 자료 입력 한 화면 ──
  // 실행이 아직 없을 때(문서를 첨부하기 전·추출하는 중), 방금 만든 초안을 불러오는 중, 승인 전 초안을 모두 같은 자리의 InputPage로 그린다.
  // 그래야 문서 추출이 끝나 검수 항목이 나타날 때 입력하던 칸의 초점과 내용, 머리글의 프로젝트 칸이 그대로 남는다.
  const loadingDraft = Boolean(runId) && loadState === 'checking' && Boolean(input?.fresh);
  const draft = Boolean(runId) && loadState === 'ready' && draftInput;
  if (input && (!runId || loadingDraft || draft)) {
    const unresolvedIds = draft && latest ? Object.keys(latest.approval_blockers) : [];
    // 저장된 기준에 값이 빈 항목이 남아 있고 화면에서 아직 손대지 않았으면, 보완하거나 빼야 시작할 수 있다.
    const unresolvedLeft = unresolvedIds.length > 0 && !excluded.length && !amendment;
    const included = draft ? criteria.length - excluded.length : 0;
    // 저장해 둔 초안을 주소로 다시 연 경우: 이 화면이 보안 여부·검수 유형·문서 이름을 기억하지 못한다. 문구 검수로 열고 문서는 바꾸지 못하게 한다.
    const reopened = draft && !input.fresh;
    const typedOnly = draft && run.criteria?.extraction?.input_kind === 'free_text';
    const pageState = reopened ? { ...input.page.state, securityMode: input.page.state.securityMode || 'secure',
      reviewMode: input.page.state.reviewMode || 'text' } : input.page.state;
    const starting = busy && ['start', 'start-skip'].includes(pending);
    // 직접 입력만으로 시작한 경우: 분석이 끝나 초안을 불러오고, 곧이어 승인으로 넘어가기까지 준비 창을 계속 띄워 둔다(사이에 꺼졌다 켜지지 않게).
    const continuing = Boolean(input.autoStart) && !autoStarted.current && (loadingDraft || (draft && Boolean(overview) && Boolean(file)));
    const review = loadingDraft ? { runId, loading: true, included: 0, busy: false, block: '검수 기준을 불러오고 있습니다.', start() {} }
      : !draft ? null
      : { runId, projectId: run.context.project_id, included, busy,
        documentName: reopened && !typedOnly ? '저장된 요구사항 문서' : undefined,
        documentLock: reopened ? '저장해 둔 초안에서는 문서를 바꿀 수 없어요. 문서를 바꾸려면 새 검수를 시작해 주세요.' : undefined,
        panel: overview ? renderCriteriaPanel() : null, loading: !overview,
        block: unresolvedLeft ? '값이 정해지지 않은 항목이 남아 있어요. 기준 보완으로 채우거나 이번 검수에서 빼 주세요.'
          : criteria.length > 0 && !included && !input.typed ? '적어도 검수 항목 한 개는 남겨 주세요.' : null,
        extras: unresolvedLeft && <div className="rv-criteria-blockers" role="status">
          <span className="rv-criteria-blocker-chips">{unresolvedIds.map(id => <button type="button" key={id} aria-label={criterionNumber(id) + '번 항목 확인하기'}
            title={criterionTitle(criteria.find(item => item.criterion_id === id))} onClick={() => showCriterion(id)}>{criterionNumber(id)}</button>)}</span>
          <button type="button" className="rv-button secondary" disabled={busy || !file || unresolvedIds.length >= criteria.length}
            onClick={() => perform(signal => startDraft(signal, true), 'start-skip')} aria-busy={working('start-skip')}>확인 필요 항목 {unresolvedIds.length}개 제외하고 바로 검수</button>
          {unresolvedIds.length >= criteria.length && <p>최소 한 개의 검수 가능한 기준이 필요합니다.</p>}
        </div>,
        error: actionError,
        start: () => perform(signal => startDraft(signal), 'start') };
    return <ReviewLayout {...input.layout} {...(reopened ? { showWorkspaceBar: true, steps: undefined } : null)}
      state={pageState} busy={busy || loadingDraft || input.layout.busy} mainClassName="rv-framework-workspace"
      startMessage={input.layout.startMessage || (starting ? actionStatus[pending] : continuing ? actionStatus.start : null)}
      projectActions={<p className="rv-step-status" role="status">{starting ? actionStatus[pending] : ''}</p>}>
      {Boolean(draft && run.synthetic) && <p className="rv-framework-notice">합성 검증 데이터 · 실제 모델의 검수 결과가 아닙니다.</p>}
      <InputPage {...input.page} state={pageState} review={review} />
    </ReviewLayout>;
  }

  if (loadState !== 'ready') {
    return <ReviewLayout state={{ step: 'running', projectName: '', criteria: [] }} busy={false}
      mainClassName="rv-framework-workspace" showWorkspaceBar={false}
      startMessage={loadState === 'checking' && initialStart.current ? '검수 상태를 확인하고 있어요.' : null}>
      <FrameworkRunStatus state={loadState} onRetry={retryRunStatus} />
    </ReviewLayout>;
  }

  return <ReviewLayout state={{ step, projectName: projectName || '포스터 검수', criteria }}
    busy={busy || processing}
    startMessage={busy && pending === 'decide-start' ? actionStatus[pending] : null}
    mainClassName="rv-framework-workspace"
    versionLabel={latest ? `기준 v${latest.version.version_no}` : run?.baseline ? '승인 기준' : '기준 검토'}
    projectActions={<>
      <p className="rv-step-status" role="status">{stageStatus}</p>
      {showingReport && <button type="button" className="rv-button secondary" disabled={busy}
        onClick={() => setReportTarget(null)}>결과 확인으로 돌아가기</button>}
    </>}
    canNavigate={key => key === 'input' ? canReturnToInput : !busy && !processing && (
      key === 'criteria' && criteria.length > 0 || ['results', 'report'].includes(key) && hasResults)}
    navigate={key => {
      if (key === 'input') {
        if (canReturnToInput) onInput();
        return;
      }
      setReportTarget(key === 'report' ? 'summary' : null);
      setShowCriteria(key === 'criteria');
    }}>
      {run?.synthetic && <p className="rv-framework-notice">합성 검증 데이터 · 실제 모델의 검수 결과가 아닙니다.</p>}
      {run?.error?.code === 'USER_CANCELLED' ? <p role="status" className="rv-framework-notice">대기 작업을 취소했습니다. 원본과 기존 기록은 보존됩니다.</p>
        : run?.error && <ErrorMessage message={run.error.code + ' · ' + run.error.message} />}
      {processing ? <ReviewProgress {...progress} image={image} error={error}
        facts={run?.baseline ? [['해상도', image.width ? `${image.width}×${image.height}` : image.unavailable ? '확인 불가' : '확인 중'],
          ['검수 기준', criteria.length ? `${criteria.length}개` : '확인 중'],
          ['검수 범위', reviewScope || '확인 중']] : []}
        cancelLabel="대기 작업 취소" cancelBusy={busy}
        onCancel={status === 'queued' && run?.context.actor_id === user.id ? () => perform(async signal => {
          try { setRun(await command(path + '/cancel', {}, null, signal)); }
          finally { setReload(n => n + 1); }
        }) : undefined}
        onRetry={error ? retryRunStatus : undefined} />
      : run?.error?.code === 'USER_CANCELLED' ? <section className="rv-panel"><h1 id="page-title" tabIndex={-1}>대기 작업 취소됨</h1><p>이 작업은 더 이상 실행되지 않습니다.</p><Link className="rv-button secondary" to="/review/tasks">내 작업으로</Link></section>
      : showingReport ? <FrameworkReport run={run} projectName={projectName} image={image} standalone />
      : hasResults && !showCriteria ? <ResultsPage review={resultView} busy={busy} finalApproved={finalApproved} exceptionApproved={exceptionApproved}
        filter={filter} setFilter={setFilter} activeEvidence={activeEvidence} setActiveEvidence={setActiveEvidence}
        edit={criteria.length ? () => setShowCriteria(true) : undefined} newReview={onNew}
        evidence={props => <PosterEvidenceDialog run={run} image={image} history={history} runs={chainRuns} retrieval={retrieval}
          onOpen={() => setEvidenceOpened(true)} {...props} />}
        // 근거 조회 경로(Neo4j/PostgreSQL·revision) 표시는 결과 화면에 두지 않는다. 조회 기록은 근거 관계도(PosterEvidenceDialog)에만 넘긴다.
        header={history && <RevisionHistory history={history} />}
        imageActions={imageActions}
        actions={<>
          <button type="button" className="rv-button primary" disabled={busy} onClick={() => setReportTarget('summary')}>
            보고서 보기<Icon name="arrow" />
          </button>
        </>} /> : <CriteriaWorkspace versionText={sourceDraft ? '승인 전 검토' : '검수 기준'} locked={!sourceDraft}
        // 여기는 승인한 기준을 다시 보는 화면이다(승인 전 초안은 자료 입력 한 화면에서 다룬다).
        {...(sourceDraft ? null : { kicker: '검수 기준', title: '승인한 검수 기준', lead: '이 검수에 쓰는 기준과 원문 출처입니다. 승인한 기준은 고칠 수 없어요.' })}
        canvas={<>
          {/* 기준이 승인되면 이 칸이 포스터를 받는 자리가 된다(아래쪽 별도 파일 칸은 두지 않는다). */}
          {pickingPoster && <input type="file" ref={posterInput} accept="image/png,image/jpeg,image/webp" className="sr-only"
            aria-label="검수 포스터" disabled={busy} onChange={event => { if (event.target.files.length) pickPoster([...event.target.files]); }} />}
          <div className={pickingPoster ? `rv-input-drop-target rv-poster-drop${posterDragging ? ' is-dragging' : ''}` : undefined}
            onDragOver={pickingPoster ? event => { event.preventDefault(); if (!busy) setPosterDragging(true); } : undefined}
            onDragLeave={pickingPoster ? event => { if (!event.currentTarget.contains(event.relatedTarget)) setPosterDragging(false); } : undefined}
            onDrop={pickingPoster ? event => { event.preventDefault(); setPosterDragging(false); if (!busy) pickPoster([...event.dataTransfer.files]); } : undefined}>
          <ImageStage src={imageUrl || (preview?.file === file ? preview.url : null)}
            alt="현재 버전의 검수 포스터" heading={false} showControls={false} bare
            empty={pickingPoster
              ? <><Icon name="image" /><h3>여기에 검수할 포스터를 놓아 주세요.</h3><p>파일을 끌어다 놓거나 버튼으로 선택하세요.</p>
                <button type="button" className="rv-button primary" disabled={busy} onClick={() => posterInput.current?.click()}><Icon name="upload" />파일 선택</button>
                <small>JPG · PNG · WEBP</small>
                {posterError && <p className="rv-poster-drop-error" role="alert">{posterError}</p>}</>
              : <><Icon name="image" /><h3>검수할 포스터를 준비하세요.</h3><p>검수 기준을 확인하고 승인한 뒤<br />포스터를 등록할 수 있습니다.</p></>} />
          {/* 넣은 포스터의 이름과 바꾸기·지우기는 칸 오른쪽 위에 띄운다(칸이 화면 높이만큼 커서 아래에 두면 스크롤해야 보인다). */}
          {pickingPoster && file && <div className="rv-poster-drop-file">
            <div className="rv-poster-drop-filebar"><strong title={file.name}>{file.name}</strong>
              <button type="button" className="rv-button secondary rv-compact" disabled={busy} onClick={() => posterInput.current?.click()}>파일 바꾸기</button>
              <button type="button" className="rv-button secondary rv-compact" disabled={busy} onClick={() => { setFile(null); setPosterError(''); }}>첨부 지우기</button>
            </div>
            {posterError && <p className="rv-poster-drop-error" role="alert">{posterError}</p>}
          </div>}
          </div>
            <div className="rv-criteria-context"><Icon name="info" /><div><strong>{sourceDraft ? '원문과 기준을 확인한 뒤 승인해 주세요.' : '승인된 기준과 원문 출처를 보존합니다.'}</strong><p>보완·제외는 사유와 함께 새 버전에 기록되며, 자동 판정과 최종 승인은 구분됩니다.</p></div></div>
        </>}
        actions={(hasCriteriaActions || onInput) && <section aria-label="검수 다음 단계" className="rv-actionbar rv-criteria-actionbar">
          <div className="rv-criteria-action-context">
            {onInput && <button type="button" className="rv-button secondary" disabled={!canReturnToInput}
              onClick={onInput}>자료 입력으로 돌아가기</button>}
            {registeringPoster && <p>{file ? '넣은 포스터를 확인한 뒤 승인 기준에 등록해 주세요.' : '기준이 승인됐어요. 왼쪽 칸에 검수할 포스터를 넣어 주세요.'}</p>}
            {readyToStart && <p>승인된 기준과 등록한 포스터를 확인한 뒤 검수를 시작할 수 있어요.</p>}
            {hasCriteriaActions && actionError}
          </div>
          {registeringPoster && <button className="rv-button primary" disabled={busy || !file || (!run.baseline && !latest)}
            onClick={() => perform(registerPoster, 'register')} aria-busy={working('register')} title={posterBlock}>승인 기준에 포스터 등록</button>}
          {readyToStart && <button className="rv-button primary" disabled={busy}
            onClick={() => perform(startReview, 'decide-start')} aria-busy={working('decide-start')}>이 포스터와 기준을 확인하고 검수 시작</button>}
        </section>}>
          {renderCriteriaPanel()}
      </CriteriaWorkspace>}
      <PosterLogoCard jobs={logoJobs.jobs} error={logoJobs.error} notice={logoNotice} />
      {/* 결과가 나오기 전(오류·접수 확인 등)에만 진행 기록을 둔다. 기준 확인·포스터 등록 단계에서는 '새 검수'와 실행 번호만 남아 보이지 않는다.
          보고서 화면 아래의 '담당자 승인과 수정본 재검수'(직접 확인 양식·최종 승인·수정 요청·재검수 접수·이력)는 2026-11-17 베타 피드백으로 없앴다. */}
      {!processing && !hasResults && !hasCriteriaActions
        && <section className="rv-panel rv-framework-actions" aria-labelledby="poster-report-approval">
      <h2 id="poster-report-approval" tabIndex={-1}>검수 진행 기록</h2>
      {(!run || running.has(status)) && <p role="status">검수 실행 상태를 확인하고 있습니다.</p>}
      {actionError}
      <button className="text-btn" disabled={busy} onClick={onNew}>새 검수</button>
      {run && <details className="rv-framework-record"><summary>실행·기준 이력</summary><p>실행 {runId}</p>
        {run.baseline && <p>승인 기준 {run.baseline.criteria_version_id} · 포스터 버전 {run.context.asset_version_id}</p>}
        {run.context.parent_run_id && <p>수정 전 실행 <a href={'?framework=' + encodeURIComponent(run.context.parent_run_id)}>{run.context.parent_run_id}</a></p>}
      </details>}
      </section>}
    {editingPoster && canEditPoster && (editingPoster === 'text' ? canEditText : !secureMaterial) && <PosterTextEditor key={runId} runPath={path} imageUrl={imageUrl}
      initialMode={editingPoster}
      placeCandidates={posterPlaceCandidates(run.criteria?.criteria || [], run.validated.items || [])}
      onClose={() => setEditingPoster(null)} />}
  </ReviewLayout>;
}
