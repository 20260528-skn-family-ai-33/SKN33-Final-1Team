// 동영상 검수 한 항목의 판정 근거를 관계도(노드·관계)로 구성한다. 포스터의 evidenceGraph.js와 같은 모양을 돌려주어
// 같은 판정 근거 창(components/EvidenceDialogParts.jsx)으로 그린다. 완료된 검수 결과만 읽고 판정을 새로 만들지 않는다.
// 자리(slot)는 [열, 행]이다. 근거 사슬(chain)은 영상 → 읽은 위치(프레임) → 읽은 문구 → 판정 → 검수 기준 → 기준 출처이고,
// 광고·협찬 표시는 고른 규정 하나에 대해 영상 → 프레임 → 읽은 문구(또는 입력한 정보) → 판정 → 규정 → 근거 지침이다.
import { kstMinute } from '../review/reportBrief.js';
import { coverageNote, formatTime, mismatchLabels, reviewerLabels, ruleLabels, severityLabels, statusLabels } from './videoFormat.js';

const TYPES = { information: '정보 검수', visual: '시각 검수' };
const DECISIONS = { unreviewed: '검토 대기', changes_approved: '수정 요청 승인', final_approved: '최종 승인', exception_approved: '예외 승인' };
const KINDS = { endorsement: '추천·보증 광고', partial: '일부 구간 추천·보증', self: '광고주 자체 광고', unknown: '모름' };
const INPUTS = { R0: ['kind', '영상 성격'], R2: ['title', '제목'], R4: ['caption', '캡션'] };   // 화면이 아니라 입력한 정보로 보는 규정
const INTERNAL = new Set(['R2', 'R3a', 'R3b', 'R3c']);   // 숫자 기준이 지침이 아니라 내부 기준인 규정
const RECORD = { read: '검수 결과 기록 · 글자 읽기 단계', judge: '검수 결과 기록 · 기준 판정 단계', disclosure: '검수 결과 기록 · 광고 표시 판정 단계',
  input: '검수를 시작할 때 입력한 정보', approval: '담당자 승인 기록' };
const GUIDELINE = ['추천·보증 심사지침', '공정거래위원회 예규 제499호', '추천·보증 등에 관한 표시·광고 심사지침'];
const INTERNAL_NOTE = '‘3초’·‘15자’·‘중간 한 번 이상’은 내부 기준이며 법령의 숫자 기준이 아닙니다.';

const quoted = value => `‘${value}’`;
const verdictOf = status => ({ code: status || 'review', label: statusLabels[status] || '확인 필요' });
const clamp01 = value => Math.min(1, Math.max(0, value));
const located = entry => Boolean(entry?.frame_id) && Number.isFinite(entry.time_ms);

// 프레임 픽셀 좌표 [x1, y1, x2, y2]를 0~1 비율로 바꾼다. 크기를 모르거나 넓이가 없으면 상자를 그리지 않는다.
function ratioBox(bbox, width, height) {
  if (!Array.isArray(bbox) || bbox.length !== 4 || !bbox.every(Number.isFinite) || !(width > 0) || !(height > 0)) return null;
  const box = [clamp01(bbox[0] / width), clamp01(bbox[1] / height), clamp01(bbox[2] / width), clamp01(bbox[3] / height)];
  return box[0] < box[2] && box[1] < box[3] ? box : null;
}

// 화면에 놓인 자리를 말로 옮긴다(위·가운데·아래 × 왼쪽·가운데·오른쪽).
function placeHint(box) {
  const x = (box[0] + box[2]) / 2, y = (box[1] + box[3]) / 2;
  const row = y < 1 / 3 ? '위' : y > 2 / 3 ? '아래' : '가운데', column = x < 1 / 3 ? '왼쪽' : x > 2 / 3 ? '오른쪽' : '가운데';
  return row === '가운데' && column === '가운데' ? '가운데' : column === '가운데' ? row : `${row} ${column}`;
}

