import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router';
import ReviewLayout from '../../layouts/ReviewLayout.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import ImageStage from '../../components/ImageStage.jsx';
import { Icon } from '../../components/Icon.jsx';
import ResultsPage from './ResultsPage.jsx';
import { frameworkResults } from './frameworkResults.js';
import ReviewProgress from './ReviewProgress.jsx';
import { frameworkProgress, processingStatuses } from './frameworkProgress.js';
import CriteriaAmendment from './CriteriaAmendment.jsx';
import CriteriaWorkspace from './CriteriaWorkspace.jsx';
import HumanReview, { reviewableItems } from './HumanReview.jsx';
import FrameworkReport from './FrameworkReport.jsx';
import PosterTextEditor from './PosterTextEditor.jsx';
import { posterPlaceCandidates } from './posterMapQr.js';
import ChangeImpactNotice from './ChangeImpactNotice.jsx';
import { unreadImagePages } from './criteriaWarnings.js';
import RevisionHistory from './RevisionHistory.jsx';
import { frameworkPrefix, frameworkJson, requirePosterReview } from './frameworkClient.js';
import { useWorkspaceReset } from '../../workspace/WorkspaceSession.jsx';
import '../../styles/review-editor-v2.css';
import { useAuth } from '../../auth/AuthProvider.jsx';
import { PosterLogoCard, usePosterLogoJobs } from './PosterLogoParts.jsx';
import { previousLogos, submitPosterLogoJob } from './posterLogoFlow.js';
import './posterLogo.css';

// 로고 접수 실패 안내는 새 실행 화면으로 이동한 뒤에도 보이도록 실행 번호별로 잠시 보관한다.
const logoNoticeKey = runId => 'poster-logo-notice:' + runId;
function readLogoNotice(runId) {
  try { return sessionStorage.getItem(logoNoticeKey(runId)) || ''; } catch { return ''; }
}

const verdicts = { pass: '충족', fail: '불충족', review: '확인 필요' };
const running = processingStatuses;
const actionStatus = {
  approve: '기준을 승인하고 있어요.',
  start: '기준을 승인하고 있어요.',
  'start-skip': '기준을 승인하고 있어요.',
  'decide-start': '포스터 검수를 접수하고 있어요.',
  final: '최종 승인을 저장하고 있어요.',
  changes: '수정 요청 승인을 저장하고 있어요.',
};
const rejectionReasons = {
  A02_DUPLICATE_REQUIREMENT: '이미 포함된 기준과 중복됩니다.',
  A02_EXPECTED_UNGROUNDED: '기대값이 원문과 일치하지 않습니다.',
  A02_SOURCE_MISMATCH: '선택한 원문 범위를 확인할 수 없습니다.',
  A02_UNIT_UNGROUNDED: '단위가 원문과 일치하지 않습니다.',
  A02_UNRESOLVED_VALUE_INVENTED: '미확정 항목에 원문으로 확인할 수 없는 값이 있습니다.',
  A02_QUALIFIER_UNGROUNDED: '허용 조건이나 예외의 원문 근거가 일치하지 않습니다.',
  A02_NORMALIZATION_UNSUPPORTED: '이 항목에는 변환된 값 형식을 사용할 수 없습니다.',
  A02_NORMALIZED_VALUE_UNGROUNDED: '변환된 값이 원문과 일치하지 않습니다.',
  A02_NORMALIZED_VALUE_AMBIGUOUS: '변환된 값에 해당하는 원문 표기를 하나로 확인할 수 없습니다.',
  A02_PRESENTATION_MISSING: '기준에 표시할 문구가 없습니다.',
  A02_PRESENTATION_UNGROUNDED: '표시 문구가 원문과 일치하지 않습니다.',
  A02_PRESENTATION_SCOPE_LOST: '표시 문구에서 가로 또는 세로 구분이 빠졌습니다.',
  A02_COMPILED_SOURCE_TOO_LARGE: '원문 인용 범위가 너무 깁니다.',
  A02_RESPONSE_INVALID: '항목의 형식이나 비교 조건이 유효하지 않습니다.',
};

