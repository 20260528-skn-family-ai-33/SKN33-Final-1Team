// 영상 로고 확정 화면의 계산: 1~3순위 후보, 같은 로고 연결 후보, 노출 구간.
// 순위 점수(rank)는 원본 로고 SigLIP2 점수에 OCR 가산점(+0.10)을 더한 값으로, 서버 실험의 고정 설정과 같다.
export const TOP_N = 3;
export const LINK_RATIO = 0.92;
export const LIST_STEP = 20;

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
