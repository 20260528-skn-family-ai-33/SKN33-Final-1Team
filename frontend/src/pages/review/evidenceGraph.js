// 포스터 검수 한 항목의 판정 근거를 관계도(노드·관계)로 구성한다. 실행 기록(RunSnapshot)과 수정본 이력·근거 조회 기록만 읽고
// 판정을 새로 만들지 않는다. 그래프(Neo4j) 투영에 있는 노드는 graph 키를 달고, 읽은 값·위치·판정은 실행 기록에서 읽었다고 표시한다.
// 자리(slot)는 [열, 행]이다. 근거 사슬(chain)은 포스터 → 읽은 위치 → 읽은 값 → 판정 → 검수 기준 → 요구사항 원문이다.
import { CODES, itemLabel, kstMinute, locationHint, reasonCode, reasonText } from './reportBrief.js';

const OPERATORS = { equals: '기대값과 같은지 비교', contains: '기대값이 들어 있는지 비교', gte: '기대값 이상인지 비교',
  lte: '기대값 이하인지 비교', present: '요소가 있는지 확인', manual: '담당자가 직접 확인' };
const DECISIONS = { unreviewed: '검토 대기', changes_approved: '수정 요청 승인', final_approved: '최종 승인', exception_approved: '예외 승인' };
const RECORD = { inspect: '검수 실행 기록 · 포스터 검사 단계', validate: '검수 실행 기록 · 판정 검증 단계', approval: '담당자 승인 기록' };

const quoted = value => `‘${value}’`;
const withUnit = (value, unit) => {
  const text = value == null ? '' : String(value).trim();
  if (!text) return null;
  return !unit || text.endsWith(unit) ? text : text + unit;
};
const isBox = box => Array.isArray(box) && box.length === 4 && box.every(value => Number.isFinite(value) && value >= 0 && value <= 1)
  && box[0] < box[2] && box[1] < box[3];
const percent = value => `${Math.round(value * 100)}%`;
const versionName = entry => (entry.revision_no ? `${entry.revision_no}차 수정본` : '원본');

// 한 검수 실행에서 이 기준의 읽은 값·위치·판정. 위치 상자는 그 실행이 검사한 포스터 버전의 것만 쓴다.
function itemView(run, criterionId) {
  const item = (run?.validated?.items || []).find(entry => entry.criterion_id === criterionId);
  if (!item) return null;
  const asset = run.context?.asset_version_id || null;
  const image = item.image && (!item.image.asset_version_id || item.image.asset_version_id === asset) ? item.image : null;
  const status = item.observation_status || 'unknown';
  return {
    item, asset, status, box: image && isBox(image.bbox) ? [...image.bbox] : null,
    value: status === 'observed' || status === 'unknown' ? withUnit(item.observed, item.observed_unit) : null,
    statusLabel: CODES.observation[status] || CODES.observation.unknown,
    verdict: { code: item.verdict, label: CODES.verdict[item.verdict] || item.verdict },
  };
}

// 같은 승인 기준으로 검수한 원본·수정본에서 이 항목의 판정. 읽지 못한 검수는 빠진다.
function versionTrail(history, runs, run, criterionId) {
  const entries = history?.entries || [];
  const current = entries.find(entry => entry.current);
  if (!current || entries.length < 2) return [];
  const points = entries.filter(entry => entry.criteria_version_id === current.criteria_version_id).map(entry => {
    const view = itemView(entry.current ? run : runs?.[entry.run_id], criterionId);
    return view && { runId: entry.run_id, name: versionName(entry), current: Boolean(entry.current),
      assetVersionId: entry.asset_version_id || view.asset, verdict: view.verdict, value: view.value,
      statusLabel: view.statusLabel, box: view.box };
  }).filter(Boolean);
  return points.length > 1 ? points : [];
}

