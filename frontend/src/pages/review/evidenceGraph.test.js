import test from 'node:test';
import assert from 'node:assert/strict';
import { evidenceGraph } from './evidenceGraph.js';

const criterion = (id, label, expected, extra = {}) => ({
  criterion_id: id, text: `${label}: ${expected}`, kind: 'text', expected, operator: 'contains', unit: null, required: true,
  source: { document_version_id: 'doc-1', page: 2, block_id: 'b1', quote: `${label}은 ${expected}로 표기한다.`, source_hash: 'a'.repeat(64) },
  details: { target: 'poster_text', field: 'venue', presentation: { label, value: expected } }, ...extra,
});
const item = (verdict, extra = {}) => ({
  criterion_id: 'venue', criterion_text: '행사 장소: 시민공원', kind: 'text', operator: 'contains', required: true,
  expected: '시민공원', observed: '시청광장', observation_status: 'observed', verdict, issues: [], ...extra,
});
function run(runId, assetId, items, extra = {}) {
  return {
    context: { run_id: runId, project_id: 'p1', actor_id: 'u1', document_version_id: 'doc-1', criteria_version_id: 'cv-1', asset_version_id: assetId },
    execution_status: 'waiting_review', human_decision: 'unreviewed', machine_verdict: 'fail', baseline: { criteria_version_id: 'cv-1' },
    criteria: { criteria: [criterion('venue', '행사 장소', '시민공원')] }, validated: { verdict: 'fail', items }, approvals: [], step_records: [], ...extra,
  };
}
const boxed = (assetId, bbox = [.1, .7, .6, .9]) => ({ image: { asset_version_id: assetId, asset_hash: 'h', bbox } });
const entry = (number, current, criteriaVersion = 'cv-1') => ({ run_id: 'run-' + number, revision_no: number, parent_run_id: number ? 'run-' + (number - 1) : null,
  current, asset_version_id: 'asset-' + number, criteria_version_id: criteriaVersion, criteria_version_no: 3 });
const byId = graph => Object.fromEntries(graph.nodes.map(node => [node.id, node]));

test('불충족 항목은 포스터에서 읽은 값부터 요구사항 원문까지 근거 사슬로 잇는다', () => {
  const current = run('run-1', 'asset-1', [item('fail', { issues: ['value_mismatch'], ...boxed('asset-1') })],
    { step_records: [{ role: 'A06', finished_at: '2026-11-12T01:20:00+00:00' }] });
  const graph = evidenceGraph(current, 'venue', {
    image: { url: '/poster', width: 1080, height: 1350 },
    retrieval: { backend: 'neo4j', required_revision: 12, observed_graph_revision: 12, fallback_reason: null,
      source_keys: [{ criterion_id: 'venue', source_key: 'source:cv-1:venue:document' }, { criterion_id: 'date', source_key: 'source:cv-1:date:document' }],
      relations: { run_ids: ['run-1'], approval_ids: [], truncated: false } },
  });
  const nodes = byId(graph);
  assert.equal(graph.label, '행사 장소');
  assert.deepEqual(graph.verdict, { code: 'fail', label: '불충족' });
  assert.deepEqual([graph.expected, graph.observed, graph.operator], ['시민공원', '시청광장', '기대값이 들어 있는지 비교']);
  assert.deepEqual(graph.reasons, ['값이 기준과 다름']);
  assert.deepEqual(graph.nodes.map(node => node.id), ['poster', 'run', 'criteria', 'region', 'observation', 'verdict', 'criterion', 'source']);
  assert.deepEqual(graph.nodes.filter(node => node.chain).map(node => node.title),
    ['검수 포스터', '포스터 아래쪽', '‘시청광장’', '불충족', '행사 장소', '요구사항 문서 2쪽']);
  assert.deepEqual(graph.path.map(step => step.text),
    ['검수 포스터', '포스터 아래쪽', '‘시청광장’ 읽음', '불충족', '행사 장소 기준 ‘시민공원’', '요구사항 문서 2쪽']);
  assert.equal(nodes.poster.sub, '1,080 × 1,350 px');
  assert.equal(nodes.run.sub, '2026-11-12 10:20 검수');
  assert.equal(nodes.verdict.tone, 'fail');
  assert.equal(nodes.verdict.sub, '값이 기준과 다름');
  assert.deepEqual(nodes.verdict.facts.find(([name]) => name === '비교한 값'), ['비교한 값', '기준 ‘시민공원’ ↔ 읽은 값 ‘시청광장’']);
  assert.equal(nodes.observation.sub, '읽음 · 아래쪽');
  assert.equal(nodes.criterion.sub, '기대값 ‘시민공원’');
  assert.equal(nodes.source.sub, '행사 장소은 시민공원로 표기한다.');
  // 그래프(Neo4j) 투영에 있는 노드만 그래프 키를 단다. 읽은 값·위치·판정은 실행 기록이다
  assert.deepEqual(graph.nodes.filter(node => node.graph).map(node => node.graph.key),
    ['poster:asset-1', 'run:run-1', 'criteria:cv-1', 'criterion:cv-1:venue', 'source:cv-1:venue:document']);
  assert.ok(['region', 'observation', 'verdict'].every(id => nodes[id].record && !nodes[id].graph));
  assert.deepEqual(graph.edges.map(edge => [edge.id, edge.via]), [['inspects', 'record'], ['applies', 'graph'], ['contains', 'graph'],
    ['region', 'record'], ['reads', 'record'], ['evidence', 'record'], ['judged', 'record'], ['criterion', 'record'], ['source', 'graph']]);
  assert.deepEqual(graph.edges.filter(edge => edge.chain).map(edge => edge.label), ['영역', '읽음', '근거', '기준', '출처']);
  assert.deepEqual(graph.box, [.1, .7, .6, .9]);
  assert.deepEqual(graph.lookup, { backend: 'neo4j', requiredRevision: 12, graphRevision: 12, fallback: null,
    sourceKeys: ['source:cv-1:venue:document'], history: null });
  assert.deepEqual(graph.trail, []);
});

