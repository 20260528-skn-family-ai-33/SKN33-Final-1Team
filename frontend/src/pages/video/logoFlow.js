// 영상 로고 확정 화면의 계산: 1~3순위 후보, 같은 로고 연결 후보, 노출 구간.
// 순위 점수(rank)는 원본 로고 SigLIP2 점수에 OCR 가산점(+0.10)을 더한 값으로, 서버 실험의 고정 설정과 같다.
export const TOP_N = 3;
export const LINK_RATIO = 0.92;
export const LIST_STEP = 20;

// 동영상 로고 검수 단계: 후보 찾기가 끝나면 서버가 순위 높은 후보로 정밀 대조까지 바로 한다(backend/app/video/logos/routes.py
// auto_selection). 위치 직접 확인은 대조 결과를 본 뒤 검수하려던 로고가 아닐 때만 고르는 단계라 기본 단계에 넣지 않는다.
export const videoLogoSteps = { input: '자료 입력', candidates: '후보 찾기', compare: '정밀 대조' };

// 정밀 대조 결과 표시: 차이 근거가 있으면 보류, 없으면 통과. 로고가 작거나 읽히지 않아 비교하지 못한 초는 따로 둔다.
export const OUTCOME = {
  match: { label: '통과', tone: 'match' },
  diff: { label: '보류', tone: 'diff' },
  unknown: { label: '비교 정보 부족', tone: 'unknown' },
};

// 묶음 전체 결과: 한 초라도 차이가 있으면 보류, 차이 없이 통과한 초가 있으면 통과, 모두 비교하지 못했으면 비교 정보 부족.
export function bundleOutcome(outcomes = {}) {
  if (outcomes.diff > 0) return 'diff';
  if (outcomes.match > 0) return 'match';
  return 'unknown';
}

