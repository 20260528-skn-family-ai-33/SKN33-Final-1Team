import { useId, useMemo, useState } from 'react';
import { Link } from 'react-router';
import { projectUrl } from './api';
import { useApi } from './useApi';
import { useWorkspace } from './Projects';
import type { CriteriaVersion, Graph, GraphNode, HistoryEvent, HistoryResponse, Impact } from './types';
import { dateTime, Empty, ErrorNotice, Field, JsonView, Loading, PanelHeading, Status } from './ui';

const nodeLabels: Record<string, string> = { project: '프로젝트', document: '문서', criteria: '조건 기준', criteria_version: '조건 기준', criterion: '조건', group: '관계 그룹', logo: '로고', asset: '산출물', asset_version: '산출물 버전', version: '버전', run: '검사', result: '결과', evidence: '근거', change_request: '수정 요청', approval: '승인' };
const edgeLabels: Record<string, string> = { contains: '포함', defines: '조건 정의', version: '버전', resubmitted_as: '수정 재제출', inspects: '검사 대상', applies: '적용 기준', reinspected_as: '재검수', compares: '로고 대조', produces: '생성 결과', evaluated_by: '조건 판정', supported_by: '판정 근거', requests: '수정 요청', approves: '승인 대상' };
export function Relations() {
  const { project } = useWorkspace(); const graph = useApi<Graph>(projectUrl(project.id, 'graph')); const criteria = useApi<CriteriaVersion[]>(projectUrl(project.id, 'criteria'), true);
  const [criterionId, setCriterionId] = useState(''); const impact = useApi<Impact>(criterionId ? `${projectUrl(project.id, 'impact')}?criterion_id=${encodeURIComponent(criterionId)}` : null);
  const [selectedId, setSelectedId] = useState(''); const [kind, setKind] = useState('all'); const [zoom, setZoom] = useState(1);
  const kinds = Array.from(new Set((graph.data?.nodes ?? []).map(node => node.kind)));
  const visibleNodes = (graph.data?.nodes ?? []).filter(node => kind === 'all' || node.kind === kind);
  const selected = graph.data?.nodes.find(node => node.id === selectedId);
  const conditionItems = (criteria.data ?? []).flatMap(version => version.items.map(item => ({ ...item, version: version.version }))).filter((item, index, all) => all.findIndex(value => value.id === item.id) === index);
  return <><div className="page-heading"><div><h1>관계</h1><p>문서 조건·산출물·검사·승인 사이의 실제 연결과 영향 범위를 조회합니다.</p></div><button className="button" onClick={graph.reload}>관계 새로 조회</button></div>
    <section className="sheet"><PanelHeading title="프로젝트 관계"><Status value={graph.data?.status ?? project.graph_status} /></PanelHeading>
      <ErrorNotice error={graph.error} retry={graph.reload} />{graph.loading && <Loading label="관계 데이터를 조회하고 있습니다." />}
      {graph.data && <><div className="toolbar"><label>노드 구분<select value={kind} onChange={event => setKind(event.target.value)}><option value="all">전체</option>{kinds.map(value => <option key={value} value={value}>{nodeLabels[value] ?? value}</option>)}</select></label>
        <div className="actions"><button className="button small" disabled={zoom <= 0.6} onClick={() => setZoom(value => Math.max(0.6, value - 0.2))}>축소</button><button className="button small" onClick={() => setZoom(1)}>배율 초기화</button><button className="button small" disabled={zoom >= 2} onClick={() => setZoom(value => Math.min(2, value + 0.2))}>확대</button></div>
        <span className="muted">동기화 {dateTime(graph.data.synced_at)}</span></div>
        {!['synced', 'ready', 'completed'].includes(graph.data.status) && <p className="notice caution">관계 상태: {graph.data.status}. 원본의 최신 변경이 아직 관계 조회에 반영되지 않았을 수 있습니다.</p>}
        {graph.data.truncated && <p className="notice caution">관계 데이터가 서버 조회 한도에서 잘렸습니다. 모든 관계가 표시된 상태가 아닙니다.</p>}
        {visibleNodes.length ? <GraphDrawing nodes={visibleNodes} edges={graph.data.edges ?? []} selectedId={selectedId} onSelect={setSelectedId} zoom={zoom} /> : <Empty title="표시할 관계가 없습니다">자료 등록 후 관계 동기화 상태를 확인하고 새로 조회해 주세요.</Empty>}
        <div className="node-inspector padded"><h3>선택한 연결 대상</h3>{selected ? <><p><strong>{selected.label}</strong> · {nodeLabels[selected.kind] ?? selected.kind}</p>
          <ul>{graph.data.edges.filter(edge => edge.source === selected.id || edge.target === selected.id).map((edge, index) => <li key={index}>{graph.data?.nodes.find(node => node.id === edge.source)?.label ?? edge.source} → {edgeLabels[edge.kind] ?? edge.kind} → {graph.data?.nodes.find(node => node.id === edge.target)?.label ?? edge.target}</li>)}</ul>
          <div className="actions">{selected.kind === 'run' && <Link className="button small" to={`/projects/${project.id}/runs/${selected.id}`}>검사 결과 열기</Link>}{selected.kind === 'criterion' && typeof selected.criterion_id === 'string' && <button className="button small" onClick={() => setCriterionId(selected.criterion_id as string)}>이 조건 영향 조회</button>}</div>
          <details><summary>대상 정보 상세</summary><JsonView value={selected} /></details></> : <p className="muted">그래프 노드를 선택하면 전체 이름과 대상 정보를 확인할 수 있습니다.</p>}</div>
      </>}
    </section>
    <section className="sheet"><PanelHeading title="조건별 영향 조회" /><div className="padded form-stack"><ErrorNotice error={criteria.error} retry={criteria.reload} />
      <Field label="영향을 조회할 조건"><select value={criterionId} onChange={event => setCriterionId(event.target.value)}><option value="">조건 선택</option>{conditionItems.map(item => <option key={item.id} value={item.id}>조건 기준 v{item.version} · {item.text}</option>)}</select></Field>
      <ErrorNotice error={impact.error} retry={impact.reload} />{impact.loading && <Loading label="선택한 조건의 실제 연결을 조회하고 있습니다." />}
      {impact.data && <><div className="metadata-line">{impact.data.status && <Status value={impact.data.status} />}</div>
        {impact.data.nodes?.length ? <GraphDrawing nodes={impact.data.nodes} edges={impact.data.edges ?? []} selectedId={selectedId} onSelect={setSelectedId} zoom={1} /> : null}
        {impact.data.items && (impact.data.items.length ? <div className="table-scroll"><table><thead><tr><th>영향받는 검사</th><th>산출물 버전</th><th>조건 기준</th><th>승인 기록</th></tr></thead><tbody>{impact.data.items.map((item, index) => <tr key={`${item.run_id}-${index}`}><td><Link className="text-link id-cell" to={`/projects/${project.id}/runs/${item.run_id}`}>{item.run_id}</Link></td><td className="id-cell">{item.asset_version_id ?? '연결 없음'}</td><td className="id-cell">{item.criteria_version_id}</td><td className="id-cell">{item.approval_ids.length ? item.approval_ids.join(', ') : '기록 없음'}</td></tr>)}</tbody></table></div> : <Empty title="이 조건을 사용한 검사 연결이 없습니다" />)}
        {impact.data.truncated && <p className="notice caution">영향 조회가 서버 한도에서 잘렸습니다. 전체 영향 범위를 표시하지 못했습니다.</p>}
        <details><summary>영향 조회 원본</summary><JsonView value={impact.data} /></details></>}
      {!criterionId && <p className="hint">조건을 선택하면 서버에서 영향 범위를 조회합니다.</p>}
    </div></section>
  </>;
}

