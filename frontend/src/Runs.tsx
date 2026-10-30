import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { createIdempotencyKey, post, projectUrl } from './api';
import { inputError, useAction, useApi } from './useApi';
import { useWorkspace } from './Projects';
import type { Asset, CriteriaVersion, Criterion, Evidence, Result, Run } from './types';
import { dateTime, Empty, ErrorNotice, Field, FilePreview, formatValue, frameTime, IssueList, JsonView, Loading, PanelHeading, ReadOnly, Status, submit, Success } from './ui';

export function Runs() {
  const { project, canEdit } = useWorkspace(); const runs = useApi<Run[]>(projectUrl(project.id, 'runs'), true);
  return <><div className="page-heading"><div><h1>검사 실행</h1><p>산출물 버전과 승인된 조건을 고정해 전체 검사를 시작합니다.</p></div><button className="button" onClick={runs.reload}>상태 새로 조회</button></div>
    {!canEdit && <ReadOnly archived={project.archived} />}
    {canEdit && <RunCreate previousRuns={runs.data ?? []} />}
    <section className="sheet"><PanelHeading title="실행 목록" /><ErrorNotice error={runs.error} retry={runs.reload} />{runs.loading && <Loading />}
      {runs.data && (runs.data.length ? <div className="table-scroll"><table><thead><tr><th>실행·등록일</th><th>산출물 버전</th><th>실행 상태</th><th>기계 판정</th><th>처리 진행</th></tr></thead><tbody>{runs.data.map(run => <tr key={run.id}>
        <td><Link className="table-link" to={run.id}>{dateTime(run.created_at)}</Link><small className="block id-cell">{run.id}</small></td><td className="id-cell">{run.asset_version_id}</td><td><Status value={run.state} /></td><td><Status value={run.verdict} /></td><td>{run.progress?.total > 0 && run.progress.unit ? `${run.progress.completed} / ${run.progress.total} ${progressUnit(run)}` : '처리 단위 집계 대기'}</td>
      </tr>)}</tbody></table></div> : <Empty title="아직 실행한 검사가 없습니다">문서 조건과 기준 로고를 승인하고 산출물 버전을 선택해 시작해 주세요.</Empty>)}
    </section></>;
}
function RunCreate({ previousRuns }: { previousRuns: Run[] }) {
  const { project } = useWorkspace(); const navigate = useNavigate(); const [params] = useSearchParams(); const action = useAction();
  const assets = useApi<Asset[]>(projectUrl(project.id, 'assets'), true); const criteria = useApi<CriteriaVersion[]>(projectUrl(project.id, 'criteria'), true);
  const [assetVersionId, setAssetVersionId] = useState(params.get('asset_version_id') ?? ''); const [criteriaId, setCriteriaId] = useState('');
  const [previousRunId, setPreviousRunId] = useState(params.get('previous_run_id') ?? '');
  const key = useRef<{ payload: string; value: string } | null>(null);
  const approved = (criteria.data ?? []).filter(item => item.status === 'approved' && item.id === project.current_criteria_id);
  return <section className="sheet"><PanelHeading title="새 검사" /><form className="form-stack padded" onSubmit={submit(() => action.run(async () => {
    if (!assetVersionId) return inputError('검사할 산출물 버전을 선택해 주세요.');
    const payload = { asset_version_id: assetVersionId, ...(criteriaId ? { criteria_version_id: criteriaId } : {}), ...(previousRunId ? { previous_run_id: previousRunId } : {}) };
    const serialized = JSON.stringify(payload);
    if (key.current?.payload !== serialized) key.current = { payload: serialized, value: createIdempotencyKey() };
    const run = await post<Run>(projectUrl(project.id, 'runs'), payload, { 'Idempotency-Key': key.current.value });
    key.current = null; navigate(`/projects/${project.id}/runs/${run.id}`);
  }))}>
    <ErrorNotice error={assets.error} retry={assets.reload} /><ErrorNotice error={criteria.error} retry={criteria.reload} />
    {(assets.loading || criteria.loading) && <Loading />}
    <div className="form-grid"><Field label="산출물 버전"><select required value={assetVersionId} onChange={event => setAssetVersionId(event.target.value)}><option value="">산출물 버전 선택</option>{(assets.data ?? []).flatMap(asset => [...asset.versions].sort((a, b) => b.version - a.version).map(version => <option key={version.id} value={version.id}>{asset.name} · {asset.kind === 'video' ? '영상' : '포스터'} v{version.version} · {version.filename}</option>))}</select></Field>
      <Field label="승인된 조건 기준"><select value={criteriaId} onChange={event => setCriteriaId(event.target.value)}><option value="">현재 승인 기준 사용</option>{approved.map(item => <option key={item.id} value={item.id}>조건 v{item.version}{item.id === project.current_criteria_id ? ' · 현재' : ''}</option>)}</select></Field></div>
    {criteria.data && !approved.length && <p className="notice caution">현재 승인된 조건 기준이 없습니다. 문서·조건에서 기준을 먼저 승인해 주세요.</p>}
    <Field label="이전 검사 연결 (선택)" hint="수정본 재검수는 이전 실행을 연결해 전체 조건을 다시 검사합니다."><select value={previousRunId} onChange={event => setPreviousRunId(event.target.value)}><option value="">신규 검사</option>{previousRuns.map(run => <option key={run.id} value={run.id}>{dateTime(run.created_at)} · {run.id}</option>)}{previousRunId && !previousRuns.some(run => run.id === previousRunId) && <option value={previousRunId}>{previousRunId}</option>}</select></Field>
    <p className="hint">실행 시 승인된 조건과 로고 버전을 고정합니다. 네트워크 오류 후 같은 입력을 다시 전송하면 동일한 요청 키를 사용합니다.</p>
    <ErrorNotice error={action.error} /><div><button className="button primary" disabled={action.pending || !assetVersionId || !approved.length || !!assets.error || !!criteria.error}>{action.pending ? '검사 생성 중…' : '전체 검사 시작'}</button></div>
  </form></section>;
}

