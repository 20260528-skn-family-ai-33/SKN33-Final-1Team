// 숏폼 검수 화면의 단계·판정 표시 문구와 시간·글자 비교 도우미를 정의한다.
export const stageLabels = {
  queued: '검수 순서를 기다리고 있어요.',
  extracting: '표본 시각마다 프레임을 뽑고 있어요.',
  scene_detection: '장면 경계를 찾고 있어요.',
  ocr: '프레임마다 글자를 읽고 있어요.',
  classifying: '자막과 배경 글자를 나누고 있어요.',
  segmenting: '같은 자막을 트랙과 구간으로 묶고 있어요.',
  review: '확정한 기준과 광고 표시를 판정하고 있어요.',
  logo: '지정한 로고와 프레임을 대조하고 있어요.',
  double_check: '결과를 정리하고 있어요.',
  transcode: '재생용 영상으로 변환하고 있어요.',
  completed: '동영상 검수를 완료했어요.',
  failed: '동영상 검수를 완료하지 못했어요.',
};
export const statusLabels = { pass: '충족', fail: '위반', review: '확인 필요', not_applicable: '대상 아님', skipped: '생략' };

// 서버 처리 단계 순서. 진행 화면의 단계 표시와 돋보기 위치 계산에 쓴다.
export const stageOrder = ['queued', 'extracting', 'scene_detection', 'ocr', 'classifying', 'segmenting', 'review', 'double_check'];
export const stepLabels = {
  queued: '순서 대기', extracting: '프레임 추출', scene_detection: '장면 경계', ocr: '글자 읽기',
  classifying: '자막 분류', segmenting: '트랙·구간', review: '기준 판정', double_check: '결과 정리',
};
// 화면 단계에 없는 서버 단계(로고 대조·재생 변환)는 가까운 단계에 묶는다.
const stageAlias = { logo: 'review', transcode: 'double_check' };

export function stageIndex(stage) { return stageOrder.indexOf(stageAlias[stage] || stage); }

// 단계마다 완료·진행 중·대기를 표시한다. 실패하면 멈춘 단계만 실패로 표시한다.
export function progressSteps(job) {
  const completed = job?.status === 'completed';
  const failed = job?.status === 'failed';
  const current = stageIndex(failed ? job.failed_stage : job?.stage);
  return stageOrder.map((key, index) => ({
    key, label: stepLabels[key],
    status: completed || index < current ? 'completed' : index === current ? (failed ? 'failed' : 'active') : 'pending',
  }));
}

// "프레임 d / t"(글자 읽기)와 "프레임 N장"(장면 경계)에서 서버가 알려 준 프레임 수를 읽는다.
export function frameProgress(detail) {
  const text = typeof detail === 'string' ? detail : '';
  const reading = /프레임\s*(\d+)\s*\/\s*(\d+)/.exec(text);
  if (reading) {
    const done = Number(reading[1]), total = Number(reading[2]);
    return total > 0 ? { done: Math.min(done, total), total } : null;
  }
  const extracted = /프레임\s*(\d+)\s*장/.exec(text);
  return extracted && Number(extracted[1]) > 0 ? { done: 0, total: Number(extracted[1]) } : null;
}

const clamp01 = value => Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));

// 서버 프레임 이름(t + 밀리초 6자리)을 만든다.
export function frameId(ms) { return `t${String(Math.max(0, Math.round(Number(ms) || 0))).padStart(6, '0')}`; }

// 서버 표본 계획(backend/app/video/frames.py plan_times)과 같은 규칙: 1초 격자 + (광고 표시 검사 대상이면) 시작·끝 3초의 0.2초 슬롯.
// 0.2초를 다 채우는 슬롯(t + 0.2초 ≤ 길이)만 뽑으므로 끝이 0.2초보다 짧게 남으면 그 시각은 계획에 없다.
const BASE_MS = 1000, EDGE_MS = 200, EDGE_WINDOW_MS = 3000;
export function planTimes(durationMs, edges = true) {
  const duration = Math.max(0, Math.round(Number(durationMs) || 0));
  const end = Math.max(duration - EDGE_MS + 1, 1);
  const times = new Map();
  for (let t = 0; t < end; t += BASE_MS) times.set(t, 'base');
  if (edges) {
    for (let t = 0; t < Math.min(EDGE_WINDOW_MS, end); t += EDGE_MS) if (!times.has(t)) times.set(t, 'head');
    for (let t = Math.ceil(Math.max(0, duration - EDGE_WINDOW_MS) / EDGE_MS) * EDGE_MS; t < end; t += EDGE_MS) if (!times.has(t)) times.set(t, 'tail');
  }
  return [...times.entries()].sort((a, b) => a[0] - b[0]).map(([time, sampling]) => ({ time_ms: time, sampling, frame_id: frameId(time) }));
}

// 검사 범위 문구. 자체 광고처럼 시작·끝 0.2초 표본을 뽑지 않은 작업에 0.2초 문구를 붙이지 않는다.
export function coverageNote(coverage) {
  if (!coverage) return '';
  const edges = (coverage.inspected_ms || []).some(time => time % BASE_MS !== 0);
  return edges ? coverage.note || '1초 간격 표본 검사 · 시작·끝 3초는 0.2초 간격' : '1초 간격 표본 검사';
}