test('확인 필요 항목은 없는 근거를 점선으로 두고 담당자 확인을 잇는다', () => {
  const review = item('review', { observed: '', observation_status: 'unreadable', issues: ['model_review', 'missing_expected_value'], expected: '' });
  const graph = evidenceGraph(run('run-1', 'asset-1', [review], { criteria: { criteria: [] } }), 'venue');
  const nodes = byId(graph);
  assert.equal(graph.label, '행사 장소: 시민공원');
  assert.deepEqual(graph.reasons, ['검사 모델이 사람 확인을 요청함', '기준값이 아직 정해지지 않음']);
  assert.deepEqual([nodes.region.title, nodes.region.dashed], ['위치 정보 없음', true]);
  assert.deepEqual([nodes.observation.title, nodes.observation.sub, nodes.observation.dashed], ['읽을 수 없음', '비교할 값이 없음', true]);
  assert.deepEqual([nodes.verdict.title, nodes.verdict.sub], ['확인 필요', '기준값이 아직 정해지지 않음']);
  assert.equal(nodes.criterion.sub, '기대값 미정');
  assert.deepEqual([nodes.source.title, nodes.source.dashed, nodes.source.graph], ['원문 정보 없음', true, null]);
  assert.deepEqual([nodes.human.title, nodes.human.dashed, nodes.human.slot], ['결정 대기', true, [2, 2]]);
  assert.deepEqual(graph.edges.filter(edge => edge.dashed).map(edge => edge.id), ['region', 'reads', 'source', 'human']);
  assert.equal(graph.edges.find(edge => edge.id === 'human').label, '확인 요청');
  assert.deepEqual(graph.edges.filter(edge => edge.via !== 'record'), []);
  assert.equal(graph.lookup.backend, null);
  assert.equal(graph.box, null);
  // 담당자 결정이 끝났는데 이 항목의 확인 기록이 없으면 대기라고 하지 않는다
  const decided = evidenceGraph(run('run-1', 'asset-1', [review], { human_decision: 'changes_approved' }), 'venue');
  assert.equal(byId(decided).human.title, '확인 기록 없음');
});

test('충족 항목은 사유 대신 맞았다는 설명을 달고 담당자 노드를 두지 않는다', () => {
  const graph = evidenceGraph(run('run-0', 'asset-0', [item('pass', { observed: '시민공원', ...boxed('asset-0', [0, 0, 1, 1]) })]), 'venue',
    { history: { answered_by: 'postgres_fallback', entries: [entry(0, true)] } });
  const nodes = byId(graph);
  assert.equal(nodes.poster.title, '원본 포스터');
  assert.equal(nodes.criteria.title, '승인 기준 v3');
  assert.equal(nodes.region.title, '포스터 전체');
  assert.equal(nodes.verdict.sub, '기준과 읽은 값이 맞음');
  assert.deepEqual(nodes.verdict.facts.find(([name]) => name === '사유'), ['사유', '기준과 포스터에서 읽은 값이 맞아 충족으로 판정했습니다.']);
  assert.equal(nodes.human, undefined);
  assert.equal(nodes.previous, undefined);
  assert.deepEqual(graph.trail, []);
  assert.equal(graph.lookup.history, null);
});