function GraphDrawing({ nodes, edges, selectedId, onSelect, zoom }: { nodes: GraphNode[]; edges: Graph['edges']; selectedId: string; onSelect: (id: string) => void; zoom: number }) {
  const markerId = `relation-${useId().replaceAll(':', '')}`;
  const layout = useMemo(() => {
    const orderedKinds = ['project', 'document', 'criteria', 'criteria_version', 'criterion', 'group', 'logo', 'asset', 'asset_version', 'version', 'run', 'result', 'evidence', 'change_request', 'approval'];
    const kinds = Array.from(new Set(nodes.map(node => node.kind))).sort((a, b) => {
      const first = orderedKinds.indexOf(a); const second = orderedKinds.indexOf(b); return (first < 0 ? 99 : first) - (second < 0 ? 99 : second);
    });
    const positions = new Map<string, { x: number; y: number }>(); let maxRows = 1;
    kinds.forEach((kind, column) => { const columnNodes = nodes.filter(node => node.kind === kind); maxRows = Math.max(maxRows, columnNodes.length); columnNodes.forEach((node, row) => positions.set(node.id, { x: 30 + column * 230, y: 54 + row * 94 })); });
    return { positions, kinds, width: Math.max(700, kinds.length * 230 + 10), height: Math.max(270, maxRows * 94 + 70) };
  }, [nodes]);
  return <div className="graph-scroll" tabIndex={0} role="region" aria-label="관계 그래프. 노드는 탭 키와 엔터 키로 선택할 수 있습니다."><svg width={layout.width * zoom} height={layout.height * zoom} viewBox={`0 0 ${layout.width} ${layout.height}`} className="relationship-graph" role="group" aria-label={`${nodes.length}개 노드의 실제 관계`}>
    <defs><marker id={markerId} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 8 4 0 8" fill="#778a91" /></marker></defs>
    {layout.kinds.map((kind, index) => <text x={30 + index * 230} y={28} className="graph-column" key={kind}>{nodeLabels[kind] ?? kind}</text>)}
    {edges.map((edge, index) => { const from = layout.positions.get(edge.source); const to = layout.positions.get(edge.target); if (!from || !to) return null; const startX = from.x + 180; const startY = from.y + 27; const endX = to.x; const endY = to.y + 27;
      return <path key={`${edge.source}-${edge.target}-${index}`} d={`M${startX},${startY} C${startX + 35},${startY} ${endX - 35},${endY} ${endX},${endY}`} className="graph-edge" markerEnd={`url(#${markerId})`}><title>{edgeLabels[edge.kind] ?? edge.kind}</title></path>;
    })}
    {nodes.map(node => { const at = layout.positions.get(node.id)!; return <g key={node.id} transform={`translate(${at.x},${at.y})`} className={`graph-node${selectedId === node.id ? ' active' : ''}`} role="button" tabIndex={0} aria-label={`${nodeLabels[node.kind] ?? node.kind}: ${node.label}`} aria-pressed={selectedId === node.id} onClick={() => onSelect(node.id)} onKeyDown={event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); onSelect(node.id); } }}>
      <title>{node.label}</title><rect width="180" height="54" rx="4" /><text x="12" y="31">{node.label.length > 18 ? `${node.label.slice(0, 17)}…` : node.label}</text>
    </g>; })}
  </svg></div>;
}