export function useRun(runId: string | null) {
  const query = useApi<Run>(runId ? `/runs/${encodeURIComponent(runId)}` : null);
  useEffect(() => {
    if (!query.data || query.error || !['queued', 'running', 'cancel_requested'].includes(query.data.state)) return;
    const timer = window.setInterval(query.reload, 4000); return () => window.clearInterval(timer);
  }, [query.data?.state, query.error, query.reload]);
  return query;
}
const stageLabels: Record<string, string> = {
  A01: '실행 조정', A02: '조건 추출', A04: '산출물 검사', A05: '산출물 검사', A06: '결과 검증', A07: '수정 초안 작성', A08: '재검수 연결',
  queued: '작업 대기', checking: '산출물 검사', inspect: '산출물 검사', inspection: '산출물 검사', validation: '결과 검증',
  prepare: '검사 준비', resume: '검사 재개', awaiting_gpu: '검사 자원 대기',
  change_request: '수정 초안 작성', report: '보고·수정 초안 작성', human_review: '사람 확인 대기', completed: '실행 완료', failed: '실행 실패', cancelled: '취소됨',
};
function progressUnit(run: Run) { return run.progress?.unit === 'batch' ? '검사 묶음' : ['criterion', 'criteria'].includes(run.progress?.unit ?? '') ? '조건' : '작업 단위'; }
export function RunSummary({ run }: { run: Run }) {
  return <div className="run-summary"><dl><div><dt>실행 상태</dt><dd><Status value={run.state} /></dd></div><div><dt>기계 판정</dt><dd><Status value={run.verdict} /></dd></div>
    <div><dt>현재 단계</dt><dd>{run.stage ? stageLabels[run.stage] ?? run.stage : '단계 기록 대기'}</dd></div><div><dt>등록일</dt><dd>{dateTime(run.created_at)}</dd></div>{run.attempt != null && <div><dt>시도</dt><dd>{run.attempt}</dd></div>}</dl>
    {run.progress?.total > 0 && run.progress.unit ? <div className="progress-line"><progress value={Math.min(run.progress.completed, run.progress.total)} max={run.progress.total} aria-label={`서버가 보고한 ${progressUnit(run)} 처리 범위`} /><span>{run.progress.completed} / {run.progress.total} {progressUnit(run)} 처리</span></div> : <p className="hint">서버의 처리 단위 집계를 기다리고 있습니다.</p>}
    <IssueList issues={run.errors} />
  </div>;
}

