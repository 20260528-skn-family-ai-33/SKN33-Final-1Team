import React, { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { useAuth } from './auth.jsx';
import * as api from './api.js';
import * as C from './contracts.js';
import { useAction, usePending, useResource, useReview } from './hooks.jsx';
import { Pager, PendingPanel, Resource, Title } from './components.jsx';
import { ErrorMessage } from '../components/ErrorMessage.jsx';
export const statusName = value => ({ queued: '접수됨', running: '실행 중', completed: '완료', failed: '실행 실패', imported: '가져온 이력' })[value] || '상태 확인 필요';
const verdictName = value => ({ pass: '충족', fail: '미충족', review: '확인 필요' })[value] || '판정 없음';
const stageName = value => ({ queued: '접수', preprocessing: '이미지 준비', ocr: '문자 인식', extraction: '정보 추출', review: '항목 검수', checking: '항목 검수', completed: '결과 저장 완료', failed: '실행 종료' })[value] || '서버 처리 중';
export function HistoryPage() {
  const { projectId } = useParams(); const [params, setParams] = useSearchParams(); const status = params.get('status') || ''; const cursor = params.get('cursor'); const imageId = params.get('image_id');
  const state = useResource(`history:${projectId}:${status}:${imageId}:${cursor}`, async signal => { const [project, page] = await Promise.all([api.project(projectId, signal), api.reviews(projectId, { status, image_id: imageId, cursor }, signal)]); return { project, page }; });
  return <><Title title="프로젝트 검수 이력">서버에 저장된 실행 상태와 당시 기준을 확인하세요.</Title><div className="v1-toolbar"><label>실행 상태<select aria-label="검수 상태 필터" value={status} onChange={event => setParams(current => { const next = new URLSearchParams(current); next.delete('cursor'); event.target.value ? next.set('status', event.target.value) : next.delete('status'); return next; })}><option value="">모두</option>{['queued', 'running', 'completed', 'failed', 'imported'].map(value => <option value={value} key={value}>{statusName(value)}</option>)}</select></label><Link className="btn secondary" to={`/projects/${projectId}/review/new`}>새 검수 준비</Link></div>
    <Resource state={state}>{({ project, page }) => <><p className="help">{project.name} · 생성 시각 역순</p><div className="v1-project-grid">{page.items.map(run => <article className="form-card" key={run.id}><span className="pill">{statusName(run.status)}</span><h2>{run.project_name}</h2><p className="help">{run.created_at ?? '생성 시각 미등록'}<br />{run.id}</p><p>자동 요약: {verdictName(run.overall_verdict)}</p>{run.retry_of && <p className="help">재검수 · 원본 {run.retry_of}</p>}<Link className="btn secondary" to={`/projects/${projectId}/reviews/${run.id}`}>실행 상세</Link></article>)}</div>{!page.items.length && <p className="form-card">조건에 맞는 검수 이력이 없습니다.</p>}<Pager page={page} path={`/projects/${projectId}/reviews`} filters={{ ...(status ? { status } : {}), ...(imageId ? { image_id: imageId } : {}) }} /></>}</Resource></>;
}
export function RunPage() {
  const { projectId, runId } = useParams(); const auth = useAuth(); const project = useResource(`run-project:${projectId}`, signal => api.project(projectId, signal)); const run = useReview(projectId, runId, auth.user.id);
  const navigate = useNavigate(); const action = useAction(`export:${runId}`); const objectUrls = useRef(new Set());
  const pending = usePending(projectId, (value, entry) => navigate(`/projects/${projectId}/${entry.operation === 'confirm_criteria' ? 'criteria' : 'reviews'}/${value.id}`));
  useEffect(() => () => { for (const url of objectUrls.current) URL.revokeObjectURL(url); objectUrls.current.clear(); }, [runId]);
  async function download() {
    const blob = await action.run(signal => api.download(projectId, runId, signal)); if (!blob) return;
    const url = URL.createObjectURL(blob); objectUrls.current.add(url); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `review-${runId}.json`; anchor.click();
    setTimeout(() => { URL.revokeObjectURL(url); objectUrls.current.delete(url); }, 1000);
  }
  return <><Title title="검수 실행 상세" >접수 이후의 실행 상태와 저장된 근거를 확인하세요.</Title>
    {run.error && <section className="v1-notice" role="status"><strong>실행 상태를 다시 확인해야 합니다.</strong><ErrorMessage message={run.error.message} /><p>통신 오류는 검수 실패 판정이 아닙니다. 마지막으로 확인한 서버 상태를 표시합니다.</p><button className="btn secondary" onClick={run.refresh}>실행 상태 다시 조회</button></section>}
    {!run.data && !run.error && <p role="status" className="form-card">서버 실행을 불러오고 있어요.</p>}
    <Resource state={project}>{project => run.data && <>
      <section className="form-card v1-run-status" aria-label="서버 실행 상태"><span className="pill">{statusName(run.data.status)}</span><h2>{run.data.project_name}</h2><p>{stageName(run.data.stage)} · {run.data.progress === null ? '진행률 미제공' : `${run.data.progress}%`}</p>{run.data.progress !== null && <progress max="100" value={run.data.progress} aria-label="서버 진행률" />}
        <p className="help">실행 ID {runId}<br />서버 자동 요약: {verdictName(run.data.overall_verdict)} · {run.data.results_complete ? '결과 전체 조회됨' : '결과 전체성 확인 필요'}</p>
        {run.data.status === 'imported' && <p className="v1-notice">가져온 과거 이력입니다. 로컬 검수 완료나 담당자의 승인으로 해석하지 않습니다.</p>}
        {run.data.error && <p className="v1-notice">실행이 완료되지 않았습니다. {run.data.error.outcome_unknown ? '중단 시점의 처리 결과를 확인해야 합니다.' : '서버 실행 기록을 확인해 주세요.'}</p>}
        <div className="v1-actions"><Link className="btn secondary" to={`/projects/${projectId}/criteria/${run.data.criteria_version_id}`}>당시 기준 v{run.data.criteria_version} 보기</Link><Link className="btn secondary" to={`/projects/${projectId}/reviews`}>검수 이력</Link>
          <button className="btn secondary" disabled={action.busy} onClick={download}>서버 결과 JSON 다운로드</button>
          <button className="btn primary" disabled={!C.editable(project) || pending.busy || run.data.origin_kind !== 'service_execution' || !['completed', 'failed'].includes(run.data.status)} onClick={() => pending.submit('retry_review', {}, runId)}>같은 기준으로 재검수</button></div><p className="help">재검수는 새 실행을 만듭니다. 초안이 변경되었다면 현재 초안을 다시 확정해야 합니다.</p><ErrorMessage message={action.error} /></section>
      <Results key={`${auth.user.id}:${runId}`} run={run.data} />
    </>}</Resource><PendingPanel mutation={pending} /></>;
}
// Structured observations remain observations, never a locally manufactured verdict.
function Observation({ value, depth = 0 }) {
  if (value === null || value === undefined) return <span>미등록</span>;
  if (typeof value !== 'object') return <span>{typeof value === 'boolean' ? (value ? '예' : '아니요') : String(value)}</span>;
  if (depth >= 5) return <span>중첩 값은 서버 JSON에서 확인해 주세요.</span>;
  if (Array.isArray(value)) return value.length ? <ul>{value.map((row, index) => <li key={index}><Observation value={row} depth={depth + 1} /></li>)}</ul> : <span>빈 목록</span>;
  return <dl className="v1-observation">{Object.entries(value).map(([key, row]) => <div key={key}><dt>{key}</dt><dd><Observation value={row} depth={depth + 1} /></dd></div>)}</dl>;
}
function Results({ run }) {
  const [selected, setSelected] = useState(null); const [ocr, setOcr] = useState(false); const [box, setBox] = useState(null); const [dimensionsMatch, setDimensionsMatch] = useState(false); const [imageError, setImageError] = useState(false);
  
  return <><div className="result-grid v1-results"><section className="form-card result-image-card"><h2>검수 이미지와 근거 위치</h2><div className="result-canvas"><img src={run.image.url} alt="실제 검수한 이미지" onLoad={event => { setDimensionsMatch(event.currentTarget.naturalWidth === run.image.width && event.currentTarget.naturalHeight === run.image.height); setImageError(false); }} onError={() => { setImageError(true); setDimensionsMatch(false); }} />{dimensionsMatch && box && <div className="evidence-box" data-testid="evidence-box" style={C.boxStyle(box, run.image)} />}</div>
      {imageError && <p className="v1-notice">이미지를 불러오지 못했습니다. 파일 상태와 접근 권한을 확인해 주세요.</p>}{!dimensionsMatch && !imageError && <p className="help">이미지 치수 확인 전에는 위치를 표시하지 않습니다.</p>}<p className="help">표시 이미지 {run.image.width}×{run.image.height} · 좌표계가 일치하는 근거만 표시합니다.</p></section>
      <section className="form-card"><h2>항목별 자동 판정</h2><p className="help">자동 판정은 담당자의 최종 승인 또는 학습 라벨이 아닙니다.</p>{run.results.map(result => <article className="result-item" key={result.result_id}><span className={`status-tag ${result.verdict || 'review'}`}>{verdictName(result.verdict)}</span><h3>{result.criterion}</h3><p>처리: {({ completed: '완료', failed: '실패', unsupported: '지원하지 않음', imported: '가져온 자료' })[result.processing_status]}</p><div className="v1-observed">관찰 내용: <Observation value={result.observed_text ?? result.observed_value} /></div><p>이유: {result.reason ?? '미등록'}</p><p className="help">검수 방법: {result.check_method ?? '미등록'}</p>{result.limitations !== null && result.limitations !== undefined && <div className="help">한계: <Observation value={result.limitations} /></div>}
        <button className="text-btn" onClick={() => { setSelected(selected === result.result_id ? null : result.result_id); setBox(null); setOcr(false); }}>근거 {result.evidence_count}개 {selected === result.result_id ? '닫기' : '조회'}</button>
        {selected === result.result_id && <Evidence key={result.result_id} run={run} result={result} onBox={setBox} />}
      </article>)}{!run.results.length && <p className="result-empty">{['queued', 'running'].includes(run.status) ? '아직 항목 결과가 저장되지 않았습니다.' : '조회할 항목 결과가 없습니다. 실행 상태와 전체성을 확인해 주세요.'}</p>}</section></div>
      <section className="form-card v1-spaced"><h2>문자 인식 원문</h2><p className="help">{run.ocr ? `서버 OCR 레코드 ${run.ocr.record_count}개` : 'OCR 완료 자료가 아직 확인되지 않았습니다.'}</p><button className="btn secondary" onClick={() => { setOcr(current => !current); setSelected(null); setBox(null); }}>{ocr ? 'OCR 원문 닫기' : 'OCR 원문 조회'}</button>{ocr && <Evidence key={`ocr:${run.ocr?.id || run.status}`} run={run} onBox={setBox} />}</section>
    </>;
}
function Evidence({ run, result, onBox }) {
  const read = (cursor, signal) => result ? api.evidence(run.id, result.result_id, cursor, signal) : api.ocr(run.id, cursor, signal);
  const resource = useResource(`evidence:${run.id}:${result?.result_id || 'ocr'}`, signal => read(null, signal));
  return <Resource state={resource}>{page => <EvidenceRows key={resource.data.meta.request_id} initial={page} read={read} image={run.image} onBox={onBox} expected={result?.evidence_count} />}</Resource>;
}
function EvidenceRows({ initial, read, image, onBox, expected }) {
  const [page, setPage] = useState(initial); const [active, setActive] = useState(null); const action = useAction(`page:${initial.meta.request_id}`);
  const total = expected ?? page.record_count; const complete = !page.has_more && page.items.length === total;
  return <div className="v1-evidence"><p role="status">{page.availability === 'pending' ? 'OCR 자료 준비 중' : page.availability === 'none' ? '서버에 OCR 완료 자료가 없습니다.' : total === 0 ? '서버에 등록된 근거가 없습니다.' : `${page.items.length} / ${total}개 조회 · ${complete ? '전체 조회됨' : '일부 조회됨'}`}</p>
    {page.items.map(record => { const style = C.boxStyle(record, image); return <article key={record.id}><p>{record.text ?? '문자 내용 미등록'}</p><p className="help">신뢰도: {record.confidence === null ? '미등록' : `${(record.confidence * 100).toFixed(1)}%`}</p><button className="text-btn" aria-pressed={active === record.id} disabled={!style} onClick={() => { setActive(record.id); onBox(record); }}>이미지에서 위치 보기</button>{!style && <p className="help">표시 가능한 위치가 없거나 좌표계가 다릅니다.</p>}</article>; })}
    {!page.has_more && !complete && total !== undefined && <p className="v1-notice">서버의 전체 개수와 조회한 자료가 다릅니다. 다시 조회해 주세요.</p>}
    {page.has_more && <button className="btn secondary" disabled={action.busy} onClick={async () => { const next = await action.run(signal => read(page.next_cursor, signal)); if (next) setPage(current => ({ ...next, items: [...current.items, ...next.items.filter(row => !current.items.some(old => old.id === row.id))] })); }}>근거 더 불러오기</button>}<ErrorMessage message={action.error} />
  </div>;
}
