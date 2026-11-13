// 샘플 체험(/sample)의 예시 자료와 미리 만든 결과. 서버로 아무것도 보내지 않고, 결과는 모두 예시다.
// 포스터 좌표는 300×400, 동영상 좌표는 320×180 기준이다(SampleArt.jsx의 그림과 같은 좌표계).

export const MEDIA_LABELS = { poster: '포스터', video: '동영상' };
export const VIDEO = { seconds: 30, width: 1920, height: 1080, name: '빛의기록_숏폼.mp4', size: '18.4 MiB' };
export const POSTER_SIZE = '1200×1600';

// 동영상 초마다 보이는 화면 글자.
export const CAPTIONS = [
  { from: 0, to: 2, kind: 'sub', text: '당신은 지금 어디에 있나요?' },
  { from: 2, to: 5, kind: 'title', text: '빛의 기록' },
  { from: 5, to: 8, kind: 'ad', text: '유료 광고 포함' },
  { from: 12, to: 18, kind: 'sub', text: '11.20 – 11.28 · 시립미술관' },
];

// 포스터는 기간을 일부러 11.29로 잘못 넣어 불일치 예시를 보여 준다.
export const RESULTS = {
  poster: {
    text: [
      { id: 't1', req: '전시 제목에 ‘빛의 기록’을 표기해 주세요.', verdict: 'pass', box: [24, 160, 196, 202], observed: '빛의 기록', why: '요구한 제목과 포스터 문구가 같습니다.' },
      { id: 't2', req: '전시 기간은 ‘11.20–11.28’로 표기해 주세요.', verdict: 'fail', box: [24, 282, 160, 304], observed: '11.20 – 11.29', why: '끝나는 날짜가 11.29로 표기되어 요구한 11.28과 다릅니다.' },
      { id: 't3', req: '전시 장소(시립미술관)를 표기해 주세요.', verdict: 'pass', box: [24, 310, 170, 326], observed: '시립미술관 2층 기획전시실', why: '요구한 장소가 포함되어 있습니다.' },
    ],
    logo: { id: 'l1', req: '하단에 주최 원본 로고를 사용해 주세요.', verdict: 'review', box: [206, 364, 274, 388], observed: '하단 오른쪽에서 원본 로고와 닮은 위치 1곳', why: '모양은 원본과 비슷하지만 오른쪽 글자 영역 색이 원본보다 옅습니다. 담당자가 확인해 주세요.' },
  },
  video: {
    text: [
      { id: 't1', req: '예고편에서 ‘빛의 기록’ 제목이 보여야 합니다.', verdict: 'pass', box: [92, 66, 228, 104], at: 2, span: [2, 5], observed: '빛의 기록', why: '0:02–0:05 표본 프레임에서 제목이 읽혔습니다.' },
      { id: 't2', req: '영상 시작 3초 안에 ‘유료 광고 포함’이 보여야 합니다.', verdict: 'fail', box: [10, 10, 88, 28], at: 5, span: [5, 8], observed: '유료 광고 포함 (처음 보인 시점 0:05)', why: '문구는 있지만 처음 보인 시점이 0:05로, 요구한 시작 3초 안에 들지 않습니다.' },
      { id: 't3', req: '전시 기간 ‘11.20–11.28’을 자막으로 보여 주세요.', verdict: 'pass', box: [60, 146, 260, 166], at: 12, span: [12, 18], observed: '11.20 – 11.28 · 시립미술관', why: '0:12–0:18 자막에서 요구한 기간이 읽혔습니다.' },
    ],
    logo: { id: 'l1', req: '예고편 마지막 구간에 원본 주최 로고를 표시합니다.', verdict: 'review', box: [250, 140, 308, 168], at: 27, span: [26, 30], observed: '0:26–0:30 오른쪽 아래에서 원본 로고와 닮은 위치', why: '모양은 원본과 비슷하지만 4초 동안 크기가 작아 선명도가 낮습니다. 담당자가 확인해 주세요.' },
  },
};