// 근거 한 곳(프레임·시각·상자·문구). raw는 결과 화면의 프레임 무대를 같은 곳으로 옮길 때 그대로 넘긴다.
function target(entry, job, frames, label) {
  const frame = frames.get(entry.frame_id);
  const width = entry.width || frame?.width || job.media?.width, height = entry.height || frame?.height || job.media?.height;
  const box = ratioBox(entry.bbox_xyxy, width, height);
  return {
    id: `${entry.frame_id}:${(entry.bbox_xyxy || []).join(',')}:${entry.time_ms}`, frameId: entry.frame_id, timeMs: entry.time_ms,
    range: Number.isFinite(entry.start_ms) && Number.isFinite(entry.end_ms) ? [entry.start_ms, entry.end_ms] : null,
    text: entry.text || '', box, hint: box ? placeHint(box) : null, ratio: width > 0 && height > 0 ? width / height : null,
    raw: label ? { ...entry, label } : entry,
  };
}

// 위치 근거가 없는 항목에서 보여 줄 장면: 1초 표본 가운데 장면 하나.
function stillFrame(job) {
  const frames = job.frames || [];
  const base = frames.filter(frame => frame.sampling === 'base');
  const frame = (base.length ? base : frames)[Math.floor(((base.length ? base : frames).length - 1) / 2)];
  return frame ? { frameId: frame.frame_id, timeMs: frame.time_ms, ratio: frame.width > 0 && frame.height > 0 ? frame.width / frame.height : null } : null;
}

function videoNode(job) {
  const media = job.media;
  const size = media?.width > 0 && media?.height > 0 ? `${media.width}×${media.height}` : null;
  const length = media?.duration_ms > 0 ? formatTime(media.duration_ms) : null;
  const note = coverageNote(job.coverage);
  return { id: 'video', kind: '영상', slot: [0, 0], chain: true, title: '검수 영상', sub: [length, size].filter(Boolean).join(' · ') || '등록한 영상',
    record: '등록한 영상 파일', facts: [['길이', length || '확인 전'], ...(size ? [['해상도', size]] : []), ...(note ? [['검사 범위', note]] : [])] };
}

function runNode(job) {
  const checkedAt = kstMinute(job.created_at);
  const decision = job.workflow?.human_decision;
  return { id: 'run', kind: '검수 실행', slot: [1, 0], title: '검수 실행', sub: checkedAt ? `${checkedAt} 검수` : '자동 검수', record: '검수 결과 기록',
    facts: [['검수 시각', checkedAt || '기록 없음'], ['판정 범위', '검사한 표본 시각 기준'], ...(decision ? [['담당자 결정', DECISIONS[decision] || '기록 확인 필요']] : [])] };
}

function frameNode(shown, emptySub) {
  if (!shown) {
    return { id: 'frame', kind: '읽은 위치', slot: [0, 1], chain: true, dashed: true, title: '위치 정보 없음', sub: emptySub, record: RECORD.read,
      facts: [['위치', emptySub]] };
  }
  const time = formatTime(shown.timeMs);
  return { id: 'frame', kind: '읽은 위치', slot: [0, 1], chain: true, title: `${time} 프레임`,
    sub: shown.box ? `화면 ${shown.hint} · 왼쪽에 상자로 표시` : '상자 없이 프레임만 기록', record: RECORD.read,
    facts: [['시각', time], ...(shown.range ? [['보인 구간', `${formatTime(shown.range[0])}~${formatTime(shown.range[1])}`]] : []),
      ...(shown.box ? [['위치', `화면 ${shown.hint}`]] : []), ['안내', '상자는 OCR이 글자를 읽은 위치입니다. 원본 영상과 맞는지 확인해 주세요.']] };
}