test('수정본이면 이전 검수의 판정과 담당자 확인을 관계도에 더한다', () => {
  const history = { answered_by: 'neo4j', entries: [entry(0, false), entry(1, false, 'cv-0'), entry(2, true), entry(3, false)] };
  const runs = { 'run-0': run('run-0', 'asset-0', [item('fail', { issues: ['value_mismatch'], ...boxed('asset-0', [.2, .1, .5, .2]) })]),
    'run-1': run('run-1', 'asset-1', [item('pass')]) };
  const current = run('run-2', 'asset-2', [item('review', { observed: '시민공원 일대', issues: ['qualifier_interpretation_required'], ...boxed('asset-2') })], {
    approvals: [{ decision: 'approve_final', reason: '확인', human_reviews: [{ criterion_id: 'venue', verdict: 'pass', observed: '시민공원 표기 확인',
      reference: '포스터 하단', reason: '허용 범위 안입니다.', checked_asset: true }] }] });
  const graph = evidenceGraph(current, 'venue', { history, runs });
  const nodes = byId(graph);
  // 기준 버전이 다른 검수(run-1)와 읽지 못한 검수(run-3)는 버전별 판정에서 빠진다
  assert.deepEqual(graph.trail.map(point => [point.runId, point.name, point.verdict.label, point.current]),
    [['run-0', '원본', '불충족', false], ['run-2', '2차 수정본', '확인 필요', true]]);
  assert.deepEqual(graph.trail[0].box, [.2, .1, .5, .2]);
  assert.equal(graph.trail[0].assetVersionId, 'asset-0');
  assert.deepEqual([nodes.previous.title, nodes.previous.sub, nodes.previous.runId, nodes.previous.slot], ['원본 · 불충족', '읽은 값 ‘시청광장’', 'run-0', [1, 2]]);
  assert.deepEqual(nodes.previous.facts.find(([name]) => name === '판정 변화'), ['판정 변화', '불충족 → 확인 필요']);
  assert.deepEqual(graph.edges.find(edge => edge.id === 'previous'), { id: 'previous', from: 'previous', to: 'observation', label: '재검수',
    relation: 'reinspected_as', dashed: true, via: 'graph' });
  assert.equal(graph.lookup.history, 'neo4j');
  assert.deepEqual([nodes.human.title, nodes.human.tone, nodes.human.dashed], ['담당자 판단 · 충족', 'pass', undefined]);
  assert.deepEqual(graph.edges.find(edge => edge.id === 'human'), { id: 'human', from: 'verdict', to: 'human', label: '직접 확인', via: 'record', dashed: false });
  assert.deepEqual(nodes.verdict.facts.at(-1), ['담당자', '담당자 판단 · 충족']);
  assert.equal(nodes.poster.title, '2차 수정본 포스터');

  const exception = evidenceGraph(run('run-0', 'asset-0', [item('fail', { issues: ['value_mismatch'] })],
    { approvals: [{ decision: 'approve_exception', criterion_ids: ['venue'], reason: '행사장 변경을 승인했습니다.' }] }), 'venue');
  assert.deepEqual([byId(exception).human.title, byId(exception).human.sub], ['예외 승인', '행사장 변경을 승인했습니다.']);
});

test('없는 항목은 만들지 않고, 다른 포스터 버전의 위치와 PostgreSQL 대체 조회를 구분한다', () => {
  assert.equal(evidenceGraph(run('run-1', 'asset-1', [item('pass')]), 'missing'), null);
  assert.equal(evidenceGraph(null, 'venue'), null);
  const stale = run('run-1', 'asset-1', [item('fail', { issues: ['observed_absence'], observed: '장소 표기 없음', observation_status: 'absent', ...boxed('asset-0') })], {
    criteria: { extraction: { input_kind: 'free_text' }, criteria: [criterion('venue', '행사 장소', '시민공원', {
      value_source: { quote: '시민공원으로 확정', reference: '담당자 메일' }, details: { target: 'file_metadata', field: 'file_format' } })] } });
  const graph = evidenceGraph(stale, 'venue', { retrieval: { backend: 'postgresql', required_revision: 7, observed_graph_revision: null,
    fallback_reason: 'GRAPH_REVISION_MISMATCH', source_keys: [], relations: { run_ids: [] } } });
  const nodes = byId(graph);
  assert.equal(graph.box, null);
  assert.equal(nodes.region.sub, '파일 정보로 확인하는 항목');
  assert.deepEqual([nodes.observation.title, nodes.observation.dashed], ['포스터에서 찾지 못함', true]);
  assert.deepEqual(nodes.observation.facts.find(([name]) => name === '검사 메모'), ['검사 메모', '장소 표기 없음']);
  assert.equal(nodes.source.title, '직접 입력한 요구사항');
  assert.deepEqual(nodes.source.facts.at(-1), ['보완 회신', '시민공원으로 확정 · 출처 담당자 메일']);
  assert.deepEqual(graph.edges.filter(edge => edge.via === 'ledger').map(edge => edge.id), ['contains', 'source']);
  assert.equal(graph.edges.find(edge => edge.id === 'applies').via, 'record');
  assert.deepEqual([graph.lookup.backend, graph.lookup.fallback], ['postgresql', 'GRAPH_REVISION_MISMATCH']);
});