// 차이 종류. 서버(backend/app/video/logos/compare.py)는 초마다 종류(kinds)와 문장(findings)을 따로 내므로,
// 문장이 어느 종류인지는 서버가 쓰는 문장 모양으로 나눈다. 모양이 바뀌어 못 나누면 '기타'로 둔다.
export const DIFF_KINDS = ['빠짐', '추가', '가림·변형', '배치', '색', '기타'];
export function diffKind(text = '') {
  if (text.includes('모양이 원본과 다름')) return '가림·변형';
  if (text.includes('원본에 없는')) return '추가';
  if (text.startsWith('색이 원본과 다름')) return '색';
  if (text.includes('위치가 원본과 다름') || text.includes('크기 비율 다름') || text.includes('외곽 비율이 원본과 다름')) return '배치';
  if (/ 없음(\(|$)/.test(text)) return '빠짐';
  return '기타';
}

// 서버 차이 문장(backend/app/video/logos/compare.py)을 담당자가 읽기 쉬운 문장으로 바꾼다(화면 표시용).
// 서버 문장은 그대로 기록·집계에 쓰고, 화면에서만 다듬는다. 비율·일치도·색상 각도 같은 수치는 보이지 않는다(원문은 title로 남긴다).
// 모르는 모양은 원문을 그대로 보인다. place: 대조한 쪽 이름('영상' | '포스터'). 포스터 서버 문장은 '영상'을 '포스터'로 바꿔 보낸다.
export function findingText(text = '', place = '영상') {
  let m;
  if ((m = /^원본 문구 '(.+)' 없음\(같은 크기 원본 OCR 기준\)$/.exec(text))) return `원본의 문구 '${m[1]}'가 ${place} 속 로고에서 읽히지 않아요.`;
  if ((m = /^원본에 없는 문구 '(.+)'\(같은 크기 원본 OCR 기준\)$/.exec(text))) return `원본에 없는 문구 '${m[1]}'가 ${place} 속 로고에 보여요.`;
  if ((m = /^원본에 없는 문구 '(.+)'$/.exec(text))) return `원본에 없는 문구 '${m[1]}'가 보여요.`;
  if ((m = /^(주|보조) 문구 '(.+)' 없음$/.exec(text))) return `${m[1]} 문구 '${m[2]}'가 보이지 않아요.`;
  if (/^원본에 없는 요소/.test(text)) return '원본에 없는 요소가 로고 안에 보여요.';
  if ((m = /^(.+) 위치가 원본과 다름$/.exec(text))) return `${m[1]}의 위치가 원본과 달라요.`;
  if ((m = /^(.+) 모양이 원본과 다름\(F1 [\d.]+\)$/.exec(text))) return `${m[1]}의 모양이 원본과 달라요.`;
  if (/^심볼:문구 크기 비율 다름/.test(text)) return '심볼과 문구의 크기 비율이 원본과 달라요.';
  if ((m = /^로고 외곽 비율이 원본과 다름\(가로\/세로 원본 ([\d.]+), (?:영상|포스터) ([\d.]+)\)$/.exec(text)))
    return `로고의 가로·세로 비율이 달라요. ${place} 속 로고가 원본보다 ${Number(m[2]) < Number(m[1]) ? '세로로 길쭉해요' : '가로로 넓어요'}.`;
  if (/^색이 원본과 다름/.test(text)) return '색이 원본과 달라요.';
  if ((m = /^(.+) 없음$/.exec(text))) return `${m[1]}이(가) 보이지 않아요.`;
  return text;
}

// 초별 결과(per_sec)에서 차이만 종류별로 모은다: [{ kind, secs(이 종류가 나온 초 수), items: [[문장, 초 수], ...] }]
export function groupDifferences(perSec = []) {
  const kinds = new Map();
  for (const row of perSec) {
    if (row.outcome !== 'diff') continue;
    for (const text of row.findings || []) {
      const kind = diffKind(text);
      if (!kinds.has(kind)) kinds.set(kind, { secs: new Set(), items: new Map() });
      const entry = kinds.get(kind);
      entry.secs.add(row.sec);
      entry.items.set(text, (entry.items.get(text) || 0) + 1);
    }
  }
  return DIFF_KINDS.filter(kind => kinds.has(kind)).map(kind => ({
    kind, secs: kinds.get(kind).secs.size,
    items: [...kinds.get(kind).items].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])),
  }));
}

// 서버가 자동으로 고른 묶음(auto_selection: [{logo, decision, groups}])을 확정 화면의 선택 상태로 바꾼다.
// 사람이 위치를 고치러 들어가면 자동 대조에 쓴 묶음이 미리 선택돼 있다.
export function autoDrafts(selection, groups) {
  const drafts = {};
  for (const pick of Array.isArray(selection) ? selection : []) {
    const ids = (pick.groups || []).filter(index => groups[index]);
    const decision = pick.decision === 'done' && ids.length ? 'done' : 'none';
    drafts[pick.logo] = { decision, confirmed: decision === 'done' ? ids : [], checked: decision === 'done' ? ids : [], manuals: [],
      sec: decision === 'done' ? groups[ids[0]].rep[pick.logo][0] : 0 };
  }
  return drafts;
}

// 서버 auto_selection(backend/app/video/logos/routes.py)과 같은 규칙: 원본 로고마다 순위 1위 묶음과, 그 점수의
// LINK_RATIO 이상인 다른 묶음을 점수 순으로 최대 TOP_N개 고른다. 후보가 없으면 decision='none'이다.
// 자동 정밀 대조가 없는 서버가 만든 작업(정밀 대조 기록 없음)을 화면이 같은 규칙으로 대조할 때 쓴다.
export function autoSelection(groups, refCount) {
  return Array.from({ length: refCount }, (_, logo) => {
    const order = groups.map((_, i) => i).sort((a, b) => groups[b].rank[logo] - groups[a].rank[logo]);
    if (!order.length) return { logo, decision: 'none', groups: [] };
    const top = groups[order[0]].rank[logo];
    const floor = top > 0 ? LINK_RATIO * top : Infinity;
    const chosen = [order[0], ...order.slice(1).filter(i => groups[i].rank[logo] >= floor)].slice(0, TOP_N);
    return { logo, decision: 'done', groups: chosen };
  });
}