function humanNode(job, status) {
  if (status !== 'review') return null;
  const decision = job.workflow?.human_decision;
  const decided = decision && decision !== 'unreviewed';
  return { id: 'human', kind: '담당자 확인', slot: [2, 2], dashed: !decided, title: decided ? DECISIONS[decision] || '담당자 결정' : '결정 대기',
    sub: '담당자가 직접 확인할 항목', record: RECORD.approval,
    facts: [['상태', decided ? `담당자 결정: ${DECISIONS[decision] || '기록 확인 필요'}` : '담당자 확인을 기다리고 있습니다.'],
      ['안내', '확인 필요 항목은 원본 영상을 보고 담당자가 판단합니다.']] };
}

function finish(base, nodes, edges, pathIds, pathText = {}) {
  const used = new Set(nodes.map(node => node.id));
  const byId = Object.fromEntries(nodes.map(node => [node.id, node]));
  return { ...base, nodes, edges: edges.filter(edge => used.has(edge.from) && used.has(edge.to)),
    path: pathIds.filter(id => used.has(id)).map(id => ({ id, text: pathText[id] || byId[id].title })) };
}

function criterionEvidence(job, item, shownIndex) {
  const result = item.result;
  const frames = new Map((job.frames || []).map(frame => [frame.frame_id, frame]));
  const evidence = (result.evidence_segments || []).filter(located).map(entry => target(entry, job, frames));
  const shown = evidence[Math.min(Math.max(0, shownIndex), evidence.length - 1)] || null;
  const verdict = verdictOf(result.status);
  const observed = (result.observed_text || shown?.text || '').trim();
  const reviewer = reviewerLabels[result.reviewer] ? `${reviewerLabels[result.reviewer]} 판정` : null;
  const severity = severityLabels[result.severity] || null;
  const type = TYPES[result.type] || null;
  const mismatch = result.mismatch || [];
  const criteria = job.criteria || [];
  const source = criteria.find(entry => entry.id === result.criterion_id)?.source;
  const quote = source && source !== 'user' && source !== result.criterion ? source : null;
  const human = humanNode(job, result.status);
  const reason = result.reason || '';
  const nodes = [
    videoNode(job), runNode(job),
    { id: 'criteria', kind: '확정 기준', slot: [2, 0], title: job.criteria_version ? `검수 기준 v${job.criteria_version}` : '확정한 검수 기준',
      sub: `기준 ${criteria.length || job.review?.results?.length || 1}개`, record: '검수를 시작할 때 확정한 기준',
      facts: [['기준 수', `${criteria.length || job.review?.results?.length || 1}개`], ['안내', '이 검수는 시작할 때 확정한 기준으로 판정했습니다.']] },
    frameNode(shown, result.whole_video ? '영상 전체에서 확인할 항목' : 'OCR이 위치를 남기지 않음'),
    { id: 'observation', kind: '읽은 문구', slot: [1, 1], chain: true, dashed: !observed, title: observed ? quoted(observed) : '읽은 문구 없음',
      sub: observed ? (shown ? `${formatTime(shown.timeMs)} 프레임에서 읽음` : '프레임에서 읽은 문구') : '비교할 문구가 없음', record: RECORD.read,
      facts: [['읽은 문구', observed ? quoted(observed) : '없음'], ...(shown?.text && shown.text.trim() !== observed ? [['이 프레임', quoted(shown.text.trim())]] : []),
        ...(evidence.length ? [['근거 구간', `${evidence.length}곳`]] : [])] },
    { id: 'verdict', kind: '판정', slot: [2, 1], chain: true, tone: verdict.code, title: verdict.label,
      sub: [reviewer, severity].filter(Boolean).join(' · ') || '자동 판정', record: RECORD.judge,
      facts: [['판정', [verdict.label, severity].filter(Boolean).join(' · ')], ...(reviewer ? [['판정 방식', reviewer]] : []),
        ['사유', reason || '판정 사유가 기록되지 않았습니다. 원본 영상과 기준을 직접 확인해 주세요.']] },
    { id: 'criterion', kind: '검수 기준', slot: [3, 1], chain: true, title: result.criterion, sub: type || '검수 기준',
      record: '검수를 시작할 때 확정한 기준', facts: [['기준', result.criterion], ...(type ? [['검수 방식', type]] : []),
        ...(result.whole_video ? [['확인 범위', '영상 전체에서 확인할 항목']] : [])] },
    { id: 'source', kind: '기준 출처', slot: [3, 2], chain: true, dashed: !source, title: !source ? '출처 정보 없음' : quote ? '기준 문서 원문' : '직접 입력한 기준',
      sub: quote || (source ? '검수 전에 확정한 문구' : '기준의 출처가 기록되지 않음'), record: '검수를 시작할 때 확정한 기준',
      facts: [['출처', quote || (source ? '담당자가 직접 입력해 확정한 기준입니다.' : '이 기준의 출처가 결과 기록에 없습니다.')]] },
  ];
  if (mismatch.length) {
    const first = mismatch[0];
    const kind = mismatchLabels[first.kind] || '불일치';
    nodes.push({ id: 'mismatch', kind: '불일치', slot: [1, 2], dashed: true, title: mismatch.length > 1 ? `${kind} 외 ${mismatch.length - 1}건` : kind,
      sub: first.expected ? `기준 ${quoted(first.expected)}` : `대표 ${quoted(first.observed)}`, record: RECORD.judge,
      facts: [...mismatch.map((entry, index) => [`불일치 ${index + 1}`, `${mismatchLabels[entry.kind] || '불일치'} · ${entry.expected
        ? `기준 ${quoted(entry.expected)} ↔ 화면 ${quoted(entry.observed)}` : `대표 ${quoted(entry.observed)}`}`]),
        ['안내', 'OCR이 잘못 읽었을 수도 있으니 원본을 확인해 주세요.']] });
  }
  if (human) nodes.push(human);
  const edges = [
    { id: 'inspects', from: 'run', to: 'video', label: '검사' },
    { id: 'applies', from: 'run', to: 'criteria', label: '적용' },
    { id: 'contains', from: 'criteria', to: 'criterion', label: '포함' },
    { id: 'frame', from: 'video', to: 'frame', label: '프레임', chain: true, dashed: !shown },
    { id: 'reads', from: 'frame', to: 'observation', label: '읽음', chain: true, dashed: !shown },
    { id: 'evidence', from: 'observation', to: 'verdict', label: '근거', chain: true },
    { id: 'judged', from: 'run', to: 'verdict', label: '판정' },
    { id: 'criterion', from: 'verdict', to: 'criterion', label: '기준', chain: true },
    { id: 'source', from: 'criterion', to: 'source', label: '출처', chain: true, dashed: !source },
    { id: 'mismatch', from: 'mismatch', to: 'observation', label: '대조', dashed: true },
    { id: 'human', from: 'verdict', to: 'human', label: '확인 요청', dashed: Boolean(human?.dashed) },
  ];
  return finish({
    key: item.key, kind: 'criterion', label: result.criterion, text: result.criterion, verdict, tone: verdict.code, tag: [type, reviewer].filter(Boolean).join(' · '),
    compare: [['읽은 문구', observed ? quoted(observed) : '기록 없음'], ...(type ? [['검수 방식', type]] : []), ...(severity ? [['중요도', severity]] : [])],
    reason, notes: result.whole_video ? ['영상 전체에서 확인할 항목입니다.'] : [], mismatch, quote, evidence, shown, still: shown ? null : stillFrame(job),
    rules: [], rule: null,
  }, nodes, edges, ['video', 'frame', 'observation', 'verdict', 'criterion', 'source'],
  { observation: observed ? `${quoted(observed)} 읽음` : '읽은 문구 없음', criterion: '검수 기준' });
}