export function RunDetail() {
  const { runId = '' } = useParams(); const { project, canEdit } = useWorkspace(); const query = useRun(runId); const action = useAction(); const retryKey = useRef<string | null>(null);
  const criteria = useApi<CriteriaVersion[]>(projectUrl(project.id, 'criteria'), true); const assets = useApi<Asset[]>(projectUrl(project.id, 'assets'), true);
  const run = query.data; const version = criteria.data?.find(item => item.id === run?.criteria_version_id); const asset = assets.data?.find(item => item.id === run?.asset_id);
  return <><div className="page-heading"><div><Link className="back-link" to={`/projects/${project.id}/runs`}>← 실행 목록</Link><h1>검사 결과와 근거</h1><p>실행 상태·기계 판정·사람의 승인을 구분해 확인합니다.</p></div><div className="actions"><button className="button" onClick={query.reload}>상태 조회</button>
    <Link className="button" to={`/projects/${project.id}/runs/${runId}/report`}>인쇄용 보고서</Link></div></div>
    <ErrorNotice error={query.error} retry={query.reload} />{query.loading && !run && <Loading />}
    {run && <><section className="sheet"><PanelHeading title={asset?.name ?? '검사 실행'}><div className="actions">
      {canEdit && ['queued', 'running'].includes(run.state) && <button className="button small danger" disabled={action.pending} onClick={() => void action.run(async () => { await post(`/runs/${encodeURIComponent(run.id)}/cancel`); query.reload(); }, '취소를 요청했습니다. 서버가 작업을 중단할 때까지 상태를 확인해 주세요.')}>실행 취소 요청</button>}
      {canEdit && ['failed', 'cancelled'].includes(run.state) && <button className="button small" disabled={action.pending} onClick={() => void action.run(async () => {
        retryKey.current ??= createIdempotencyKey(); await post(`/runs/${encodeURIComponent(run.id)}/retry`, undefined, { 'Idempotency-Key': retryKey.current }); retryKey.current = null; query.reload();
      }, '재시도를 요청했습니다. 실행 상태에서 새 시도를 확인해 주세요.')}>실행 재시도</button>}
      <Link className="button small primary" to={`/projects/${project.id}/approvals?run_id=${encodeURIComponent(run.id)}`}>수정·승인으로 이동</Link></div></PanelHeading>
      <RunSummary run={run} /><div className="padded metadata-line"><span className="id-cell">실행 {run.id}</span>{version && <span>조건 v{version.version}</span>}<span className="id-cell">산출물 버전 {run.asset_version_id}</span></div>
      {run.approval && <div className="padded notice success"><strong>{run.approval.exception_reason ? '사람의 예외 승인 기록' : '사람의 최종 승인 기록'}</strong><p>승인일 {dateTime(run.approval.created_at)}</p>{run.approval.exception_reason && <p>예외 사유: {run.approval.exception_reason}</p>}<p>원래 기계 판정과 실행 상태는 유지됩니다.</p></div>}
      <ErrorNotice error={action.error} /><Success>{action.success}</Success>
    </section><ErrorNotice error={criteria.error} retry={criteria.reload} /><ErrorNotice error={assets.error} retry={assets.reload} />
      <ResultsExplorer run={run} criteria={run.snapshot?.items ?? version?.items ?? []} asset={asset} />
      <section className="sheet padded"><RunScope run={run} /><details><summary>고정된 기준 버전과 적용 조건</summary><JsonView value={publicSnapshot(run)} /></details></section>
    </>}
  </>;
}

export function publicSnapshot(run: Run) {
  const snapshot = run.snapshot;
  return {
    document: snapshot?.document ? { id: snapshot.document.id, version: snapshot.document.version, filename: snapshot.document.filename } : undefined, criteria_version_id: run.criteria_version_id,
    asset: snapshot?.asset, asset_version: snapshot?.asset_version ? { id: snapshot.asset_version.id, version: snapshot.asset_version.version, filename: snapshot.asset_version.filename } : run.asset_version_id,
    items: snapshot?.items, groups: snapshot?.groups,
    inference_profile: snapshot?.inference_profile,
    logos: snapshot?.logos?.map(logo => ({ id: logo.id, name: logo.name, version: logo.version })),
    previous_run_id: snapshot?.previous_run_id ?? run.previous_run_id, change_request_id: snapshot?.change_request_id,
  };
}

