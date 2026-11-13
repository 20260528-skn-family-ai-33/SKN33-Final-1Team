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

test('광고·협찬 표시는 고른 규정의 근거 사슬을 만들고 입력한 정보로 보는 규정은 입력값을 근거로 둔다', () => {
  const hit = { frame_id: 't001000', time_ms: 1000, text: '유료 광고 포함', bbox_xyxy: [184, 84, 356, 114], class: 'allowed' };
  const data = job([], {
    disclosure_input: { kind: 'endorsement', title: '가을 축제 브이로그', caption: '' },
    disclosure: { applicable: true, status: 'review', text_time_status: 'pass', severity: 'warn', reviewer: 'manager', reason: '시인성 확인이 남았습니다.',
      rules: { R0: { status: 'pass', note: '적용 대상 확정' }, R2: { status: 'review', note: '제목 앞 15자 안의 허용 문구' },
        R3a: { status: 'pass', evidence: [hit], note: '[0, 3초) 0.2초 프레임' }, R5: { status: 'review', note: '담당자 확인' } },
      notes: ['표본 문구 확인과 표시 규정 충족은 다른 상태입니다.'] },
  });
  const lead = open(data);
  assert.equal(lead.kind, 'disclosure');
  assert.equal(lead.rule, 'R2');   // 위반이 없으면 확인 필요인 첫 규정부터 본다
  assert.deepEqual(lead.rules.map(rule => [rule.key, rule.label, rule.verdict.code]),
    [['R0', '적용 대상', 'pass'], ['R2', '제목 앞 15자', 'review'], ['R3a', '시작 3초', 'pass'], ['R5', '형태·시인성', 'review']]);
  const title = byId(lead);
  assert.equal(title.observation.kind, '입력한 정보');
  assert.equal(title.observation.sub, '‘가을 축제 브이로그’');
  assert.equal(title.frame.dashed, true);
  assert.equal(title.human.title, '결정 대기');
  assert.deepEqual(lead.path.map(step => step.id), ['video', 'frame', 'observation', 'verdict', 'rule', 'guideline']);

  const screen = open(data, 0, { rule: 'R3a' });
  const nodes = byId(screen);
  assert.equal(screen.rule, 'R3a');
  assert.equal(nodes.observation.title, '‘유료 광고 포함’');
  assert.equal(nodes.verdict.tone, 'pass');
  assert.equal(nodes.rule.title, '시작 3초');
  assert.equal(nodes.human, undefined);
  assert.equal(screen.evidence[0].raw.label, '시작 3초');
  assert.ok(nodes.guideline.facts.some(([name]) => name === '안내'));   // 숫자 기준은 내부 기준이라는 안내
  for (const edge of screen.edges) assert.ok(nodes[edge.from] && nodes[edge.to], edge.id);
});

test('규정이 없는 광고·협찬 판정과 없는 항목도 처리한다', () => {
  const data = job([], { disclosure_input: { kind: 'self' },
    disclosure: { applicable: false, status: 'not_applicable', severity: 'info', reviewer: 'auto', reason: '광고주 자체 광고입니다.', rules: {} } });
  const graph = open(data);
  const nodes = byId(graph);
  assert.deepEqual(graph.verdict, { code: 'not_applicable', label: '대상 아님' });
  assert.equal(graph.rule, null);
  assert.equal(nodes.policy, undefined);
  assert.equal(nodes.rule.title, '적용 대상');
  assert.equal(nodes.observation.sub, '광고주 자체 광고');
  for (const edge of graph.edges) assert.ok(nodes[edge.from] && nodes[edge.to], edge.id);
  assert.equal(videoEvidence(data, null), null);
  assert.equal(videoEvidence(null, judgmentItems(data)[0]), null);
});