export function eventLabel(event: HistoryEvent) {
  const value = event.kind ?? event.event_type ?? event.type ?? event.action ?? '이력';
  const labels: Record<string, string> = {
    'project.created': '프로젝트 생성', 'project.archived': '프로젝트 보관', 'project.restored': '프로젝트 복원', 'membership.changed': '참여 권한 변경',
    'document.submitted': '문서 등록', 'document.extracted': '문서 조건 추출', 'criteria.created': '조건 버전 저장', 'criteria.approved': '조건 승인',
    'logo.created': '기준 로고 등록', 'logo.approved': '로고 승인', 'asset.version_created': '산출물 버전 등록',
    'run.submitted': '검사 접수', 'run.validated': '검사 근거 확인', 'run.awaiting_review': '검사 결과 검토 대기',
    'run.cancel_requested': '검사 취소 요청', 'run.attempt_finished': '검사 시도 종료', 'run.retried': '검사 재시도', 'run.approved': '최종 승인',
    'change_request.created': '수정 요청 작성', 'change_request.approved': '수정 요청 승인',
    'tool.started': '처리 시작', 'tool.finished': '처리 종료', 'job.yielded': '다음 묶음 대기', 'job.finished': '작업 종료',
    'graph.rebuild_requested': '관계 재구성 요청',
  };
  return labels[value] ?? value;
}
export function History() {
  const { project } = useWorkspace(); const query = useApi<HistoryResponse>(projectUrl(project.id, 'history')); const [search, setSearch] = useState('');
  const visible = (query.data?.items ?? []).filter(event => JSON.stringify(event).toLowerCase().includes(search.toLowerCase()) || eventLabel(event).includes(search));
  const approvals = (query.data?.approvals ?? []).filter(approval => JSON.stringify(approval).toLowerCase().includes(search.toLowerCase()));
  return <><div className="page-heading"><div><h1>이력</h1><p>등록·실행·수정·승인의 실제 기록을 조회합니다.</p></div><button className="button" onClick={query.reload}>새로 조회</button></div>
    <section className="sheet"><PanelHeading title="프로젝트 작업·승인 기록" /><div className="toolbar"><label className="search-field"><span className="sr-only">이력 검색</span><input type="search" placeholder="작업·대상 ID·기록 검색" value={search} onChange={event => setSearch(event.target.value)} /></label></div>
      <ErrorNotice error={query.error} retry={query.reload} />{query.loading && <Loading />}
      {query.data && (visible.length ? <div className="table-scroll"><table><thead><tr><th>시각</th><th>작업</th><th>사용자</th><th>기록</th></tr></thead><tbody>{visible.map((event, index) => <tr key={event.id ?? index}>
        <td className="nowrap">{dateTime(event.created_at ?? event.at)}</td><td>{eventLabel(event)}</td><td>{event.actor_name ?? event.actor_id ?? event.user_id ?? '기록 없음'}</td><td>{event.message && <p>{event.message}</p>}<details><summary>원본 기록 보기</summary><JsonView value={event} /></details></td>
      </tr>)}</tbody></table></div> : <Empty title={search ? '검색한 이력이 없습니다' : '아직 기록된 이력이 없습니다'}>자료 등록과 승인·실행 후 이 화면에서 변경을 추적할 수 있습니다.</Empty>)}
    </section>
    <section className="sheet"><PanelHeading title="사람의 승인 기록" />{query.data && (approvals.length ? <div className="table-scroll"><table><thead><tr><th>승인일·유형</th><th>승인 대상</th><th>원판정</th><th>예외 사유·조건</th></tr></thead><tbody>{approvals.map(approval => <tr key={approval.id}><td>{dateTime(approval.created_at)}<small className="block">{approval.target_type === 'criteria' ? '조건 기준 승인' : approval.target_type === 'logo' ? '로고 승인' : approval.target_type === 'change_request' ? '수정 요청 승인' : approval.exception_reason ? '예외 승인' : '최종 승인'}</small></td><td className="id-cell">{approval.run_id ? <Link to={`/projects/${project.id}/runs/${approval.run_id}`}>{approval.run_id}</Link> : approval.target_id}</td><td><Status value={approval.original_verdict} /></td><td>{approval.exception_reason ?? '예외 사유 없음'}{approval.exception_criterion_ids?.length > 0 && <small className="block id-cell">예외 조건: {approval.exception_criterion_ids.join(', ')}</small>}</td></tr>)}</tbody></table></div> : <Empty title="조회된 승인 기록이 없습니다" />)}</section></>;
}