function ExtractionRejections({ items }) {
  return <section className="rv-criteria-rejections" aria-label="원문 검증에서 제외된 후보">
    <h3>원문 검증에서 제외된 후보 {items.length}개</h3>
    <p>아래 후보는 초안에서 제외했습니다. 원문을 확인한 뒤 필요한 기준을 보완해 주세요.</p>
    <ul>{items.map(({ candidate_index, code, source }) => <li key={candidate_index}>
      <strong>후보 {candidate_index} · {rejectionReasons[code] || '원문 검증에 실패했습니다.'}</strong>
      <span className="rv-criteria-rejection-code">{code}</span>
      {source ? <div className="rv-criteria-source">
        <Icon name="file" /><details><summary>확인된 원문 · {source.page}쪽</summary><p>{source.quote}</p></details>
      </div> : <p>확인 가능한 원문이 없습니다.</p>}
    </li>)}</ul>
  </section>;
}


function command(path, body, key, signal) {
  return frameworkJson(path, { method: 'POST', signal, body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json', ...(key ? { 'Idempotency-Key': key } : {}) } });
}

export default function FrameworkReviewPage({ runId, initialFile, initialCriterionIds, projectName, openRun, onInput, onNew, referenceLogos }) {
  const { user } = useAuth();
  const path = '/runs/' + encodeURIComponent(runId);
  const [run, setRun] = useState(null);
  const [overview, setOverview] = useState(null);
  const [file, setFile] = useState(initialFile);
  const [excluded, setExcluded] = useState([]);
  const [amendment, setAmendment] = useState(null);
  const [humanReviews, setHumanReviews] = useState({});
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(null);
  const [reload, setReload] = useState(0);
  const [showCriteria, setShowCriteria] = useState(false);
  const [reportTarget, setReportTarget] = useState(null);
  const [activeId, setActiveId] = useState(null);
  const [filter, setFilter] = useState('all');
  const [activeEvidence, setActiveEvidence] = useState(null);
  const [preview, setPreview] = useState(null);
  const [imageSize, setImageSize] = useState(null);
  const [editingPoster, setEditingPoster] = useState(null);
  const [changeImpact, setChangeImpact] = useState(null);
  const [revisions, setRevisions] = useState(null);
  const [logoNotice] = useState(() => readLogoNotice(runId));
  const logoJobs = usePosterLogoJobs(runId);
  const active = useRef(null);
  useWorkspaceReset(() => active.current?.abort());
  const keys = useRef(new Map());
  const uploaded = useRef(null);
  const amendmentForm = useRef(null);
  const humanReviewForm = useRef(null);
  const latest = overview?.latest_version;
  const status = run?.execution_status;
  const sourceDraft = status === 'waiting_criteria' && !run.baseline;
  const savedCriteria = latest?.version.criteria || run?.criteria?.criteria || [];
  const selectedPdfIds = sourceDraft && initialCriterionIds ? new Set(initialCriterionIds) : null;
  const criteria = selectedPdfIds ? savedCriteria.filter(item => selectedPdfIds.has(item.criterion_id)) : savedCriteria;
  const selectionUnavailable = selectedPdfIds && overview && criteria.length !== selectedPdfIds.size;
  const needsVersion = !latest || (selectedPdfIds && savedCriteria.length !== criteria.length) || excluded.length > 0 || amendment;
  const rejections = run?.criteria?.extraction?.rejected_candidates || [];
  const imagePages = unreadImagePages(run?.criteria?.warnings);
  const reviewScope = [
    criteria.some(item => item.kind !== 'visual') && '문구·값',
    criteria.some(item => item.kind === 'visual') && '시각 조건',
  ].filter(Boolean).join(' · ');
  const selected = criteria.find(item => item.criterion_id === (amendment?.criterion_id || activeId)) || criteria[0];
  const imageUrl = run?.baseline && run.context.asset_version_id
    ? frameworkPrefix + '/images/' + encodeURIComponent(run.context.asset_version_id) + '/content' : null;
  const image = useMemo(() => ({ url: imageUrl, ...(imageSize?.url === imageUrl ? imageSize : {}) }), [imageUrl, imageSize]);
  const resultView = useMemo(() => run ? frameworkResults(run, image, projectName) : null, [run, image, projectName]);
  const hasResults = !!run?.validated;
  const processing = !run || running.has(status);
  const progress = processing ? frameworkProgress(run) : null;
  const stageStatus = actionStatus[pending] || (progress && !error
    ? run ? progress.message : '검수 상태를 불러오고 있어요.'
    : null);
  const showingReport = !processing && hasResults && !showCriteria && Boolean(reportTarget);
  const step = processing ? 'running' : showingReport ? 'report' : hasResults && !showCriteria ? 'results' : 'criteria';
  const finalApproved = status === 'completed' && run?.human_decision === 'final_approved';
  const canReturnToInput = !busy && !processing && Boolean(onInput);
  const canRevise = status === 'waiting_review' || status === 'waiting_revision';
  const revisionReady = status === 'waiting_revision';
  const canEditPoster = hasResults && Boolean(imageUrl) && (canRevise || status === 'completed');
  const reviewItems = run ? reviewableItems(run) : [];
  const requiredItems = (run?.validated?.items || []).filter(item => item.required);
  const canFinalize = requiredItems.length > 0 && requiredItems.every(item => item.verdict === 'pass' ||
    (item.verdict === 'review' && reviewItems.some(candidate => candidate.criterion_id === item.criterion_id) &&
      humanReviews[item.criterion_id]?.verdict === 'pass')) && !Object.values(humanReviews).some(item => item.verdict === 'fail');

  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => {
    if (step === 'report' && reportTarget === 'approval') {
      document.getElementById(revisionReady ? 'poster-revision-file' : 'poster-report-approval')?.focus();
      return;
    }
    document.getElementById(step === 'running' ? 'review-process-title' : 'page-title')?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [step, reportTarget, revisionReady]);
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
  useEffect(() => { setHumanReviews({}); setEditingPoster(null); }, [runId]);
  useEffect(() => {
    const controller = new AbortController();
    let timer;
    async function read() {
      try {
        const snapshot = await frameworkJson(path, { signal: controller.signal });
        const info = !snapshot.baseline && ['waiting_criteria', 'waiting_asset'].includes(snapshot.execution_status)
          ? await frameworkJson(path + '/criteria-review', { signal: controller.signal }) : null;
        if (controller.signal.aborted) return;
        setRun(snapshot); setOverview(info);
        if (running.has(snapshot.execution_status)) timer = setTimeout(read, 1000);
      } catch (err) { if (!controller.signal.aborted) setError(err.message); }
    }
    read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [path, reload]);

  // 관계 조회(이전 기준과 달라진 항목 · 수정본 검수 이력)는 부가 안내다. 실패해도 검수 흐름을 막지 않는다.
  const criteriaRun = Boolean(run) && !run.baseline && ['waiting_criteria', 'waiting_asset'].includes(status);
  const latestSha = latest?.sha256;
  useEffect(() => {
    setChangeImpact(null);
    if (!criteriaRun) return;
    const controller = new AbortController();
    frameworkJson(path + '/criteria-change-impact', { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setChangeImpact(value); }).catch(() => {});
    return () => controller.abort();
  }, [path, criteriaRun, latestSha]);
  const posterRun = Boolean(run?.baseline) && !processing;
  const humanDecision = run?.human_decision;
  useEffect(() => {
    if (!posterRun) { setRevisions(null); return; }
    const controller = new AbortController();
    frameworkJson(path + '/revision-history', { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setRevisions(value); }).catch(() => {});
    return () => controller.abort();
  }, [path, posterRun, status, humanDecision]);

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
  const exclusionBlock = criteria.length && excluded.length === criteria.length ? '적어도 기준 한 개는 남겨 주세요.'
    : excluded.length && !reason.trim() ? '제외 사유를 먼저 적어 주세요.' : undefined;
  const posterBlock = !file ? '먼저 검수할 포스터 파일을 선택해 주세요.' : undefined;
  async function saveVersion(signal, base = latest, unresolvedIds = []) {
    if (selectionUnavailable) return;
    if (amendment && !amendmentForm.current.reportValidity()) return;
    const unresolvedReason = '담당자가 확인 필요 항목을 제외하고 검수를 시작했습니다.';
    const selectionReason = '자료 입력에서 검수 대상으로 선택하지 않았습니다.';
    const omittedPdfCriteria = selectedPdfIds
      ? (base?.version.criteria || savedCriteria).filter(item => !selectedPdfIds.has(item.criterion_id)) : [];
    const body = { parent_version_id: base?.version.criteria_version_id || null,
      expected_base_sha256: base?.sha256 || overview.source_draft_sha256, purpose: 'human_review',
      edits: [...omittedPdfCriteria.map(c => ({
        action: 'exclude', criterion_id: c.criterion_id, reason: selectionReason,
        source: { reference: 'PDF 검수 항목 선택', quote: selectionReason },
      })), ...criteria.filter(c => excluded.includes(c.criterion_id)).map(c => ({
        action: 'exclude', criterion_id: c.criterion_id, reason: reason.trim(),
        source: { reference: '담당자 기준 검토', quote: reason.trim() },
      })), ...unresolvedIds.filter(id => !excluded.includes(id)).map(id => ({
        action: 'exclude', criterion_id: id, reason: unresolvedReason,
        source: { reference: '담당자 검수 시작 선택', quote: unresolvedReason },
      })), ...(amendment ? [amendment] : [])] };
    const version = await command(path + '/criteria-versions', body, requestKey(JSON.stringify(body)), signal);
    setOverview({ ...overview, latest_version: version }); setExcluded([]); setReason(''); setAmendment(null);
    return version;
  }
  async function approveVersion(signal, version = latest) {
    const body = { criteria_version_id: version.version.criteria_version_id, expected_sha256: version.sha256,
      purpose: 'human_review', reviewed_all_criteria: true, reason: '표시된 기준 전체와 원문을 확인하고 승인했습니다.' };
    await command(path + '/criteria-approvals', body, requestKey(JSON.stringify(body)), signal);
    setRun(await frameworkJson(path, { signal }));
  }
  async function registerPoster(signal, version = latest, navigate = true) {
    if (!file) return;
    await requirePosterReview({ signal });
    if (uploaded.current?.file !== file) {
      const form = new FormData(); form.append('image', file);
      const image = await frameworkJson('/projects/' + encodeURIComponent(run.context.project_id) + '/images',
        { method: 'POST', body: form, signal });
      uploaded.current = { file, id: image.id };
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
  async function attachPosterLogos(childRunId, revision, signal) {
    try {
      const logos = revision ? await previousLogos(runId, { signal }) : referenceLogos || [];
      if (!logos.length) return;
      await submitPosterLogoJob({ poster: file, logos, title: (projectName || '포스터') + ' 로고 검수', runId: childRunId, signal });
    } catch (failure) {
      if (signal.aborted) return;
      try {
        sessionStorage.setItem(logoNoticeKey(childRunId),
          '포스터 로고 검수를 접수하지 못했어요(' + failure.message + '). 기준 문구 검수는 그대로 진행합니다.');
      } catch { /* 브라우저 저장소를 쓸 수 없으면 안내만 생략한다 */ }
    }
  }
  async function approveAndStart(signal, excludeUnresolved = false) {
    if (selectionUnavailable) return;
    await requirePosterReview({ signal });
    let version = needsVersion ? await saveVersion(signal) : latest;
    if (excludeUnresolved && version && Object.keys(version.approval_blockers).length) {
      version = await saveVersion(signal, version, Object.keys(version.approval_blockers));
    }
    if (!version || Object.keys(version.approval_blockers).length) return;
    await approveVersion(signal, version);
    if (!file) return;
    const child = await registerPoster(signal, version, false);
    try {
      await command('/runs/' + encodeURIComponent(child.context.run_id) + '/approvals', {
        context: child.context, actor_id: child.context.actor_id, decision: 'approve_criteria', reason: '',
      }, null, signal);
    } finally {
      if (!signal.aborted) openRun(child.context.run_id);
    }
  }
  async function decide(decision, signal) {
    if (decision === 'approve_criteria') await requirePosterReview({ signal });
    const reviews = Object.values(humanReviews).filter(item => item.verdict);
    if (reviews.length && !humanReviewForm.current.reportValidity()) return;
    const snapshot = await command(path + '/approvals', { context: run.context, actor_id: run.context.actor_id, decision,
      ...(reviews.length ? { human_reviews: reviews } : {}),
      reason: decision === 'approve_changes' ? reason.trim() : reviews.length ? '항목별 직접 확인 결과와 근거를 검토하고 승인했습니다.' : '' }, null, signal);
    setRun(snapshot); setReason(''); setHumanReviews({});
    if (decision === 'approve_changes' && snapshot.execution_status === 'waiting_revision') setReportTarget('approval');
    if (decision === 'approve_final' && snapshot.execution_status === 'completed' &&
        snapshot.human_decision === 'final_approved') setReportTarget(null);
    if (running.has(snapshot.execution_status)) setReload(n => n + 1);
  }

  const reviewingDraft = sourceDraft && Boolean(overview);
  const registeringPoster = status === 'waiting_asset';
  const readyToStart = status === 'waiting_criteria' && Boolean(run?.baseline);
  const hasCriteriaActions = reviewingDraft || registeringPoster || readyToStart;
  const imageActions = imageUrl && <div className="rv-result-image-actions" role="group" aria-label="검수 이미지 작업">
    {canEditPoster && <>
      <button type="button" className="rv-button primary" disabled={busy || image.unavailable}
        onClick={() => setEditingPoster('text')}><Icon name="pencil" />포스터 문구 수정</button>
      <button type="button" className="rv-button secondary" disabled={busy || image.unavailable}
        onClick={() => setEditingPoster('qr')}>QR 생성</button>
    </>}
    <a className="rv-button secondary" href={imageUrl} target="_blank" rel="noopener noreferrer"
      title="근거 박스 없이 원본 이미지 새 탭에서 열기">원본</a>
  </div>;
  const actionError = error && <>
    <ErrorMessage message={error} />
    <button disabled={busy} onClick={() => { setExcluded([]); setReason(''); setError(''); setReload(n => n + 1); }}>서버 상태 다시 확인</button>
  </>;

  return <ReviewLayout state={{ step, projectName: projectName || '포스터 검수', criteria }}
    busy={busy || processing}
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
        onRetry={error ? () => { setError(''); setReload(n => n + 1); } : undefined} />
      : run?.error?.code === 'USER_CANCELLED' ? <section className="rv-panel"><h1 id="page-title" tabIndex={-1}>대기 작업 취소됨</h1><p>이 작업은 더 이상 실행되지 않습니다.</p><Link className="rv-button secondary" to="/review/tasks">내 작업으로</Link></section>
      : showingReport ? <FrameworkReport run={run} projectName={projectName} image={image} standalone />
      : hasResults && !showCriteria ? <ResultsPage review={resultView} exportData={run} busy={busy} finalApproved={finalApproved}
        filter={filter} setFilter={setFilter} activeEvidence={activeEvidence} setActiveEvidence={setActiveEvidence}
        edit={criteria.length ? () => setShowCriteria(true) : undefined} newReview={onNew}
        revise={canRevise ? () => setReportTarget('approval') : undefined}
        header={revisions?.run_id === runId ? <RevisionHistory history={revisions} /> : null}
        imageActions={imageActions}
        actions={<>
          <button type="button" className="rv-button primary" disabled={busy} onClick={() => setReportTarget('summary')}>
            {canRevise ? '보고서·담당자 승인' : '보고서 보기'}<Icon name="arrow" />
          </button>
        </>} /> : <CriteriaWorkspace versionText={sourceDraft ? '승인 전 검토' : '검수 기준'} locked={!sourceDraft}
        canvas={<>
          <ImageStage src={imageUrl || (preview?.file === file ? preview.url : null)}
            title="검수 포스터" alt="현재 버전의 검수 포스터" subtitle={file?.name}
            empty={<><Icon name="image" /><h3>검수할 포스터를 준비하세요.</h3><p>검수 기준을 확인하고 승인한 뒤<br />포스터를 등록할 수 있습니다.</p></>} />
            <div className="rv-criteria-context"><Icon name="info" /><div><strong>{sourceDraft ? '원문과 기준을 확인한 뒤 승인해 주세요.' : '승인된 기준과 원문 출처를 보존합니다.'}</strong><p>보완·제외는 사유와 함께 새 버전에 기록되며, 자동 판정과 최종 승인은 구분됩니다.</p></div></div>
        </>}
        actions={(hasCriteriaActions || onInput) && <section aria-label="검수 다음 단계" className="rv-actionbar rv-criteria-actionbar">
          <div className="rv-criteria-action-context">
            {onInput && <button type="button" className="rv-button secondary" disabled={!canReturnToInput}
              onClick={onInput}>자료 입력으로 돌아가기</button>}
            {referenceLogos?.length > 0 && (registeringPoster || reviewingDraft) && <p>
              첨부한 원본 로고 {referenceLogos.length}개는 포스터를 등록할 때 로고 위치 후보 찾기에 함께 접수합니다.</p>}
            {reviewingDraft && (excluded.length > 0
              ? <label>제외 사유 <input value={reason} disabled={busy} onChange={e => setReason(e.target.value)} /></label>
              : <p>{criteria.length}개 기준의 원문과 기대값을 확인해 주세요. 검수 시작을 누르면 기준 승인과 포스터 등록이 함께 진행됩니다.</p>)}
            {registeringPoster && <>
              <p>기준이 승인됐어요. 검수할 포스터를 등록해 주세요.</p>
              <label>검수 포스터 <input type="file" accept="image/png,image/jpeg,image/webp"
                disabled={busy} onChange={e => setFile(e.target.files[0] || null)} /></label>
              {file && <p>{file.name}</p>}
            </>}
            {readyToStart && <p>승인된 기준과 등록한 포스터를 확인한 뒤 검수를 시작할 수 있어요.</p>}
            {hasCriteriaActions && actionError}
          </div>
          {reviewingDraft && (
            <div className="rv-criteria-action-buttons">{needsVersion && <button className="rv-button secondary" disabled={busy || selectionUnavailable || excluded.length === criteria.length || (!!excluded.length && !reason.trim())} onClick={() => perform(saveVersion, 'save')} aria-busy={working('save')} title={exclusionBlock}>검토한 기준 버전 저장</button>}
            {!selectionUnavailable && !needsVersion && (Object.keys(latest.approval_blockers).length ? <div className="rv-criteria-blockers" role="status"><p>확인 필요 항목은 보완하거나 이번 검수에서 제외할 수 있습니다.</p>{Object.keys(latest.approval_blockers).map(id => <button type="button" key={id} onClick={() => { setActiveId(id); document.getElementById('rv-criterion-' + id)?.scrollIntoView({ block: 'nearest' }); }}>{id} 확인하기</button>)}<button type="button" className="rv-button primary" disabled={busy || Object.keys(latest.approval_blockers).length >= criteria.length} onClick={() => perform(signal => approveAndStart(signal, true), 'start-skip')} aria-busy={working('start-skip')}>확인 필요 항목 {Object.keys(latest.approval_blockers).length}개 제외하고 {file ? '바로 검수' : '기준 승인'}</button>{Object.keys(latest.approval_blockers).length >= criteria.length && <p>최소 한 개의 검수 가능한 기준이 필요합니다.</p>}</div> : <button className="rv-button secondary" disabled={busy} onClick={() => perform(approveVersion, 'approve')} aria-busy={working('approve')}>이 버전의 전체 기준 승인</button>)}
            {(needsVersion || !Object.keys(latest.approval_blockers).length) && <button className="rv-button primary" disabled={busy || selectionUnavailable || excluded.length === criteria.length || (!!excluded.length && !reason.trim())} onClick={() => perform(approveAndStart, 'start')} aria-busy={working('start')} title={exclusionBlock}>{file ? '기준 승인하고 검수 시작' : '기준 확인하고 승인'}</button>}
            </div>
          )}
          {registeringPoster && <button className="rv-button primary" disabled={busy || !file || (!run.baseline && !latest)}
            onClick={() => perform(registerPoster, 'register')} aria-busy={working('register')} title={posterBlock}>승인 기준에 포스터 등록</button>}
          {readyToStart && <button className="rv-button primary" disabled={busy}
            onClick={() => perform(signal => decide('approve_criteria', signal), 'decide-start')} aria-busy={working('decide-start')}>이 포스터와 기준을 확인하고 검수 시작</button>}
        </section>}>
          <div className="rv-panel-heading"><div><span className="rv-eyebrow">REVIEW CRITERIA</span><h2>검수 항목 <span>{criteria.length}</span></h2></div><span className="rv-criteria-selection-count">{criteria.length - excluded.length}개 포함</span></div>
            {criteriaRun && changeImpact?.run_id === runId && <ChangeImpactNotice impact={changeImpact} />}
            {selectionUnavailable && <ErrorMessage message="선택한 항목 중 저장한 기준 버전에서 이미 제외된 항목이 있어요. 자료 입력으로 돌아가 선택을 다시 확인해 주세요." />}
            {imagePages.length > 0 && <section className="rv-relation-notice" role="status" aria-label="읽지 않은 이미지 안내">
              <div className="rv-relation-notice-head"><Icon name="warning" /><div>
                <strong>{imagePages.join(', ')}쪽의 이미지 안 내용은 읽지 않았어요.</strong>
                <p>글자로 된 부분만 기준으로 만들었습니다. 이미지에 요구사항이 있으면 원문을 보고 기준을 보완해 주세요.</p></div></div>
            </section>}
            {rejections.length > 0 && <ExtractionRejections items={rejections} />}
            <div className="rv-criteria-toolbar"><span>원문 기준 · {sourceDraft ? '보완·제외 후 전체 승인' : '읽기 전용'}</span><span>제외 {excluded.length}개</span></div>
            <ul className="rv-criteria-list" aria-label="검수 항목 목록">{criteria.map((c, index) => <li id={'rv-criterion-' + c.criterion_id} key={c.criterion_id} className={`rv-criteria-item${selected?.criterion_id === c.criterion_id ? ' is-active' : ''}${excluded.includes(c.criterion_id) ? ' is-excluded' : ''}${latest?.approval_blockers?.[c.criterion_id] ? ' is-blocked' : ''}`}>
              <label className="rv-criteria-check"><input type="checkbox" aria-label={`검수에 포함: ${c.text}`} disabled={!sourceDraft || busy || amendment?.criterion_id === c.criterion_id}
                checked={!excluded.includes(c.criterion_id)} onChange={e => setExcluded(ids => e.target.checked ? ids.filter(id => id !== c.criterion_id) : [...ids, c.criterion_id])} /></label>
              <button type="button" className="rv-criteria-select" aria-current={selected?.criterion_id === c.criterion_id ? 'true' : undefined} aria-controls="rv-active-criterion"
                disabled={busy || (!!amendment && amendment.criterion_id !== c.criterion_id)} onClick={() => setActiveId(c.criterion_id)}>
                <span className="rv-criteria-item-index">{String(index + 1).padStart(2, '0')}</span><span className="rv-criteria-item-text">{c.text}</span><span className="rv-criteria-item-type">{!c.expected.trim() ? '기대값 미정' : c.kind === 'visual' ? '시각' : '정보'}</span><span className="rv-criteria-item-arrow">↗</span>
              </button>
            </li>)}</ul>
            {selected && <div className="rv-criteria-detail" id="rv-active-criterion">
              <div className="rv-criteria-detail-heading"><div><span className="rv-eyebrow">SELECTED CRITERION</span><h3>{selected.text}</h3></div></div>
              <p className="rv-framework-expected">{selected.expected || '기대값 미정'} {selected.unit || ''}</p>
              <div className="rv-criteria-source"><Icon name="file" /><details open><summary>요구사항 원문 · {selected.source.page}쪽</summary><p>{selected.source.quote}</p>
                {selected.value_source && <p>보완 회신: {selected.value_source.quote} · 출처: {selected.value_source.reference}</p>}</details></div>
              {sourceDraft && (amendment ? <>
                <form ref={amendmentForm} onSubmit={e => { e.preventDefault(); perform(saveVersion, 'save'); }}><CriteriaAmendment value={amendment} onChange={setAmendment} disabled={busy} /></form>
                <button type="button" className="rv-button secondary" disabled={busy} onClick={() => setAmendment(null)}>보완 취소</button>
              </> : <button type="button" className="rv-button secondary rv-framework-amend" disabled={busy || excluded.includes(selected.criterion_id)} onClick={() => {
                const c = selected;
                const kind = c.kind === 'unsupported' ? (c.details?.target === 'poster_visual' ? 'visual' : 'text') : c.kind;
                setAmendment({ action: 'amend', criterion_id: c.criterion_id, reason: '',
                  source: { reference: c.value_source?.reference || '', quote: c.value_source?.quote || '' },
                  resolution: { kind, operator: c.operator === 'manual' ? (kind === 'number' ? 'equals' : kind === 'visual' ? 'present' : 'contains') : c.operator,
                    expected: c.expected, unit: c.unit || null, required: c.required,
                    target: c.details?.target === 'unspecified' ? '' : c.details?.target || '', field: c.details?.field === 'unspecified' ? '' : c.details?.field || '' } });
              }}>기준 보완</button>)}
            </div>}
            {!criteria.length && <p className="rv-criteria-empty">검수 기준을 불러오고 있습니다.</p>}
            {latest?.version.exclusions.length > 0 && <details className="rv-criteria-add"><summary>제외한 기준 {latest.version.exclusions.length}개</summary><ul>{latest.version.exclusions.map(c => <li key={c.criterion_id}>{c.criterion_id} · {c.reason}</li>)}</ul></details>}
      </CriteriaWorkspace>}
      <PosterLogoCard jobs={logoJobs.jobs} error={logoJobs.error} notice={logoNotice} />
      {!processing && (!hasResults || showingReport) && <section className="rv-panel rv-framework-actions" aria-labelledby="poster-report-approval">
      <h2 id="poster-report-approval" tabIndex={-1}>{canRevise ? '담당자 승인과 수정본 재검수' : '검수 진행 기록'}</h2>
      {revisionReady && <>
        <p>수정 요청이 승인됐어요. 수정한 포스터를 등록하면 기존 승인 기준으로 재검수를 접수합니다.</p>
        {canEditPoster && <button type="button" className="rv-button secondary" disabled={busy || image.unavailable}
          onClick={() => setEditingPoster('text')}><Icon name="pencil" />포스터 문구 수정</button>}
        <label>수정 포스터 <input id="poster-revision-file" type="file" accept="image/png,image/jpeg,image/webp"
          disabled={busy} onChange={e => setFile(e.target.files[0] || null)} /></label>
        {file && <p>{file.name}</p>}
        <button className="rv-button primary" disabled={busy || !file || (!run.baseline && !latest)}
          onClick={() => perform(registerPoster, 'revision')} aria-busy={working('revision')} title={posterBlock}>수정본 재검수 접수</button>
      </>}
      {(!run || running.has(status)) && <p role="status">검수 실행 상태를 확인하고 있습니다.</p>}
      {!showingReport && run?.report && <FrameworkReport run={run} projectName={projectName} image={image} />}
      {status === 'waiting_review' && <>
        <p>담당자가 결과를 검토하고 수정 요청을 승인하면 수정본을 재검수할 수 있습니다.</p>
        {reviewItems.length > 0 && <form ref={humanReviewForm} onSubmit={e => e.preventDefault()}>
          <HumanReview items={reviewItems} values={humanReviews} onChange={setHumanReviews} disabled={busy} />
        </form>}
        {(run.machine_verdict === 'pass' || reviewItems.length > 0) && <button className="rv-button primary" disabled={busy || !canFinalize}
          onClick={() => perform(signal => decide('approve_final', signal), 'final')} aria-busy={working('final')}
          title={!canFinalize ? '필수 항목이 모두 충족되거나 담당자 확인을 마쳐야 승인할 수 있어요.' : undefined}>{run.machine_verdict === 'pass' ? '최종 승인' : '사람 확인 후 최종 승인'}</button>}
        <label>수정 요청 사유 <input disabled={busy} value={reason} onChange={e => setReason(e.target.value)} /></label>
        <button className="rv-button secondary" disabled={busy || !reason.trim()} onClick={() => perform(signal => decide('approve_changes', signal), 'changes')} aria-busy={working('changes')}
          title={!reason.trim() ? '수정 요청 사유를 먼저 적어 주세요.' : undefined}>수정 요청 승인</button>
      </>}
      {status === 'completed' && !finalApproved && <p role="status">담당자 승인 완료</p>}
      {!run?.report && run?.approvals?.flatMap(approval => (approval.human_reviews || []).map(item => <section key={approval.decision + item.criterion_id}>
        <h3>담당자 확인 · {item.criterion_id} · {verdicts[item.verdict]}</h3>
        <p>{item.observed}</p><p>근거: {item.reference} · 사유: {item.reason}</p><p>검토자: {approval.actor_id}</p>
      </section>))}
      {!hasCriteriaActions && actionError}
      {!hasResults && <button className="text-btn" disabled={busy} onClick={onNew}>새 검수</button>}
      {run && <details className="rv-framework-record"><summary>실행·기준 이력</summary><p>실행 {runId}</p>
        {run.baseline && <p>승인 기준 {run.baseline.criteria_version_id} · 포스터 버전 {run.context.asset_version_id}</p>}
        {run.context.parent_run_id && <p>수정 전 실행 <a href={'?framework=' + encodeURIComponent(run.context.parent_run_id)}>{run.context.parent_run_id}</a></p>}
      </details>}
      </section>}
    {editingPoster && canEditPoster && <PosterTextEditor key={runId} runPath={path} imageUrl={imageUrl}
      initialMode={editingPoster}
      placeCandidates={posterPlaceCandidates(run.criteria?.criteria || [], run.validated.items || [])}
      onClose={() => setEditingPoster(null)} />}
  </ReviewLayout>;
}