export function RunScope({ run }: { run: Run }) {
  const report = run.report && typeof run.report === 'object' && !Array.isArray(run.report) ? run.report : null;
  const value = report?.coverage;
  const coverage = value && typeof value === 'object' && !Array.isArray(value) ? value : null;
  const samples = Array.isArray(coverage?.sampled_frame_ms) ? coverage.sampled_frame_ms.filter((sample): sample is number => typeof sample === 'number') : [];
  const intervals = Array.isArray(coverage?.uninspected_intervals) ? coverage.uninspected_intervals : [];
  const gaps = intervals.flatMap(interval => interval && typeof interval === 'object' && !Array.isArray(interval) && typeof interval.start_ms === 'number' && typeof interval.end_ms === 'number' ? [{ start: interval.start_ms, end: interval.end_ms }] : []);
  return <div className="run-scope"><h3>검사 범위와 제한</h3>
    {coverage ? <><p className="hint">{coverage.kind === 'video' ? '영상의 추출 프레임을 대상으로 한 검사입니다. 프레임 사이 구간·음성·정확한 연속 노출시간은 확인하지 않습니다.' : coverage.complete === true ? '서버가 전체 이미지 검사 범위를 기록했습니다.' : '일부 범위에 확인이 필요합니다.'}</p>
      {samples.length > 0 && <p>검사 시점: {samples.map(sample => frameTime(sample)).join(', ')}</p>}
      {gaps.length > 0 && <p>미관측 구간: {gaps.map(gap => `${frameTime(gap.start)}~${frameTime(gap.end)}`).join(', ')}</p>}
    </> : <p className="hint">검사 범위가 아직 기록되지 않았습니다.</p>}
    {typeof report?.summary === 'string' && <p className="preserve-lines">{report.summary}</p>}
    {report && <details><summary>서버가 기록한 검사 범위·제한 상세</summary><JsonView value={report} /></details>}
  </div>;
}

