import React, { useEffect, useRef, useState } from 'react';
import { WorkspaceFooter, WorkspaceTopbar } from '../../layouts/ReviewLayout.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import ImageStage from '../../components/ImageStage.jsx';
import CriteriaAmendment from './CriteriaAmendment.jsx';
import HumanReview, { reviewableItems } from './HumanReview.jsx';
import { frameworkPrefix, frameworkJson, requirePosterReview } from './frameworkClient.js';

const verdicts = { pass: '충족', fail: '불충족', review: '확인 필요' };
const running = new Set(['queued', 'extracting', 'reviewing']);

function command(path, body, key, signal) {
  return frameworkJson(path, { method: 'POST', signal, body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json', ...(key ? { 'Idempotency-Key': key } : {}) } });
}

export default function FrameworkReviewPage({ runId, initialFile, openRun, onNew }) {
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
  const [reload, setReload] = useState(0);
  const active = useRef(null);
  const keys = useRef(new Map());
  const uploaded = useRef(null);
  const amendmentForm = useRef(null);
  const humanReviewForm = useRef(null);
  const latest = overview?.latest_version;
  const status = run?.execution_status;
  const sourceDraft = status === 'waiting_criteria' && !run.baseline;
  const criteria = latest?.version.criteria || run?.criteria?.criteria || [];
  const reviewItems = run ? reviewableItems(run) : [];
  const requiredItems = (run?.validated?.items || []).filter(item => item.required);
  const canFinalize = requiredItems.length > 0 && requiredItems.every(item => item.verdict === 'pass' ||
    (item.verdict === 'review' && reviewItems.some(candidate => candidate.criterion_id === item.criterion_id) &&
      humanReviews[item.criterion_id]?.verdict === 'pass')) && !Object.values(humanReviews).some(item => item.verdict === 'fail');

  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => { setHumanReviews({}); }, [runId]);
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

  function requestKey(name) {
    if (!keys.current.has(name)) keys.current.set(name, crypto.randomUUID());
    return keys.current.get(name);
  }
  async function perform(action) {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller; setBusy(true); setError('');
    try { await action(controller.signal); }
    catch (err) { if (!controller.signal.aborted) setError(err.code && err.code !== 'http_error' ? err.message + ' (' + err.code + ')' : err.message); }
    finally { if (!controller.signal.aborted) { active.current = null; setBusy(false); } }
  }
  async function saveVersion(signal) {
    if (amendment && !amendmentForm.current.reportValidity()) return;
    const body = { parent_version_id: latest?.version.criteria_version_id || null,
      expected_base_sha256: latest?.sha256 || overview.source_draft_sha256, purpose: 'human_review',
      edits: [...criteria.filter(c => excluded.includes(c.criterion_id)).map(c => ({
        action: 'exclude', criterion_id: c.criterion_id, reason: reason.trim(),
        source: { reference: '담당자 기준 검토', quote: reason.trim() },
      })), ...(amendment ? [amendment] : [])] };
    const version = await command(path + '/criteria-versions', body, requestKey(JSON.stringify(body)), signal);
    setOverview({ ...overview, latest_version: version }); setExcluded([]); setReason(''); setAmendment(null);
  }
  async function approveVersion(signal) {
    const body = { criteria_version_id: latest.version.criteria_version_id, expected_sha256: latest.sha256,
      purpose: 'human_review', reviewed_all_criteria: true, reason: '표시된 기준 전체와 원문을 확인하고 승인했습니다.' };
    await command(path + '/criteria-approvals', body, requestKey(JSON.stringify(body)), signal);
    setRun(await frameworkJson(path, { signal }));
  }
  async function registerPoster(signal) {
    if (!file) return;
    await requirePosterReview({ signal });
    if (uploaded.current?.file !== file) {
      const form = new FormData(); form.append('image', file);
      const image = await frameworkJson('/projects/' + encodeURIComponent(run.context.project_id) + '/images',
        { method: 'POST', body: form, signal });
      uploaded.current = { file, id: image.id };
    }
    const revision = status === 'waiting_revision';
    const baseline = revision ? run.baseline : { criteria_version_id: latest.version.criteria_version_id, criteria_sha256: latest.sha256 };
    const body = { criteria_version_id: baseline.criteria_version_id, expected_criteria_sha256: baseline.criteria_sha256,
      asset_version_id: uploaded.current.id };
    const child = await command(path + (revision ? '/poster-revisions' : '/poster-reviews'), body, requestKey(JSON.stringify(body)), signal);
    openRun(child.context.run_id);
  }
  async function decide(decision, signal) {
    if (decision === 'approve_criteria') await requirePosterReview({ signal });
    const reviews = Object.values(humanReviews).filter(item => item.verdict);
    if (reviews.length && !humanReviewForm.current.reportValidity()) return;
    const snapshot = await command(path + '/approvals', { context: run.context, actor_id: run.context.actor_id, decision,
      ...(reviews.length ? { human_reviews: reviews } : {}),
      reason: decision === 'approve_changes' ? reason.trim() : reviews.length ? '항목별 직접 확인 결과와 근거를 검토하고 승인했습니다.' : '' }, null, signal);
    setRun(snapshot); setReason(''); setHumanReviews({});
    if (running.has(snapshot.execution_status)) setReload(n => n + 1);
  }

  return <div className="rv-workspace"><WorkspaceTopbar current="/review/new" />
    <main className="rv-main"><section className="rv-panel rv-framework-review" aria-busy={busy}>
      <h1>승인 기준 포스터 검수</h1>
      {run && <p>실행 {runId}{run.synthetic && ' · 합성 검증 데이터'}</p>}
      {run?.context.parent_run_id && <p>수정 전 실행 <a href={'?framework=' + encodeURIComponent(run.context.parent_run_id)}>{run.context.parent_run_id}</a></p>}
      {criteria.length > 0 && <details open={sourceDraft || undefined}><summary>검수 기준 {criteria.length}개{latest && ' · v' + latest.version.version_no}</summary>
        <ul>{criteria.map(c => <li key={c.criterion_id}><strong>{c.criterion_id} · {c.text}</strong> · {c.expected || '기대값 미정'} {c.unit || ''}
          <p>{c.source.page}쪽 · {c.source.quote}</p>
          {c.value_source && <p>보완 회신: {c.value_source.quote} · 출처: {c.value_source.reference}</p>}
          {sourceDraft && <>
            <label><input type="checkbox" disabled={busy || amendment?.criterion_id === c.criterion_id} checked={excluded.includes(c.criterion_id)}
              onChange={e => setExcluded(ids => e.target.checked ? [...ids, c.criterion_id] : ids.filter(id => id !== c.criterion_id))} /> 이 버전에서 제외</label>
            {amendment?.criterion_id === c.criterion_id ? <>
              <form ref={amendmentForm} onSubmit={e => { e.preventDefault(); perform(saveVersion); }}>
                <CriteriaAmendment value={amendment} onChange={setAmendment} disabled={busy} />
              </form>
              <button type="button" disabled={busy} onClick={() => setAmendment(null)}>보완 취소</button>
            </> : <button type="button" disabled={busy || !!amendment || excluded.includes(c.criterion_id)} onClick={() => {
              const kind = c.kind === 'unsupported' ? (c.details?.target === 'poster_visual' ? 'visual' : 'text') : c.kind;
              setAmendment({
              action: 'amend', criterion_id: c.criterion_id, reason: '',
              source: { reference: c.value_source?.reference || '', quote: c.value_source?.quote || '' },
              resolution: { kind,
                operator: c.operator === 'manual' ? (kind === 'number' ? 'equals' : kind === 'visual' ? 'present' : 'contains') : c.operator,
                expected: c.expected, unit: c.unit || null, required: c.required,
                target: c.details?.target === 'unspecified' ? '' : c.details?.target || '',
                field: c.details?.field === 'unspecified' ? '' : c.details?.field || '' },
            }); }}>기준 보완</button>}
          </>}</li>)}</ul>
      </details>}
      {latest?.version.exclusions.length > 0 && <details><summary>제외한 기준 {latest.version.exclusions.length}개</summary>
        <ul>{latest.version.exclusions.map(c => <li key={c.criterion_id}>{c.criterion_id} · {c.reason}</li>)}</ul></details>}
      {sourceDraft && overview && <>
        {excluded.length > 0 && <label>제외 사유 <input value={reason} disabled={busy} onChange={e => setReason(e.target.value)} /></label>}
        {(!latest || excluded.length > 0 || amendment) && <button className="rv-button secondary" disabled={busy || excluded.length === criteria.length || (!!excluded.length && !reason.trim())}
          onClick={() => perform(saveVersion)}>검토한 기준 버전 저장</button>}
        {latest && !excluded.length && !amendment && (Object.keys(latest.approval_blockers).length ?
          <p role="status">승인 전 보완하거나 사유를 적어 제외할 항목: {Object.keys(latest.approval_blockers).join(', ')}</p> :
          <button className="rv-button primary" disabled={busy} onClick={() => perform(approveVersion)}>이 버전의 전체 기준 승인</button>)}
      </>}
      {(status === 'waiting_asset' || status === 'waiting_revision') && <>
        <label>{status === 'waiting_revision' ? '수정 포스터' : '검수 포스터'} <input type="file" accept="image/png,image/jpeg,image/webp"
          disabled={busy} onChange={e => setFile(e.target.files[0] || null)} /></label>
        {file && <p>{file.name}</p>}
        <button className="rv-button primary" disabled={busy || !file || (!run.baseline && !latest)}
          onClick={() => perform(registerPoster)}>{status === 'waiting_revision' ? '수정본 재검수 접수' : '승인 기준에 포스터 등록'}</button>
      </>}
      {run?.baseline && <ImageStage title="검수 포스터" alt="현재 버전의 검수 포스터"
        src={frameworkPrefix + '/images/' + encodeURIComponent(run.context.asset_version_id) + '/content'} />}
      {run?.baseline && <p>승인 기준 {run.baseline.criteria_version_id} · 포스터 버전 {run.context.asset_version_id}</p>}
      {status === 'waiting_criteria' && run.baseline && <button className="rv-button primary" disabled={busy}
        onClick={() => perform(signal => decide('approve_criteria', signal))}>이 포스터와 기준을 확인하고 검수 시작</button>}
      {(!run || running.has(status)) && <p role="status">검수 실행 상태를 확인하고 있습니다.</p>}
      {run?.error && <ErrorMessage message={run.error.code + ' · ' + run.error.message} />}
      {run?.report && <section><h2>검수 보고서</h2><p>기계 판정 · {verdicts[run.machine_verdict]}</p>
        <p style={{ whiteSpace: 'pre-wrap' }}>{run.report.summary}</p>
        <ul>{run.report.change_requests.map((item, i) => <li key={i}>{item}</li>)}</ul></section>}
      {status === 'waiting_review' && <>
        {reviewItems.length > 0 && <form ref={humanReviewForm} onSubmit={e => e.preventDefault()}>
          <HumanReview items={reviewItems} values={humanReviews} onChange={setHumanReviews} disabled={busy} />
        </form>}
        {(run.machine_verdict === 'pass' || reviewItems.length > 0) && <button className="rv-button primary" disabled={busy || !canFinalize}
          onClick={() => perform(signal => decide('approve_final', signal))}>{run.machine_verdict === 'pass' ? '최종 승인' : '사람 확인 후 최종 승인'}</button>}
        <label>수정 요청 사유 <input disabled={busy} value={reason} onChange={e => setReason(e.target.value)} /></label>
        <button className="rv-button secondary" disabled={busy || !reason.trim()} onClick={() => perform(signal => decide('approve_changes', signal))}>수정 요청 승인</button>
      </>}
      {status === 'completed' && <p role="status">담당자 승인 완료</p>}
      {run?.approvals?.flatMap(approval => (approval.human_reviews || []).map(item => <section key={approval.decision + item.criterion_id}>
        <h3>담당자 확인 · {item.criterion_id} · {verdicts[item.verdict]}</h3>
        <p>{item.observed}</p><p>근거: {item.reference} · 사유: {item.reason}</p><p>검토자: {approval.actor_id}</p>
      </section>))}
      <ErrorMessage message={error} />
      {error && <button disabled={busy} onClick={() => { setExcluded([]); setReason(''); setError(''); setReload(n => n + 1); }}>서버 상태 다시 확인</button>}
      <button className="text-btn" disabled={busy} onClick={onNew}>새 검수</button>
    </section></main><WorkspaceFooter /></div>;
}