function humanNode(run, item) {
  let review = null;
  for (const approval of run.approvals || []) {
    for (const entry of approval.human_reviews || []) if (entry.criterion_id === item.criterion_id) review = entry;
  }
  if (review) {
    const label = CODES.verdict[review.verdict] || review.verdict;
    return { edge: '직접 확인', node: { title: `담당자 판단 · ${label}`, sub: review.reason, tone: review.verdict, record: RECORD.approval,
      facts: [['담당자 판단', label], ['직접 본 내용', review.observed], ['확인한 근거', review.reference], ['사유', review.reason]] } };
  }
  const exception = (run.approvals || []).find(approval => approval.decision === 'approve_exception'
    && (approval.criterion_ids || []).includes(item.criterion_id));
  if (exception) {
    return { edge: '예외 승인', node: { title: '예외 승인', sub: exception.reason, record: RECORD.approval,
      facts: [['담당자 결정', '값 불일치 예외 승인'], ['사유', exception.reason]] } };
  }
  if (item.verdict !== 'review') return null;
  const waiting = (run.human_decision || 'unreviewed') === 'unreviewed';
  return { edge: '확인 요청', dashed: true, node: { title: waiting ? '결정 대기' : '확인 기록 없음', sub: '담당자가 직접 확인할 항목', dashed: true,
    record: RECORD.approval, facts: [['상태', waiting ? '담당자 확인을 기다리고 있습니다.' : '이 항목의 담당자 확인 기록이 없습니다.'],
      ['안내', '확인 필요 항목은 보고서·담당자 승인 단계에서 직접 확인해 기록합니다.']] } };
}

