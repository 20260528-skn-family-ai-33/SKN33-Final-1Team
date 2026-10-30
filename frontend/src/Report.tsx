import { Link, useParams } from 'react-router';
import { fileUrl, projectUrl } from './api';
import { useApi } from './useApi';
import type { Asset, CriteriaVersion, HistoryResponse, Project, Run } from './types';
import { dateTime, Empty, ErrorNotice, formatValue, frameTime, IssueList, JsonView, Loading, Status } from './ui';
import { eventLabel } from './Relations';
import { publicSnapshot, RunScope } from './Runs';

export function Report() {
  const { projectId = '', runId = '' } = useParams();
  const project = useApi<Project>(projectUrl(projectId)); const runQuery = useApi<Run>(`/runs/${encodeURIComponent(runId)}`);
  const criteria = useApi<CriteriaVersion[]>(projectUrl(projectId, 'criteria'), true); const assets = useApi<Asset[]>(projectUrl(projectId, 'assets'), true);
  const history = useApi<HistoryResponse>(projectUrl(projectId, 'history'));
  const run = runQuery.data?.project_id === projectId ? runQuery.data : null;
  const basis = criteria.data?.find(item => item.id === run?.criteria_version_id); const asset = assets.data?.find(item => item.id === run?.asset_id);
  const assetVersion = run?.snapshot?.asset_version ?? asset?.versions.find(item => item.id === run?.asset_version_id);
  const events = (history.data?.items ?? []).filter(event => event.run_id === runId);
  const approval = run?.approval ?? history.data?.approvals?.find(item => item.run_id === runId && item.target_type === 'run');
  const applied = run?.snapshot?.items ?? basis?.items ?? [];
  const missing = applied.filter(criterion => !run?.results?.some(result => result.criterion_id === criterion.id));
  return <main id="main" className="report-page"><div className="report-actions no-print"><Link className="button" to={`/projects/${projectId}/runs/${runId}`}>결과로 돌아가기</Link><button className="button primary" disabled={!run || !project.data} onClick={() => window.print()}>인쇄 / PDF 저장</button></div>
    <ErrorNotice error={project.error} retry={project.reload} /><ErrorNotice error={runQuery.error} retry={runQuery.reload} />
    {(project.loading || runQuery.loading) && <Loading />}
    {runQuery.data && !run && <p className="notice error" role="alert">요청한 프로젝트와 실행의 프로젝트가 일치하지 않습니다.</p>}
    {run && project.data && <article className="report-paper"><header className="report-heading"><p className="report-brand">크리에이티브체커</p><h1>산출물 검수 보고서</h1><p>{project.data.name}</p><p className="muted">실행 {run.id}</p></header>
      <section><h2>검수 대상과 기준</h2><dl className="report-facts"><div><dt>산출물</dt><dd>{asset?.name ?? run.asset_id}</dd></div><div><dt>파일 버전</dt><dd>{assetVersion ? `v${assetVersion.version} · ${assetVersion.filename}` : run.asset_version_id}</dd></div>
        <div><dt>조건 기준</dt><dd>{basis ? `v${basis.version}` : run.criteria_version_id}</dd></div><div><dt>실행 등록일</dt><dd>{dateTime(run.created_at)}</dd></div><div><dt>실행 상태</dt><dd><Status value={run.state} /></dd></div><div><dt>기계 판정</dt><dd><Status value={run.verdict} /></dd></div></dl>
        <ErrorNotice error={criteria.error} retry={criteria.reload} /><ErrorNotice error={assets.error} retry={assets.reload} /><IssueList issues={run.errors} />
        <p className="hint">사람의 승인은 아래 이력과 함께 확인합니다. 실행 실패·미검사·불확실한 결과는 이 보고서에서 충족으로 변경하지 않습니다.</p>
      </section>
      <section><h2>조건별 결과·근거</h2>{run.results?.length ? run.results.map((result, index) => { const criterion = applied.find(item => item.id === result.criterion_id); return <section className="report-result" key={result.criterion_id}>
        <h3>{index + 1}. {criterion?.text ?? result.criterion_id}</h3><div className="metadata-line"><span>{criterion ? criterion.required ? '필수' : '권장' : '조건 상세 조회 필요'}</span><Status value={result.verdict} /><Status value={result.execution_status} /></div>
        <dl className="comparison-values"><div><dt>기대값</dt><dd>{formatValue(result.expected)}</dd></div><div><dt>관측값</dt><dd>{formatValue(result.observed)}</dd></div></dl><p>{result.reason || '판정 사유 기록 없음'}</p>
        {criterion?.source && <p className="report-source">조건 출처: {criterion.source.kind === 'document' ? '문서' : '사용자 입력'}{criterion.source.page != null ? ` ${criterion.source.page}페이지` : ''}{criterion.source.quote ? ` · ${criterion.source.quote}` : ''}</p>}
        {result.evidence?.length ? <ol className="report-evidence">{result.evidence.map((evidence, evidenceIndex) => <li key={evidenceIndex}><a href={fileUrl(evidence.file_id)} target="_blank" rel="noreferrer">근거 파일 {evidence.file_id}</a>{evidence.frame_ms != null && <span> · 영상 {frameTime(evidence.frame_ms)}</span>}{evidence.bbox && <span> · 위치 [{evidence.bbox.join(', ')}]</span>}{evidence.text && <blockquote>{evidence.text}</blockquote>}</li>)}</ol> : <p className="notice caution">검증된 근거 파일 없음</p>}
        {result.limitations?.length > 0 && <div className="report-limitations"><strong>판정 제한</strong><ul>{result.limitations.map((value, limitIndex) => <li key={limitIndex}>{value}</li>)}</ul></div>}
      </section>; }) : <Empty title="기록된 검사 결과가 없습니다">실행 상태와 오류를 확인해 주세요.</Empty>}{missing.length > 0 && <div className="missing-criteria"><h3>미검사 적용 조건</h3><ul>{missing.map(criterion => <li key={criterion.id}><strong>{criterion.text}</strong> · {criterion.required ? '필수' : '권장'} · 미검사<p>기대값: {formatValue(criterion.expected)}</p></li>)}</ul></div>}</section>
      <section><h2>실행 범위·제한 사항</h2><RunScope run={run} /><details open className="report-snapshot"><summary>실행에 고정된 기준 기록</summary><JsonView value={publicSnapshot(run)} /></details></section>
      <section><h2>사람의 최종·예외 승인</h2>{approval ? <div className="form-stack"><p><strong>{approval.exception_reason ? '예외 승인' : '최종 승인'}</strong> · {dateTime(approval.created_at)}</p><p>승인자: {approval.approved_by}</p><p>원판정: <Status value={approval.original_verdict} /></p>{approval.exception_reason && <><p className="preserve-lines">예외 사유: {approval.exception_reason}</p><ul>{approval.exception_criterion_ids.map(id => <li key={id}>{applied.find(item => item.id === id)?.text ?? id}</li>)}</ul></>}<p className="hint">사람의 승인은 실행 상태와 원판정을 변경하지 않습니다.</p></div> : <p>조회된 최종·예외 승인 기록이 없습니다.</p>}</section>
      <section><h2>이 실행의 작업·승인 이력</h2><ErrorNotice error={history.error} retry={history.reload} />{history.loading && <Loading />}
        {history.data && (events.length ? <ol className="report-history">{events.map((event, index) => <li key={event.id ?? index}><strong>{eventLabel(event)}</strong><span> · {dateTime(event.created_at ?? event.at)}</span><JsonView value={event} /></li>)}</ol> : <p>이 실행에 연결된 이력이 조회되지 않았습니다. 기계 판정은 사람의 승인을 뜻하지 않습니다.</p>)}
      </section><footer className="report-footer">자료는 등록된 프로젝트 권한으로 조회됩니다. 이 문서는 조회 시점의 서버 기록을 표시합니다.</footer>
    </article>}
  </main>;
}