// 돋보기 연출 상태. 위치는 서버가 알려 준 처리량(읽은 프레임 수·진행률)으로만 정하고 경과 시간으로 추정하지 않는다.
// 서버는 표본 계획의 시간 순서대로 읽으므로, 읽은 장수를 계획에 대응시켜 지금 읽는 프레임의 시각(lensTime)을 구한다.
export function scanState(job) {
  if (!job) return { phase: 'waiting', position: 0, readUpTo: -1, lensTime: null, frames: null, framesReady: false };
  if (job.status === 'completed') return { phase: 'done', position: 1, readUpTo: Infinity, lensTime: null, frames: null, framesReady: true };
  const failed = job.status === 'failed';
  const index = stageIndex(failed ? job.failed_stage : job.stage);
  const ocr = stageOrder.indexOf('ocr');
  const frames = frameProgress(job.stage_detail);
  let position = 0;
  if (index > ocr) position = 1;
  else if (index === ocr) position = frames?.total ? frames.done / frames.total : clamp01(((job.progress ?? 15) - 15) / 73);
  position = clamp01(position);
  const phase = failed ? 'failed' : index <= 0 ? 'waiting' : index < ocr ? 'preparing' : index === ocr ? 'reading'
    : index < stageOrder.indexOf('review') ? 'organizing' : 'judging';
  let readUpTo = index > ocr ? Infinity : -1, lensTime = null;
  const plan = index === ocr && job.media?.duration_ms > 0 ? planTimes(job.media.duration_ms, job.disclosure_input?.kind !== 'self') : [];
  if (plan.length) {
    const done = Math.min(plan.length, Math.round(position * plan.length));
    readUpTo = done > 0 ? plan[done - 1].time_ms : -1;
    lensTime = plan[Math.min(plan.length - 1, done)].time_ms;
  }
  return { phase, position, readUpTo, lensTime, frames, framesReady: index >= stageOrder.indexOf('scene_detection') };
}

// 필름 칸: 영상 비율로 칸 크기를 정하고, 서버가 실제로 뽑는 1초 표본 프레임을 고르게 골라 최대 18칸까지 놓는다.
export function filmCells(media, maxWidth = 720, height = 64, gap = 6) {
  const aspect = media?.width > 0 && media?.height > 0 ? media.width / media.height : 9 / 16;
  const width = Math.round(Math.min(120, Math.max(28, height * aspect)));
  const fit = Math.max(4, Math.min(18, Math.floor((maxWidth + gap) / (width + gap))));
  const seconds = media?.duration_ms > 0 ? Math.ceil(Math.max(media.duration_ms - EDGE_MS + 1, 1) / BASE_MS) : fit;
  const count = Math.min(fit, seconds);
  const cells = Array.from({ length: count }, (_, index) => {
    const second = count === 1 ? 0 : Math.round(index * (seconds - 1) / (count - 1));
    return { index, time_ms: second * 1000, frame_id: frameId(second * 1000) };
  });
  return { width, height, gap, cells };
}

// 결과 목록: 기준별 결과, 영상 전체에서 볼 항목, 광고·협찬 표시를 한 목록에 순서대로 묶는다.
export function judgmentItems(job) {
  const results = job?.review?.results || [];
  const items = [];
  results.filter(result => !result.whole_video).forEach(result => items.push({ key: `criterion:${result.criterion_id}`, kind: 'criterion', group: 'criteria', status: result.status, result }));
  results.filter(result => result.whole_video).forEach(result => items.push({ key: `criterion:${result.criterion_id}`, kind: 'criterion', group: 'whole', status: result.status, result }));
  if (job?.disclosure) items.push({ key: 'disclosure', kind: 'disclosure', group: 'disclosure', status: job.disclosure.status, disclosure: job.disclosure });
  return items;
}

export function filterItems(items, filter) { return filter === 'all' ? items : items.filter(item => item.status === filter); }
export const severityLabels = { block: '차단', warn: '경고', info: '정보' };
export const reviewerLabels = { auto: '자동', manager: '담당자' };
export const roleLabels = { subtitle: '자막', background: '배경', other: '기타' };
export const mismatchLabels = { variants: '프레임마다 다르게 읽힘', spacing: '띄어쓰기 다름', case: '대소문자 다름', one_character: '한 글자 다름' };
export const ruleLabels = {
  R0: '적용 대상', R1: '표시 문구', R2: '제목 앞 15자', R3a: '시작 3초', R3b: '끝 3초', R3c: '중간 반복', R4: '캡션만', R5: '형태·시인성', R6: '가상인물',
};

// 서버 기준 형식(포스터와 같이 정의되지 않은 필드는 거부)에 맞게 화면용 필드(selected 등)를 뺀다.
export function criteriaPayload(criteria) {
  return criteria.map(({ id, text, type, source }) => ({ id, text, type, source }));
}

// 완료·실패면 더 이상 상태를 다시 읽지 않는다.
export function isTerminal(status) { return status === 'completed' || status === 'failed'; }

// 밀리초를 m:ss 형식으로, 0.2초 단위 시각은 m:ss.s로 바꾼다.
export function formatTime(ms) {
  const value = Math.max(0, Number(ms) || 0);
  const seconds = Math.floor(value / 1000);
  const tenth = Math.floor((value % 1000) / 100);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}${tenth ? `.${tenth}` : ''}`;
}

// 기준 문구와 화면 문구를 글자 단위로 맞춰 다른 글자만 표시한다(LCS).
export function diffChars(expected = '', observed = '') {
  const a = Array.from(expected), b = Array.from(observed);
  const table = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i -= 1) for (let j = b.length - 1; j >= 0; j -= 1) {
    table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
  }
  const marks = []; let i = 0, j = 0;
  while (j < b.length) {
    if (i < a.length && a[i] === b[j]) { marks.push([b[j], false]); i += 1; j += 1; }
    else if (i < a.length && table[i + 1][j] >= table[i][j + 1]) i += 1;
    else { marks.push([b[j], true]); j += 1; }
  }
  return marks;
}
