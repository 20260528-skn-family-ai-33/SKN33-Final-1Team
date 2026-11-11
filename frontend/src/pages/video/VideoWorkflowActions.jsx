import React, { useEffect, useRef, useState } from 'react';
import { fetchJson } from '../../api/client.js';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';

export const decisionLabels = {
  unreviewed: '담당자 확인 대기', changes_approved: '수정 요청 승인', final_approved: '최종 승인',
};

export default function VideoWorkflowActions({ job, onDecision, onRevise, onOpenRun }) {
  const [reason, setReason] = useState('');
  const [notes, setNotes] = useState({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [history, setHistory] = useState(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const active = useRef(null);
  const command = useRef(null);
  const path = '/api/video-reviews/' + encodeURIComponent(job.id);
  const decision = job.workflow.human_decision;
  const options = job.approval_options;
  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => {
    if (!historyOpen) return undefined;
    const controller = new AbortController();
    setHistoryError('');
    fetchJson(path + '/revisions', { signal: controller.signal })
      .then(body => { if (!controller.signal.aborted) setHistory(body.items); })
      .catch(failure => { if (!controller.signal.aborted) setHistoryError(failure.message); });
    return () => controller.abort();
  }, [path, historyOpen, decision]);

  async function approve(action) {
    if (active.current) return;
    const fields = { decision: action, criteria_version: job.criteria_version, asset_sha256: job.media.sha256,
      reason: action === 'approve_changes' ? reason.trim() : '',
      human_checks: action === 'approve_final' ? notes : {} };
    const key = JSON.stringify(fields);
    if (command.current?.key !== key) command.current = { key, id: crypto.randomUUID() };
    const controller = new AbortController();
    active.current = controller;
    setPending(true);
    setError('');
    try {
      const updated = await fetchJson(path + '/approvals', { method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...fields, command_id: command.current.id }) });
      if (!controller.signal.aborted) onDecision(updated);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message);
    } finally {
      if (!controller.signal.aborted) { active.current = null; setPending(false); }
    }
  }

  return <section className="rv-video-coverage" aria-label="영상 승인과 수정본">
    <h3>{decisionLabels[decision]} · 영상 v{job.workflow.asset_version}</h3>
    {decision === 'unreviewed' && <>
      {(options?.human_check_ids || []).map(id => <label key={id}>
        {job.criteria.find(item => item.id === id)?.text || '광고·협찬 표시'} 직접 확인 내용과 근거
        <textarea value={notes[id] || ''} maxLength={500} disabled={pending}
          onChange={event => setNotes(previous => ({ ...previous, [id]: event.target.value }))} />
      </label>)}
      {options?.blocked && <p>위반이나 근거가 부족한 항목은 수정한 뒤 다시 검수해 주세요.</p>}
      <button type="button" className="rv-button primary" disabled={pending || !options || options.blocked ||
        options.human_check_ids.some(id => !notes[id]?.trim())} onClick={() => approve('approve_final')}>최종 승인</button>
      <label>수정 요청 사유 <textarea value={reason} maxLength={500} disabled={pending} onChange={event => setReason(event.target.value)} /></label>
      <button type="button" className="rv-button secondary" disabled={pending || !reason.trim()}
        onClick={() => approve('approve_changes')}>수정 요청 승인</button>
    </>}
    {decision === 'changes_approved' && <button type="button" className="rv-button secondary" onClick={onRevise}>수정본 등록</button>}
    <ErrorMessage message={error} />
    <details onToggle={event => setHistoryOpen(event.currentTarget.open)}>
      <summary>영상·기준·승인 이력</summary>
      <ErrorMessage message={historyError} />
      {historyOpen && !history && !historyError && <p role="status">검수 이력을 불러오고 있어요.</p>}
      {history && <ol>{history.map(item => <li key={item.id}>
        <button type="button" className="text-btn" onClick={() => onOpenRun(item.id)}>영상 v{item.asset_version} · 기준 v{item.criteria_version}</button>
        <span> · {item.status === 'completed' ? decisionLabels[item.human_decision] : item.status === 'failed' ? '검수 실패' : '검수 중'}</span>
        {item.decisions.map(record => <p key={record.command_id}>{record.reason || '근거 확인 완료'} · {record.approved_at}</p>)}
      </li>)}</ol>}
    </details>
  </section>;
}
