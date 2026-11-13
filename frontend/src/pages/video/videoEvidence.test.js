import test from 'node:test';
import assert from 'node:assert/strict';
import { videoEvidence } from './videoEvidence.js';
import { judgmentItems } from './videoFormat.js';

const frame = (ms, sampling = 'base') => ({ frame_id: `t${String(ms).padStart(6, '0')}`, time_ms: ms, sampling, width: 540, height: 960, lines: [] });
const segment = (ms, text, extra = {}) => ({ track_id: 'T001', segment_id: 'S01', start_ms: ms, end_ms: ms + 2000, frame_id: frame(ms).frame_id, time_ms: ms,
  width: 540, height: 960, bbox_xyxy: [108, 768, 432, 864], text, ...extra });
function job(results, extra = {}) {
  return {
    id: 'job-1', status: 'completed', created_at: '2026-11-12T01:20:00+00:00', criteria_version: 2,
    media: { duration_ms: 12000, width: 540, height: 960 }, coverage: { duration_ms: 12000, inspected_ms: [0, 1000, 2000] },
    criteria: [{ id: 'c1', text: '행사 문구: 가을 축제', type: 'information', source: '행사 문구는 ‘가을 축제’로 표기한다.' },
      { id: 'c2', text: '로고가 잘리지 않을 것', type: 'visual', source: 'user' }],
    frames: [0, 1000, 2000, 3000, 4000].map(ms => frame(ms)), review: { results }, ...extra,
  };
}
const result = (status, extra = {}) => ({ criterion_id: 'c1', criterion: '행사 문구: 가을 축제', type: 'information', status, observed_text: '가을 축재',
  reason: '화면 문구가 기준과 다르게 읽혔습니다.', evidence_segments: [segment(1000, '가을 축재'), segment(3000, '가을 축제')], mismatch: [],
  whole_video: false, severity: 'block', reviewer: 'auto', ...extra });
const byId = graph => Object.fromEntries(graph.nodes.map(node => [node.id, node]));
const open = (data, index = 0, options) => videoEvidence(data, judgmentItems(data)[index], options);

test('위반 항목은 영상 프레임에서 읽은 문구부터 기준 출처까지 근거 사슬로 잇는다', () => {
  const data = job([result('fail', { mismatch: [{ kind: 'one_character', expected: '가을 축제', observed: '가을 축재' }] })]);
  const graph = open(data);
  const nodes = byId(graph);
  assert.deepEqual(graph.verdict, { code: 'fail', label: '위반' });
  assert.equal(graph.tag, '정보 검수 · 자동 판정');
  assert.deepEqual(graph.nodes.map(node => node.id), ['video', 'run', 'criteria', 'frame', 'observation', 'verdict', 'criterion', 'source', 'mismatch']);
  assert.deepEqual(graph.nodes.filter(node => node.chain).map(node => node.id), ['video', 'frame', 'observation', 'verdict', 'criterion', 'source']);
  assert.deepEqual(graph.path.map(step => step.id), ['video', 'frame', 'observation', 'verdict', 'criterion', 'source']);
  assert.equal(nodes.frame.title, '0:01 프레임');
  assert.equal(nodes.observation.title, '‘가을 축재’');
  assert.equal(nodes.verdict.tone, 'fail');
  assert.equal(nodes.source.title, '기준 문서 원문');
  assert.equal(nodes.run.sub, '2026-11-12 10:20 검수');
  assert.equal(nodes.mismatch.title, '한 글자 다름');
  assert.equal(graph.quote, '행사 문구는 ‘가을 축제’로 표기한다.');
  // 프레임 픽셀 좌표는 0~1 비율 상자로 바뀐다
  assert.deepEqual(graph.shown.box, [0.2, 0.8, 0.8, 0.9]);
  assert.equal(graph.shown.hint, '아래');
  assert.equal(graph.evidence.length, 2);
  assert.equal(graph.still, null);
  // 모든 관계는 있는 노드끼리만 잇고, 담당자 확인 노드는 확인 필요일 때만 생긴다
  for (const edge of graph.edges) assert.ok(nodes[edge.from] && nodes[edge.to], edge.id);
  assert.equal(nodes.human, undefined);
});

test('근거 위치를 바꾸면 읽은 위치와 읽은 문구 노드가 그 프레임을 따른다', () => {
  const graph = open(job([result('review', { reviewer: 'manager', severity: 'warn' })]), 0, { shown: 1 });
  const nodes = byId(graph);
  assert.equal(graph.shown.frameId, 't003000');
  assert.equal(nodes.frame.title, '0:03 프레임');
  assert.equal(nodes.observation.sub, '0:03 프레임에서 읽음');
  assert.deepEqual(nodes.observation.facts[1], ['이 프레임', '‘가을 축제’']);
  assert.equal(nodes.human.title, '결정 대기');
  assert.ok(graph.edges.some(edge => edge.id === 'human' && edge.dashed));
  // 범위를 넘는 순서는 마지막 근거로 맞춘다
  assert.equal(open(job([result('review')]), 0, { shown: 9 }).shown.frameId, 't003000');
});

test('위치 근거가 없는 항목은 점선 노드로 두고 영상의 한 장면만 보여 준다', () => {
  const data = job([{ criterion_id: 'c2', criterion: '로고가 잘리지 않을 것', type: 'visual', status: 'review', observed_text: '', reason: '',
    evidence_segments: [], mismatch: [], whole_video: true, severity: 'warn', reviewer: 'manager' }]);
  const graph = open(data);
  const nodes = byId(graph);
  assert.equal(graph.shown, null);
  assert.deepEqual(graph.still, { frameId: 't002000', timeMs: 2000, ratio: 540 / 960 });
  assert.equal(nodes.frame.dashed, true);
  assert.equal(nodes.frame.sub, '영상 전체에서 확인할 항목');
  assert.equal(nodes.observation.title, '읽은 문구 없음');
  assert.equal(nodes.source.title, '직접 입력한 기준');
  assert.deepEqual(graph.notes, ['영상 전체에서 확인할 항목입니다.']);
  assert.equal(graph.quote, null);
});

test('광고·협찬 판정이 기록된 이전 검수도 결과 목록과 근거 창에 넣지 않는다', () => {
  const data = job([], { disclosure_input: { kind: 'endorsement' },
    disclosure: { applicable: true, status: 'review', reason: '시인성 확인이 남았습니다.', rules: { R0: { status: 'pass' } } } });
  assert.deepEqual(judgmentItems(data), []);
  assert.equal(videoEvidence(data, { key: 'disclosure', kind: 'disclosure', disclosure: data.disclosure }), null);
  assert.equal(videoEvidence(data, null), null);
  assert.equal(videoEvidence(null, judgmentItems(job([result('fail')]))[0]), null);
});