export const VERDICT_LABELS = { poster: { pass: '적합', fail: '부적합', review: '확인 필요' }, video: { pass: '충족', fail: '위반', review: '확인 필요' } };
export const REVIEW_STEPS = ['자료 입력', '검수 진행', '결과 확인', '보고서'];
export const LOGO_STEPS = ['자료 입력', '후보 찾기', '위치 확정', '정밀 대조'];

// 진행 화면 연출. 실제 화면의 단계 이름을 따른다(포스터 frameworkProgress.js, 동영상 videoFormat.js).
export const POSTER_PHASES = [
  { label: '순서 대기', title: '포스터 검수 순서를 기다리고 있어요.', until: 3 },
  { label: '문구·시각 조건 검사', title: '포스터의 문구와 시각 조건을 확인하고 있어요.', until: 11 },
  { label: '근거 확인', title: '검사 결과와 원문 근거를 확인하고 있어요.', until: 16 },
  { label: '결과 정리', title: '항목별 검수 결과를 정리하고 있어요.', until: 21 },
];
export const VIDEO_PHASES = [
  { label: '순서 대기', title: '검수 순서를 기다리고 있어요.', until: 2 },
  { label: '프레임 추출', title: '표본 시각마다 프레임을 뽑고 있어요.', until: 4 },
  { label: '장면 경계', title: '장면 경계를 찾고 있어요.', until: 5 },
  { label: '글자 읽기', title: '프레임마다 글자를 읽고 있어요.', until: 13 },
  { label: '자막 분류', title: '자막과 배경 글자를 나누고 있어요.', until: 14 },
  { label: '트랙·구간', title: '같은 자막을 트랙과 구간으로 묶고 있어요.', until: 16 },
  { label: '기준 판정', title: '확정한 기준과 광고 표시를 판정하고 있어요.', until: 18 },
  { label: '결과 정리', title: '결과를 정리하고 있어요.', until: 21 },
];
// 로고 검수는 후보 찾기 작업까지가 진행 화면이다(위치 확정·정밀 대조는 다음 단계).
export const LOGO_PHASES = {
  poster: [
    { label: '순서 대기', title: '로고 검수 순서를 기다리고 있어요.', until: 3 },
    { label: '원본 로고 준비', title: '원본 로고의 모양과 색을 살펴보고 있어요.', until: 5 },
    { label: '후보 찾기', title: '포스터에서 원본 로고와 닮은 위치를 찾고 있어요.', until: 17 },
    { label: '후보 정리', title: '찾은 후보를 위치 확정용으로 정리하고 있어요.', until: 21 },
  ],
  video: [
    { label: '순서 대기', title: '로고 검수 순서를 기다리고 있어요.', until: 2 },
    { label: '프레임 추출', title: '표본 시각마다 프레임을 뽑고 있어요.', until: 5 },
    { label: '후보 찾기', title: '프레임마다 원본 로고와 닮은 위치를 찾고 있어요.', until: 17 },
    { label: '후보 묶기', title: '같은 위치의 후보를 시간 구간으로 묶고 있어요.', until: 21 },
  ],
};
export const TOTAL_TICKS = 22;
export const TICK_MS = 280;
const FILM_FRAMES = 31;

export const formatSeconds = seconds => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;

// 고른 검수 유형에 해당하는 예시 항목(문구 항목들 + 로고 항목).
export function sampleItems(media, mode) {
  const result = RESULTS[media];
  if (!result) return [];
  return [...(mode !== 'logo' ? result.text : []), ...(mode !== 'text' ? [result.logo] : [])];
}

// 결과 화면을 열 때 먼저 펼쳐 둘 항목: 처음 나오는 부적합·확인 필요 항목.
export function firstIssue(media, mode) {
  return sampleItems(media, mode).find(item => item.verdict !== 'pass')?.id || null;
}