// 먼저 볼 규정: 전체 판정과 같은 상태인 첫 규정(위반 → 확인 필요 순), 없으면 첫 규정.
function leadRule(disclosure) {
  const entries = Object.entries(disclosure.rules || {});
  const pick = status => entries.find(([, rule]) => rule.status === status)?.[0];
  return pick('fail') || (disclosure.status !== 'pass' && pick('review')) || entries[0]?.[0] || null;
}

function disclosureEvidence(job, item, ruleKey, shownIndex) {
  const disclosure = item.disclosure;
  const entries = Object.entries(disclosure.rules || {});
  const key = entries.some(([name]) => name === ruleKey) ? ruleKey : leadRule(disclosure);
  const rule = key ? disclosure.rules[key] : null;
  const frames = new Map((job.frames || []).map(frame => [frame.frame_id, frame]));
  const evidence = (rule?.evidence || []).filter(located).map(entry => target(entry, job, frames, ruleLabels[key] || '표시 규정'));
  const shown = evidence[Math.min(Math.max(0, shownIndex), evidence.length - 1)] || null;
  const verdict = verdictOf(disclosure.status);
  const ruleVerdict = verdictOf(rule ? rule.status : disclosure.status);
  const ruleName = key ? ruleLabels[key] || '표시 규정' : '적용 대상';
  const info = job.disclosure_input || {};
  const input = key ? INPUTS[key] : ['kind', '영상 성격'];
  const inputValue = input && (input[0] === 'kind' ? KINDS[info.kind] : (info[input[0]] || '').trim());
  const human = humanNode(job, ruleVerdict.code);
  const reviewer = reviewerLabels[disclosure.reviewer] ? `${reviewerLabels[disclosure.reviewer]} 판정` : null;
  const internal = INTERNAL.has(key);
  const observation = input
    ? { id: 'observation', kind: '입력한 정보', slot: [1, 1], chain: true, dashed: !inputValue, title: input[1],
      sub: inputValue ? (input[0] === 'kind' ? inputValue : quoted(inputValue)) : '입력하지 않음', record: RECORD.input,
      facts: [[input[1], inputValue || '입력하지 않았습니다.']] }
    : { id: 'observation', kind: '읽은 문구', slot: [1, 1], chain: true, dashed: !shown?.text, title: shown?.text ? quoted(shown.text) : '읽은 문구 없음',
      sub: shown ? `${formatTime(shown.timeMs)} 프레임에서 읽음` : evidence.length ? '프레임에서 읽은 문구' : '이 규정에 연결된 화면 문구가 없음', record: RECORD.read,
      facts: [['읽은 문구', shown?.text ? quoted(shown.text) : '없음'], ...(evidence.length ? [['근거 위치', `${evidence.length}곳`]] : [])] };
  const nodes = [
    videoNode(job), runNode(job),
    ...(entries.length ? [{ id: 'policy', kind: '표시 규정', slot: [2, 0], title: '광고·협찬 표시', sub: `규정 ${entries.length}개 확인 · ${verdict.label}`,
      record: RECORD.disclosure, facts: [['전체 판정', verdict.label], ...(disclosure.applicable && disclosure.text_time_status
        ? [['표시 문구·시간', `${statusLabels[disclosure.text_time_status] || '확인 필요'} (표본 기준)`]] : []),
      ['사유', disclosure.reason || '제공된 판정 이유가 없습니다.']] }] : []),
    frameNode(shown, input ? '입력한 정보로 확인하는 규정' : rule && !evidence.length && ruleVerdict.code === 'review' ? '담당자가 직접 확인하는 규정' : '화면 위치가 기록되지 않음'),
    observation,
    { id: 'verdict', kind: '판정', slot: [2, 1], chain: true, tone: ruleVerdict.code, title: ruleVerdict.label,
      sub: rule ? `${ruleName} 규정` : reviewer || '자동 판정', record: RECORD.disclosure,
      facts: [['판정', ruleVerdict.label], ...(rule?.note ? [['확인 내용', rule.note]] : []), ...(!rule ? [['사유', disclosure.reason || '제공된 판정 이유가 없습니다.']] : []),
        ['전체 판정', `광고·협찬 표시 ${verdict.label}`]] },
    { id: 'rule', kind: '규정', slot: [3, 1], chain: true, title: ruleName, sub: rule?.note || (rule ? '표시 규정' : disclosure.reason || '광고·협찬 표시 대상 여부'),
      record: RECORD.disclosure, facts: [['규정', ruleName], ...(rule?.note ? [['확인 내용', rule.note]] : []), ...(internal ? [['안내', INTERNAL_NOTE]] : [])] },
    { id: 'guideline', kind: '근거 지침', slot: [3, 2], chain: true, title: GUIDELINE[0], sub: GUIDELINE[1], record: '판정 규정이 따르는 지침',
      facts: [['지침', GUIDELINE[2]], ['고시', GUIDELINE[1]], ...(internal ? [['안내', INTERNAL_NOTE]] : [])] },
  ];
  if (human) nodes.push(human);
  const edges = [
    { id: 'inspects', from: 'run', to: 'video', label: '검사' },
    { id: 'applies', from: 'run', to: 'policy', label: '적용' },
    { id: 'contains', from: 'policy', to: 'rule', label: '포함' },
    { id: 'frame', from: 'video', to: 'frame', label: '프레임', chain: true, dashed: !shown },
    { id: 'reads', from: 'frame', to: 'observation', label: input ? '입력' : '읽음', chain: true, dashed: !shown },
    { id: 'evidence', from: 'observation', to: 'verdict', label: '근거', chain: true },
    { id: 'judged', from: 'run', to: 'verdict', label: '판정' },
    { id: 'rule', from: 'verdict', to: 'rule', label: '규정', chain: true },
    { id: 'guideline', from: 'rule', to: 'guideline', label: '지침', chain: true },
    { id: 'human', from: 'verdict', to: 'human', label: '확인 요청', dashed: Boolean(human?.dashed) },
  ];
  return finish({
    key: item.key, kind: 'disclosure', label: '광고·협찬 표시', text: '광고·협찬 표시', verdict, tone: ruleVerdict.code, tag: reviewer || '',
    compare: [...(disclosure.applicable && disclosure.text_time_status ? [['표시 문구·시간', `${statusLabels[disclosure.text_time_status] || '확인 필요'} (표본 기준)`]] : []),
      ...(severityLabels[disclosure.severity] ? [['중요도', severityLabels[disclosure.severity]]] : []), ...(KINDS[info.kind] ? [['영상 성격', KINDS[info.kind]]] : [])],
    reason: disclosure.reason || '', notes: disclosure.notes || [], mismatch: [], quote: null, evidence, shown, still: shown ? null : stillFrame(job),
    rules: entries.map(([name, entry]) => ({ key: name, label: ruleLabels[name] || '표시 규정', verdict: verdictOf(entry.status), note: entry.note || '' })), rule: key,
  }, nodes, edges, ['video', 'frame', 'observation', 'verdict', 'rule', 'guideline'],
  { observation: input ? `${input[1]} ${inputValue ? '확인' : '없음'}` : shown?.text ? `${quoted(shown.text)} 읽음` : '읽은 문구 없음',
    rule: `${ruleName} 규정` });
}

// item: videoFormat.judgmentItems(job)의 한 항목. rule: 광고·협찬 표시에서 볼 규정, shown: 왼쪽에 보여 줄 근거 순서(0부터).
export function videoEvidence(job, item, { rule = null, shown = 0 } = {}) {
  if (!job || !item) return null;
  if (item.kind === 'criterion' && item.result) return criterionEvidence(job, item, shown);
  if (item.kind === 'disclosure' && item.disclosure) return disclosureEvidence(job, item, rule, shown);
  return null;
}