export function evidenceGraph(run, criterionId, { image = null, history = null, runs = {}, retrieval = null } = {}) {
  const view = itemView(run, criterionId);
  if (!view) return null;
  const { item } = view;
  const criteria = run.criteria?.criteria || [];
  const criterion = criteria.find(entry => entry.criterion_id === criterionId) || null;
  const label = itemLabel(criterion, item);
  const context = run.context || {};
  const versionId = run.baseline?.criteria_version_id || context.criteria_version_id || null;
  const entry = (history?.entries || []).find(candidate => candidate.current) || null;
  const name = entry ? versionName(entry) : null;
  const hint = view.box ? locationHint(view.box) : null;
  const expected = withUnit(item.expected, item.expected_unit);
  const issues = item.issues || [];
  const reasons = issues.map(reasonText);
  const operator = OPERATORS[item.operator] || null;
  const trail = versionTrail(history, runs, run, criterionId);
  const before = trail[trail.findIndex(point => point.current) - 1] || null;
  const human = humanNode(run, item);
  const backend = retrieval ? (retrieval.backend === 'neo4j' ? 'graph' : 'ledger') : 'record';
  const sized = image?.width > 0 && image?.height > 0;
  const finished = [...(run.step_records || [])].reverse().find(record => record.role === 'A06' && record.finished_at);
  const checkedAt = kstMinute(finished?.finished_at);
  const fileOnly = criterion?.details?.target === 'file_metadata';
  const freeText = run.criteria?.extraction?.input_kind === 'free_text';
  const source = criterion?.source || null;
  const compared = [withUnit(item.comparison_expected ?? item.expected, item.comparison_unit || item.expected_unit),
    withUnit(item.comparison_observed, item.comparison_unit || item.observed_unit) || view.value];
  const humanSummary = human ? human.node.title : null;

  const nodes = [
    { id: 'poster', kind: '포스터', slot: [0, 0], chain: true, title: name ? `${name} 포스터` : '검수 포스터',
      sub: sized ? `${image.width.toLocaleString('ko-KR')} × ${image.height.toLocaleString('ko-KR')} px` : '등록한 이미지',
      graph: view.asset ? { kind: 'poster_version', key: 'poster:' + view.asset } : null,
      facts: [['버전', name || '수정본 이력 확인 전'], ...(sized ? [['크기', `${image.width.toLocaleString('ko-KR')} × ${image.height.toLocaleString('ko-KR')} px`]] : [])] },
    { id: 'run', kind: '검수 실행', slot: [1, 0], title: '검수 실행', sub: checkedAt ? `${checkedAt} 검수` : '자동 검수',
      graph: context.run_id ? { kind: 'run', key: 'run:' + context.run_id } : null,
      facts: [['검수 시각', checkedAt || '기록 없음'], ['전체 자동 판정', CODES.verdict[run.validated?.verdict || run.machine_verdict] || '판정 없음'],
        ['담당자 결정', DECISIONS[run.human_decision] || '기록 확인 필요']] },
    { id: 'criteria', kind: '승인 기준', slot: [2, 0], title: entry?.criteria_version_no ? `승인 기준 v${entry.criteria_version_no}` : '승인된 검수 기준',
      sub: `기준 ${criteria.length || run.validated.items.length}개`,
      graph: versionId ? { kind: 'criteria_version', key: 'criteria:' + versionId } : null,
      facts: [['기준 버전', entry?.criteria_version_no ? `v${entry.criteria_version_no}` : '승인된 버전'],
        ['기준 수', `${criteria.length || run.validated.items.length}개`], ['안내', '이 검수는 승인된 기준 버전에 고정해 판정했습니다.']] },
    { id: 'region', kind: '읽은 위치', slot: [0, 1], chain: true, dashed: !view.box,
      title: !view.box ? '위치 정보 없음' : hint === '전체' ? '포스터 전체' : `포스터 ${hint}`,
      sub: view.box ? '왼쪽 포스터에 상자로 표시' : fileOnly ? '파일 정보로 확인하는 항목' : '검사 모델이 위치를 남기지 않음',
      record: RECORD.inspect,
      facts: view.box ? [['위치', hint === '전체' ? '포스터 전체' : `포스터 ${hint}`],
        ['상자', `왼쪽에서 ${percent(view.box[0])} · 위에서 ${percent(view.box[1])} · 너비 ${percent(view.box[2] - view.box[0])} · 높이 ${percent(view.box[3] - view.box[1])}`],
        ['안내', '상자는 검사 모델이 읽은 위치입니다. 실제 포스터와 맞는지 확인해 주세요.']]
        : [['위치', fileOnly ? '파일 정보로 확인하는 항목이라 포스터 안 위치가 없습니다.' : '이 항목은 포스터 안 위치가 기록되지 않았습니다.']] },
    { id: 'observation', kind: '읽은 값', slot: [1, 1], chain: true, dashed: !view.value,
      title: view.value ? quoted(view.value) : view.statusLabel,
      sub: [view.value && view.status !== 'unknown' ? view.statusLabel : null, hint && (hint === '전체' ? '포스터 전체' : hint)].filter(Boolean).join(' · ')
        || (view.value ? '포스터에서 읽은 값' : '비교할 값이 없음'),
      record: RECORD.inspect,
      facts: [['읽은 값', view.value ? quoted(view.value) : '없음'], ['관측 상태', view.statusLabel],
        ...(!view.value && item.observed?.trim() ? [['검사 메모', item.observed.trim()]] : []),
        ...(hint ? [['위치', hint === '전체' ? '포스터 전체' : `포스터 ${hint}`]] : [])] },
    { id: 'verdict', kind: '판정', slot: [2, 1], chain: true, tone: view.verdict.code, title: view.verdict.label,
      sub: reasonText(reasonCode(issues)) || (view.verdict.code === 'pass' ? '기준과 읽은 값이 맞음' : '사유 기록 없음'),
      record: RECORD.validate,
      facts: [['판정', `${view.verdict.label} · ${item.required ? '필수 조건' : '참고 조건'}`],
        ...(operator ? [['비교 방법', operator]] : []),
        ...(compared[0] || compared[1] ? [['비교한 값', `기준 ${compared[0] ? quoted(compared[0]) : '미정'} ↔ 읽은 값 ${compared[1] ? quoted(compared[1]) : '없음'}`]] : []),
        ['사유', reasons.length ? reasons.join(', ') : view.verdict.code === 'pass' ? '기준과 포스터에서 읽은 값이 맞아 충족으로 판정했습니다.'
          : '판정 사유가 기록되지 않았습니다. 원본과 기준을 직접 확인해 주세요.'],
        ...(humanSummary ? [['담당자', humanSummary]] : [])] },
    { id: 'criterion', kind: '검수 기준', slot: [3, 1], chain: true, title: label, sub: expected ? `기대값 ${quoted(expected)}` : '기대값 미정',
      graph: versionId ? { kind: 'criterion', key: `criterion:${versionId}:${criterionId}` } : null,
      facts: [['기준', criterion?.text || item.criterion_text || label], ['기대값', expected ? quoted(expected) : '아직 정해지지 않음'],
        ...(operator ? [['비교 방법', operator]] : []), ['조건', item.required ? '필수 조건' : '참고 조건 · 전체 판정에서 제외']] },
    { id: 'source', kind: '요구사항 원문', slot: [3, 2], chain: true, dashed: !source,
      title: !source ? '원문 정보 없음' : freeText ? '직접 입력한 요구사항' : `요구사항 문서 ${source.page}쪽`,
      sub: source?.quote || '기준의 원문 인용을 찾지 못함',
      graph: source && versionId ? { kind: 'source', key: `source:${versionId}:${criterionId}:document` } : null,
      facts: source ? [['원문', source.quote],
        ...(criterion.value_source ? [['보완 회신', `${criterion.value_source.quote} · 출처 ${criterion.value_source.reference}`]] : [])]
        : [['원문', '이 기준의 원문 인용이 실행 기록에 없습니다.']] },
  ];
  const edges = [
    { id: 'inspects', from: 'run', to: 'poster', label: '검사', via: 'record', relation: 'inspects' },
    { id: 'applies', from: 'run', to: 'criteria', label: '적용', relation: 'applies',
      via: retrieval?.relations?.run_ids?.includes(context.run_id) ? backend : 'record' },
    { id: 'contains', from: 'criteria', to: 'criterion', label: '포함', via: backend, relation: 'contains' },
    { id: 'region', from: 'poster', to: 'region', label: '영역', via: 'record', chain: true, dashed: !view.box },
    { id: 'reads', from: 'region', to: 'observation', label: '읽음', via: 'record', chain: true, dashed: !view.box },
    { id: 'evidence', from: 'observation', to: 'verdict', label: '근거', via: 'record', chain: true },
    { id: 'judged', from: 'run', to: 'verdict', label: '판정', via: 'record' },
    { id: 'criterion', from: 'verdict', to: 'criterion', label: '기준', via: 'record', chain: true },
    { id: 'source', from: 'criterion', to: 'source', label: '출처', via: backend, relation: 'sourced_from', chain: true, dashed: !source },
  ];
  if (before) {
    const current = trail.find(point => point.current);
    nodes.push({ id: 'previous', kind: '이전 검수', slot: [1, 2], dashed: true, title: `${before.name} · ${before.verdict.label}`,
      sub: before.value ? `읽은 값 ${quoted(before.value)}` : before.statusLabel, runId: before.runId,
      graph: { kind: 'run', key: 'run:' + before.runId },
      facts: [['이전 검수', `${before.name} 포스터`], ['읽은 값', before.value ? quoted(before.value) : before.statusLabel],
        ['판정 변화', before.verdict.code === current.verdict.code ? `${before.verdict.label} 그대로` : `${before.verdict.label} → ${current.verdict.label}`]] });
    edges.push({ id: 'previous', from: 'previous', to: 'observation', label: '재검수', relation: 'reinspected_as', dashed: true,
      via: history.answered_by === 'neo4j' ? 'graph' : 'ledger' });
  }
  if (human) {
    nodes.push({ id: 'human', kind: '담당자 확인', slot: [2, 2], ...human.node });
    edges.push({ id: 'human', from: 'verdict', to: 'human', label: human.edge, via: 'record', dashed: Boolean(human.dashed) });
  }
  const node = id => nodes.find(candidate => candidate.id === id);
  const path = [
    { id: 'poster', text: node('poster').title },
    { id: 'region', text: node('region').title },
    { id: 'observation', text: view.value ? `${quoted(view.value)} 읽음` : view.statusLabel },
    { id: 'verdict', text: view.verdict.label },
    { id: 'criterion', text: expected ? `${label} 기준 ${quoted(expected)}` : `${label} 기준` },
    { id: 'source', text: node('source').title },
  ];
  return {
    criterionId, label, text: criterion?.text || item.criterion_text || label, required: Boolean(item.required), verdict: view.verdict,
    expected, observed: view.value, observation: view.statusLabel, operator, reasons, box: view.box, hint, versionName: name,
    assetVersionId: view.asset, nodes, edges, path, trail,
    lookup: {
      backend: retrieval ? (retrieval.backend === 'neo4j' ? 'neo4j' : 'postgresql') : null,
      requiredRevision: retrieval?.required_revision ?? null, graphRevision: retrieval?.observed_graph_revision ?? null,
      fallback: retrieval?.fallback_reason || null,
      sourceKeys: (retrieval?.source_keys || []).filter(row => row.criterion_id === criterionId).map(row => row.source_key),
      history: trail.length ? (history.answered_by === 'neo4j' ? 'neo4j' : 'postgresql') : null,
    },
  };
}