// 로고 후보 찾기 문구. 일반 자료는 OpenAI 위치 탐지, 보안 자료는 RunPod 후보 탐지로 표시한다.
export function logoDetail(part, media, security) {
  if (part >= 1) return media === 'video' ? '원본 로고 1개 · 후보 묶음 3개를 찾았어요.' : '원본 로고 1개 · 후보 4개를 찾았어요.';
  const percent = Math.round(part * 100);
  if (media === 'video') {
    const second = Math.min(VIDEO.seconds, Math.ceil(part * VIDEO.seconds));
    return security === 'general' ? `OpenAI 위치 탐지 ${second}/${VIDEO.seconds}초` : `후보 탐지 ${second}/${VIDEO.seconds}초`;
  }
  return security === 'general' ? `OpenAI로 원본 로고 위치를 찾는 중 · ${percent}%` : `후보 탐지 타일 ${Math.min(23, Math.ceil(part * 23))}/23 · ${percent}%`;
}

// tick(0~TOTAL_TICKS)에 맞는 진행 화면 값. 화면을 훑는 단계(동영상 글자 읽기·로고 후보 찾기)는 단계 안에서도 진행분을 올린다.
export function runningView(tick, { media, mode, security }) {
  const logoOnly = mode === 'logo';
  const video = media === 'video';
  const phases = logoOnly ? LOGO_PHASES[media] : video ? VIDEO_PHASES : POSTER_PHASES;
  const active = phases.findIndex(phase => tick < phase.until);
  const done = active === -1 ? phases.length : active;
  const scanAt = logoOnly ? 2 : video ? 3 : -1;
  const scanFrom = scanAt > 0 ? phases[scanAt - 1].until : 0;
  const scanPart = scanAt < 0 ? 0 : done > scanAt ? 1 : active === scanAt ? (tick - scanFrom) / (phases[scanAt].until - scanFrom) : 0;
  const completedRoles = Math.max(0, done - 1);
  const percent = !logoOnly && !video ? Math.round(completedRoles / 3 * 100)
    : Math.min(100, Math.round((done + (active === scanAt ? scanPart : 0)) / phases.length * 100));
  const message = logoOnly
    ? active !== -1 && active < scanAt ? `원본 로고 1개 · ${video ? `영상 ${formatSeconds(VIDEO.seconds)}` : `포스터 ${POSTER_SIZE}`}` : logoDetail(scanPart, media, security)
    : video ? `영상 ${formatSeconds(VIDEO.seconds)} · ${VIDEO.width}×${VIDEO.height}` : `${completedRoles} / 3개 처리 단계 완료`;
  const frames = Math.round(scanPart * FILM_FRAMES);
  const filmCaption = active === scanAt ? `프레임 ${frames} / ${FILM_FRAMES} ${logoOnly ? '탐지' : '읽음'}`
    : done > scanAt ? `표본 프레임 ${logoOnly ? '탐지' : '읽기'} 완료` : active >= 1 ? `표본 프레임 ${FILM_FRAMES}장 준비` : '표본 프레임 준비 전';
  const speed = video && !logoOnly && (active >= 4 || active === -1)
    ? `장당 ${security === 'general' ? '1.4초 · openai' : '0.8초 · vllm'}` : '글자 읽기 후 표시';
  const logoPart = Math.min(1, tick / 18);
  return {
    percent, message, filmCaption, speed, scanPart,
    title: (phases[active] || phases[phases.length - 1]).title,
    steps: phases.map((phase, index) => ({ label: phase.label,
      status: active === -1 || index < active ? 'completed' : index === active ? 'active' : 'pending' })),
    logo: { percent: Math.round(logoPart * 100), state: logoPart >= 1 ? '위치 확정 대기' : '후보 찾는 중', detail: logoDetail(logoPart, media, security) },
  };
}