export function ResultsExplorer({ run, criteria, asset }: { run: Run; criteria: Criterion[]; asset?: Asset }) {
  const [filter, setFilter] = useState('all'); const [priority, setPriority] = useState('all'); const [selectedId, setSelectedId] = useState(''); const [evidenceIndex, setEvidenceIndex] = useState(0); const [density, setDensity] = useState('comfortable');
  const results = run.results ?? [];
  const missing = criteria.filter(criterion => !results.some(result => result.criterion_id === criterion.id) && (priority === 'all' || criterion.required === (priority === 'required')));
  const visible = results.filter(item => (filter === 'all' || item.verdict === filter) && (priority === 'all' || criteria.find(criterion => criterion.id === item.criterion_id)?.required === (priority === 'required')));
  const selected = visible.find(item => item.criterion_id === selectedId) ?? visible[0]; const selectedCriterion = criteria.find(item => item.id === selected?.criterion_id);
  const evidence: Evidence | undefined = selected?.evidence?.[evidenceIndex] ?? selected?.evidence?.[0];
  const assetVersion = asset?.versions.find(item => item.id === run.asset_version_id) ?? run.snapshot?.asset_version;
  const assetKind = asset?.kind ?? run.snapshot?.asset?.kind;
  const originalMedia = evidence?.file_id === assetVersion?.file_id;
  const previewKind = evidence ? originalMedia ? assetKind : 'image' : assetKind;
  return <div className={`results-grid density-${density}`}><section className="sheet result-table-pane"><PanelHeading title="조건별 결과"><label className="density-control">표시 밀도<select value={density} onChange={event => setDensity(event.target.value)}><option value="comfortable">보통</option><option value="compact">촘촘하게</option></select></label></PanelHeading>
    <div className="toolbar"><label>기계 판정<select value={filter} onChange={event => { setFilter(event.target.value); setEvidenceIndex(0); }}><option value="all">전체</option><option value="fail">위반</option><option value="review">확인 필요</option><option value="pass">충족</option></select></label>
      <label>중요도<select value={priority} onChange={event => { setPriority(event.target.value); setEvidenceIndex(0); }}><option value="all">필수·권장 전체</option><option value="required">필수</option><option value="recommended">권장</option></select></label><span className="muted">{visible.length}개 결과</span></div>
    {!results.length ? <Empty title="아직 검사 결과가 없습니다">실행 상태와 오류를 확인해 주세요. 결과가 없는 상태를 충족으로 처리하지 않습니다.</Empty> : !visible.length ? <Empty title="필터에 맞는 결과가 없습니다" /> : <div className="table-scroll"><table className="results-table"><thead><tr><th>조건</th><th>관측값</th><th>기계 판정</th><th>검사 상태</th></tr></thead><tbody>{visible.map(result => {
      const criterion = criteria.find(item => item.id === result.criterion_id);
      return <tr key={result.criterion_id} className={selected?.criterion_id === result.criterion_id ? 'selected-row' : ''}><td><button className="table-button" aria-pressed={selected?.criterion_id === result.criterion_id} onClick={() => { setSelectedId(result.criterion_id); setEvidenceIndex(0); }}>{criterion?.text ?? result.criterion_id}</button><small className="block muted">{criterion ? criterion.required ? '필수' : '권장' : '조건 상세 조회 필요'}</small></td>
        <td className="observed-cell">{formatValue(result.observed)}</td><td><Status value={result.verdict} /></td><td><Status value={result.execution_status} /></td></tr>;
    })}</tbody></table></div>}
    {missing.length > 0 && <div className="padded missing-criteria"><h3>미검사 적용 조건</h3><p className="hint">아래 조건에는 검사 결과가 기록되지 않았습니다. 충족으로 간주하지 않습니다.</p><ul>{missing.map(criterion => <li key={criterion.id}><strong>{criterion.text}</strong><span className="status caution">미검사</span><small className="block muted">{criterion.required ? '필수' : '권장'} · 기대값: {formatValue(criterion.expected)}</small></li>)}</ul></div>}
    {selected && <div className="result-explanation padded"><h3>{selectedCriterion?.text ?? '선택한 조건'}</h3><dl className="comparison-values"><div><dt>기대값</dt><dd>{formatValue(selected.expected)}</dd></div><div><dt>관측값</dt><dd>{formatValue(selected.observed)}</dd></div></dl>
      <p>{selected.reason || '판정 사유 기록이 없습니다.'}</p>{selected.limitations?.length > 0 && <div className="notice caution"><strong>판정 제한</strong><ul>{selected.limitations.map((value, index) => <li key={index}>{value}</li>)}</ul></div>}
      {selectedCriterion?.source.quote && <blockquote className="evidence-quote">원문: {selectedCriterion.source.quote}{selectedCriterion.source.page != null && <span> · {selectedCriterion.source.page}페이지</span>}</blockquote>}
    </div>}
  </section><section className="sheet evidence-pane"><PanelHeading title="근거 위치·영상 시점" />
    {selected ? <div className="padded"><EvidenceSelector result={selected} index={evidenceIndex} setIndex={setEvidenceIndex} />
      {evidence?.file_id ? <FilePreview fileId={evidence.file_id} kind={previewKind} title={`${selectedCriterion?.text ?? '검사'} 근거`} evidence={evidence} />
        : <><Empty title="검증된 근거 파일이 없습니다">근거가 없는 결과는 원문·관측값·제한 사항을 확인해 주세요.</Empty>{assetVersion && <details><summary>검사한 원본 산출물 보기</summary><FilePreview fileId={assetVersion.file_id} kind={assetKind} title={assetVersion.filename} /></details>}</>}
      {evidence?.bbox && <p className="hint">근거 좌표 (x, y, 너비, 높이): {evidence.bbox.map(value => String(value)).join(', ')}</p>}
    </div> : <Empty title="확인할 결과를 선택해 주세요">결과 표에서 조건을 선택하면 근거 파일과 영상 시점을 표시합니다.</Empty>}
  </section></div>;
}
function EvidenceSelector({ result, index, setIndex }: { result: Result; index: number; setIndex: (value: number) => void }) {
  if (!result.evidence?.length) return null;
  return <div className="evidence-selector" role="group" aria-label="조건의 근거 선택">{result.evidence.map((evidence, itemIndex) => <button className={`button small${index === itemIndex ? ' selected' : ''}`} key={`${evidence.file_id}-${itemIndex}`} aria-pressed={index === itemIndex} onClick={() => setIndex(itemIndex)}>근거 {itemIndex + 1}{evidence.frame_ms != null ? ` · ${frameTime(evidence.frame_ms)}` : ''}</button>)}</div>;
}