// 자동으로 고른 묶음을 POST /compare 본문으로 바꾼다(위치 확인 화면이 보내는 값과 같은 모양).
export function autoCompareBody(video, selection = autoSelection(video.groups, video.refs.length)) {
  return selection.filter(pick => video.refs[pick.logo]).map(pick => {
    const answer = handoff({ video: video.key, ref: pick.logo, refName: video.refs[pick.logo].name,
      decision: pick.decision === 'done' && pick.groups.length ? 'done' : 'none', groups: video.groups, checked: new Set(pick.groups), manuals: [] });
    // 서버 auto_answers처럼 반올림 뒤 넓이가 없는 영역은 빼고, 남는 영역이 없으면 '대조하지 않음'으로 보낸다.
    const crops = answer.crops.filter(crop => crop.bbox[0] < crop.bbox[2] && crop.bbox[1] < crop.bbox[3]);
    return { ...answer, crops, decision: crops.length ? answer.decision : 'none' };
  });
}

// 원본 로고 기준 순위 상위 n개 그룹 번호
export function topGroups(groups, ref, n = TOP_N) {
  return groups.map((g, i) => i).sort((a, b) => groups[b].rank[ref] - groups[a].rank[ref]).slice(0, n);
}

// 확정 그룹(여러 개면 그중 가장 낮은 점수)의 LINK_RATIO배 이상인 다른 그룹(같은 로고의 다른 구간 후보), 점수 순
export function linkCandidates(groups, ref, confirmed) {
  if (!confirmed.length) return [];
  const floor = LINK_RATIO * Math.min(...confirmed.map(i => groups[i].rank[ref]));
  return groups.map((g, i) => i).filter(i => !confirmed.includes(i) && groups[i].rank[ref] >= floor)
    .sort((a, b) => groups[b].rank[ref] - groups[a].rank[ref]);
}

// 초 목록을 이어지는 구간으로 묶는다: [3,4,5,9] -> [[3,5],[9,9]]
export function toIntervals(secs) {
  const sorted = [...new Set(secs)].sort((a, b) => a - b); const out = [];
  for (const s of sorted) {
    const last = out[out.length - 1];
    if (last && s === last[1] + 1) last[1] = s; else out.push([s, s]);
  }
  return out;
}

export function intervalText([a, b]) { return a === b ? `${a}초` : `${a}~${b}초`; }

// 2부 검수로 넘기는 값: 선택한 후보(확정 후보 + 추가로 고른 연결 후보)마다 crop 하나, 직접 지정마다 사용자가 자른 영역 하나
// logo는 원본 로고 번호(업로드 순서)로, 파일 이름이 같은 로고가 있어도 구분한다.
export function handoff({ video, ref, refName, decision, groups, checked, manuals }) {
  if (decision === 'none') return { video, logo: ref, reference: refName, decision, crops: [], groups: [], intervals: [], total_seconds: 0 };
  const ids = [...checked];
  // 직접 지정은 사용자가 정한 시간 구간(secs)이 노출 구간이다.
  const secs = ids.flatMap(i => groups[i].secs).concat(manuals.flatMap(m => m.secs));
  const crops = ids.map(gi => ({ group: gi, sec: groups[gi].rep[ref][0], bbox: groups[gi].rep[ref][1].map(v => Math.round(v)), source: 'candidate' }))
    .concat(manuals.map(m => ({ sec: m.sec, bbox: m.box.map(v => Math.round(v)), source: 'manual', intervals: toIntervals(m.secs) })));
  return { video, logo: ref, reference: refName, decision, crops, groups: ids, intervals: toIntervals(secs), total_seconds: new Set(secs).size };
}
