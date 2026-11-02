import { useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { post, projectUrl } from './api';
import { inputError, useAction, useApi } from './useApi';
import { useWorkspace } from './Projects';
import { RunSummary, useRun } from './Runs';
import type { Approval, ChangeRequest, ChangeRequestItem, CriteriaVersion, Run } from './types';
import { dateTime, Empty, ErrorNotice, Field, JsonView, Loading, PanelHeading, ReadOnly, Status, submit, Success } from './ui';

export function Approvals() {
  const { project, canEdit } = useWorkspace(); const runs = useApi<Run[]>(projectUrl(project.id, 'runs'), true); const [params, setParams] = useSearchParams();
  const selected = runs.data?.find(run => run.id === params.get('run_id')) ?? runs.data?.[0];
  const runId = params.get('run_id') ?? selected?.id ?? null; const query = useRun(runId);
  const criteria = useApi<CriteriaVersion[]>(projectUrl(project.id, 'criteria'), true);
  const run = query.data;
  return <><div className="page-heading"><div><h1>수정·승인</h1><p>수정 요청을 검토해 전달하고 재검수 뒤 최종·예외 승인을 기록합니다.</p></div><Link className="button" to="../history">승인 이력 보기</Link></div>
    {!canEdit && <ReadOnly archived={project.archived} />}
    <section className="sheet padded"><Field label="대상 검사 실행"><select value={runId ?? ''} onChange={event => setParams({ run_id: event.target.value })} disabled={!runs.data?.length}>
      {!runs.data?.length && <option value="">실행을 선택해 주세요</option>}{(runs.data ?? []).map(item => <option key={item.id} value={item.id}>{dateTime(item.created_at)} · {item.id}</option>)}
      {runId && !runs.data?.some(item => item.id === runId) && <option value={runId}>{runId}</option>}
    </select></Field><ErrorNotice error={runs.error} retry={runs.reload} />{runs.loading && <Loading />}{runs.data?.length === 0 && <Empty title="수정·승인할 실행이 없습니다">검사 실행에서 산출물을 검사한 뒤 이 화면에서 검토할 수 있습니다.</Empty>}
    </section>
    <ErrorNotice error={query.error} retry={query.reload} /><ErrorNotice error={criteria.error} retry={criteria.reload} />{query.loading && !run && <Loading />}
    {run && <><section className="sheet"><PanelHeading title="검사 상태"><Link className="button small" to={`/projects/${project.id}/runs/${run.id}`}>결과·근거 확인</Link><button className="button small" onClick={query.reload}>상태 조회</button></PanelHeading><RunSummary run={run} /></section>
      <ChangeRequests key={run.id} run={run} criteria={criteria.data?.find(item => item.id === run.criteria_version_id)} />
      <FinalApproval key={`approval-${run.id}`} run={run} criteria={criteria.data?.find(item => item.id === run.criteria_version_id)} refresh={query.reload} />
    </>}
  </>;
}

function ChangeRequests({ run, criteria }: { run: Run; criteria?: CriteriaVersion }) {
  const { project, canEdit, canApprove } = useWorkspace(); const query = useApi<ChangeRequest[]>(`/runs/${encodeURIComponent(run.id)}/change-requests`, true);
  const save = useAction(); const approve = useAction(); const [editing, setEditing] = useState(false); const [message, setMessage] = useState(''); const [items, setItems] = useState<ChangeRequestItem[]>([]);
  const [selectedId, setSelectedId] = useState(''); const [confirmed, setConfirmed] = useState(false); const [dirty, setDirty] = useState(false);
  const selected = query.data?.find(item => item.id === selectedId) ?? query.data?.[0];
  const available = run.snapshot?.items ?? criteria?.items ?? (run.results ?? []).map(result => ({ id: result.criterion_id, text: result.criterion_id }));
  const draftAllowed = canEdit && !run.approval && ['waiting_review', 'completed'].includes(run.state);
  function edit(request?: ChangeRequest) {
    if (dirty && !window.confirm('저장하지 않은 수정 요청 편집을 버릴까요?')) return;
    setSelectedId(request?.id ?? ''); setMessage(request?.message ?? ''); setItems((request?.items ?? []).map(item => ({ ...item }))); setEditing(true); setDirty(false); setConfirmed(false); save.clear(); approve.clear();
  }
  return <section className="sheet"><PanelHeading title="수정 요청 초안">{draftAllowed && <button className="button small" onClick={() => edit()}>새 초안 작성</button>}<button className="button small" onClick={query.reload}>초안 새로 조회</button></PanelHeading>
    {canEdit && !draftAllowed && <p className="padded hint">검사 완료 후 최종 승인 전까지 수정 요청을 작성할 수 있습니다.</p>}
    <ErrorNotice error={query.error} retry={query.reload} />{query.loading && <Loading />}
    {query.data && (query.data.length ? <div className="table-scroll"><table><thead><tr><th>수정 요청</th><th>상태</th><th>등록일</th><th>열기</th></tr></thead><tbody>{query.data.map(request => <tr className={selected?.id === request.id ? 'selected-row' : ''} key={request.id}>
      <td>{request.message || '수정 요청'}<small className="block id-cell">{request.id}</small></td><td><Status value={request.status} /></td><td>{dateTime(request.created_at)}</td><td><button className="text-button" onClick={() => { if (dirty && !window.confirm('저장하지 않은 편집을 버리고 요청을 열까요?')) return; setSelectedId(request.id); setEditing(false); setDirty(false); setConfirmed(false); approve.clear(); }}>내용 보기</button></td>
    </tr>)}</tbody></table></div> : <Empty title="수정 요청 초안이 없습니다">실제 검사 결과를 바탕으로 초안을 작성해 주세요. 자동 초안 생성이 실패했다면 실행 오류도 확인해 주세요.</Empty>)}
    {selected && !editing && <div className="padded form-stack"><div className="criterion-title"><h3>요청 내용</h3><Status value={selected.status} /></div><p className="preserve-lines">{selected.message}</p>
      <ul className="request-items">{(selected.items ?? []).map((item, index) => <li key={`${item.criterion_id}-${index}`}><strong>{available.find(value => value.id === item.criterion_id)?.text ?? item.criterion_id}</strong><p>{item.message}</p></li>)}</ul>
      {draftAllowed && <div><button className="button" onClick={() => edit(selected)}>내용 편집해 새 초안 만들기</button></div>}
      {selected.status === 'draft' && canApprove && !run.approval && <div className="approval-confirm"><label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />근거와 수정 내용을 확인했습니다.</label>
        <button className="button primary" disabled={!confirmed || approve.pending} onClick={() => void approve.run(async () => { await post(`/change-requests/${encodeURIComponent(selected.id)}/approve`); setConfirmed(false); query.reload(); }, '수정 요청을 승인했습니다. 수정 버전을 업로드한 뒤 전체 재검수를 진행해 주세요.')}>수정 요청 승인</button></div>}
      {selected.status === 'approved' && <div className="actions"><Link className="button primary" to={`/projects/${project.id}/assets?asset_id=${encodeURIComponent(run.asset_id)}&change_request_id=${encodeURIComponent(selected.id)}&run_id=${encodeURIComponent(run.id)}`}>연결한 수정 버전 업로드</Link>
        <Link className="button" to={`/projects/${project.id}/runs?previous_run_id=${encodeURIComponent(run.id)}`}>새 버전 전체 재검수</Link></div>}
      {selected.approved_at && <p className="hint">수정 요청 승인일: {dateTime(selected.approved_at)}</p>}
    </div>}
    {editing && draftAllowed && <form className="form-stack padded draft-editor" onSubmit={submit(() => save.run(async () => {
      if (!message.trim()) return inputError('수정 요청의 전체 메시지를 입력해 주세요.');
      if (!items.length || items.some(item => !item.criterion_id || !item.message.trim())) return inputError('수정할 조건과 수정 내용을 한 개 이상 입력해 주세요.');
      if (new Set(items.map(item => item.criterion_id)).size !== items.length) return inputError('같은 조건의 수정 내용을 한 항목으로 모아 주세요.');
      const created = await post<ChangeRequest>(`/runs/${encodeURIComponent(run.id)}/change-requests`, { message: message.trim(), items: items.map(item => ({ ...item, message: item.message.trim() })) });
      setSelectedId(created.id); setEditing(false); setDirty(false); setConfirmed(false); query.reload();
    }, '편집 내용을 새 수정 요청 초안으로 저장했습니다. 저장된 초안을 확인하고 승인해 주세요.'))}>
      <h3>초안 편집</h3><p className="hint">편집한 내용은 새 초안으로 저장됩니다. 기존 요청과 승인 기록은 유지됩니다.</p>
      <Field label="전체 수정 요청 메시지"><textarea required rows={3} value={message} onChange={event => { setMessage(event.target.value); setDirty(true); }} /></Field>
      {items.map((item, index) => <div className="request-item-editor" key={index}><Field label={`수정 조건 ${index + 1}`}><select required value={item.criterion_id} onChange={event => { setItems(current => current.map((value, itemIndex) => itemIndex === index ? { ...value, criterion_id: event.target.value } : value)); setDirty(true); }}><option value="">조건 선택</option>{available.map(value => <option key={value.id} value={value.id}>{value.text}</option>)}{item.criterion_id && !available.some(value => value.id === item.criterion_id) && <option value={item.criterion_id}>{item.criterion_id}</option>}</select></Field>
        <Field label="수정 내용"><textarea required rows={3} value={item.message} onChange={event => { setItems(current => current.map((value, itemIndex) => itemIndex === index ? { ...value, message: event.target.value } : value)); setDirty(true); }} /></Field><button type="button" className="text-button danger-text" onClick={() => { setItems(current => current.filter((_, itemIndex) => itemIndex !== index)); setDirty(true); }}>항목 제거</button></div>)}
      <div className="actions"><button type="button" className="button" disabled={!available.length} onClick={() => { setItems(current => [...current, { criterion_id: available[0]?.id ?? '', message: '' }]); setDirty(true); }}>수정 항목 추가</button>
        <button className="button primary" disabled={save.pending || !items.length}>{save.pending ? '저장 중…' : '새 초안 저장'}</button><button type="button" className="button" onClick={() => { if (!dirty || window.confirm('저장하지 않은 초안 편집을 닫을까요?')) { setEditing(false); setDirty(false); } }}>편집 닫기</button></div>
      <ErrorNotice error={save.error} />
    </form>}
    <ErrorNotice error={approve.error} /><Success>{approve.success}</Success><Success>{save.success}</Success>
  </section>;
}

function FinalApproval({ run, criteria, refresh }: { run: Run; criteria?: CriteriaVersion; refresh: () => void }) {
  const { project, canApprove } = useWorkspace(); const action = useAction(); const [mode, setMode] = useState<'final' | 'exception'>(run.verdict === 'pass' ? 'final' : 'exception');
  const [reason, setReason] = useState(''); const [exceptions, setExceptions] = useState<string[]>([]); const [confirmed, setConfirmed] = useState(false); const [approval, setApproval] = useState<Approval | null>(null);
  const results = run.results ?? [];
  const applicable = run.snapshot?.items ?? criteria?.items ?? results.map(result => ({ id: result.criterion_id, text: result.criterion_id, required: true }));
  const passed = (id: string) => results.some(result => result.criterion_id === id && result.execution_status === 'completed' && result.verdict === 'pass');
  const needed = new Set(applicable.filter(item => item.required && !passed(item.id)).map(item => item.id));
  const unresolved = (value: unknown) => Array.isArray(value) ? value.length > 0 : value && typeof value === 'object' ? Object.keys(value).length > 0 : Boolean(value);
  for (const group of run.snapshot?.groups ?? criteria?.groups ?? []) {
    const groupIds = group.criterion_ids.filter(id => applicable.some(item => item.id === id));
    if (unresolved(group.condition) || unresolved(group.exception)) groupIds.forEach(id => needed.add(id));
    else if (group.operator === 'any' && groupIds.some(passed)) groupIds.forEach(id => needed.delete(id));
  }
  if (run.verdict !== 'pass' && !needed.size) applicable.filter(item => !passed(item.id)).forEach(item => needed.add(item.id));
  const eligible = ['completed', 'waiting_review', 'failed', 'cancelled'].includes(run.state) && applicable.length > 0;
  const recorded = approval ?? run.approval;
  return <section className="sheet"><PanelHeading title="최종·예외 승인"><Link className="button small" to={`/projects/${project.id}/history`}>기록된 승인 조회</Link></PanelHeading><div className="padded form-stack">
    <p>사람의 승인은 원래 기계 판정과 실행 기록을 보존합니다. 예외 승인에는 사유와 대상 조건이 필요합니다.</p>
    {!eligible && <p className="notice caution">진행 중인 실행은 승인할 수 없습니다. 실행이 끝나고 적용 조건을 확인할 수 있을 때 승인해 주세요.</p>}
    {['failed', 'cancelled'].includes(run.state) && <p className="notice caution">실패·취소 실행입니다. 미검사 필수 조건을 포함한 예외 대상과 사유를 명시해야 합니다. 승인 후에도 실행 상태와 원판정은 유지됩니다.</p>}
    {!canApprove && <p className="notice info">최종·예외 승인은 프로젝트 승인자가 수행할 수 있습니다.</p>}
    {canApprove && !recorded && <form className="form-stack" onSubmit={submit(() => action.run(async () => {
      if (!eligible || !confirmed) return inputError('실행 상태와 근거 확인을 완료해 주세요.');
      if (mode === 'exception' && (!reason.trim() || !exceptions.length)) return inputError('예외 승인 사유와 대상 조건을 입력해 주세요.');
      if (mode === 'exception' && [...needed].some(id => !exceptions.includes(id))) return inputError('미검사 필수 조건과 미확정 관계를 포함한 필요한 예외 대상을 모두 지정해 주세요.');
      if (mode === 'final' && (run.verdict !== 'pass' || ['failed', 'cancelled'].includes(run.state))) return inputError('미충족·실패·취소 실행은 대상과 사유를 지정하는 예외 승인으로 기록해 주세요.');
      const created = await post<Approval>(`/runs/${encodeURIComponent(run.id)}/approve`, { exception_criterion_ids: mode === 'exception' ? exceptions : [], ...(mode === 'exception' ? { exception_reason: reason.trim() } : {}) });
      setApproval(created); setConfirmed(false); refresh();
    }, mode === 'exception' ? '예외 승인을 기록했습니다. 원판정은 유지됩니다.' : '최종 승인을 기록했습니다.'))}>
      <Field label="승인 유형"><select disabled={!eligible || action.pending} value={mode} onChange={event => { setMode(event.target.value as 'final' | 'exception'); setConfirmed(false); }}><option value="final" disabled={run.verdict !== 'pass' || ['failed', 'cancelled'].includes(run.state)}>최종 승인</option><option value="exception">사유를 남기는 예외 승인</option></select></Field>
      {mode === 'exception' && <><div className="actions"><button type="button" className="button small" disabled={!eligible} onClick={() => { setExceptions([...needed]); setConfirmed(false); }}>필요한 예외 대상 모두 선택</button><span className="hint">미검사 필수 조건과 미확정 관계를 포함해 검토해 주세요.</span></div>
        <fieldset className="group-members"><legend>예외로 승인할 적용 조건</legend>{applicable.map(item => { const result = results.find(value => value.criterion_id === item.id); return <label className="check" key={item.id}><input type="checkbox" disabled={!eligible} checked={exceptions.includes(item.id)} onChange={event => { setExceptions(current => event.target.checked ? [...current, item.id] : current.filter(id => id !== item.id)); setConfirmed(false); }} /><span>{item.text}<small className="block muted">{item.required ? '필수' : '권장'}{needed.has(item.id) ? ' · 예외 지정 필요' : ''}</small></span>{result ? <Status value={result.verdict} /> : <span className="status caution">미검사</span>}</label>; })}</fieldset>
        <Field label="예외 승인 사유"><textarea required rows={3} disabled={!eligible} value={reason} onChange={event => { setReason(event.target.value); setConfirmed(false); }} /></Field></>}
      <label className="check"><input type="checkbox" disabled={!eligible} checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />결과·근거·제한 사항과 승인 대상을 확인했습니다.</label>
      <ErrorNotice error={action.error} /><div><button className="button primary" disabled={action.pending || !eligible || !confirmed}>{mode === 'exception' ? '예외 승인 기록' : '최종 승인 기록'}</button></div>
    </form>}
    <Success>{action.success}</Success>
    {recorded && <div className="notice success"><strong>{recorded.exception_reason ? '사람의 예외 승인이 기록되어 있습니다.' : '사람의 최종 승인이 기록되어 있습니다.'}</strong><p>승인일 {dateTime(recorded.approved_at ?? recorded.created_at)}</p>{recorded.exception_reason && <><p>예외 사유: {recorded.exception_reason}</p><p>예외 대상: {recorded.exception_criterion_ids.map(id => applicable.find(item => item.id === id)?.text ?? id).join(', ')}</p></>}<p>원판정: <Status value={recorded.original_verdict} /></p></div>}
  </div></section>;
}
