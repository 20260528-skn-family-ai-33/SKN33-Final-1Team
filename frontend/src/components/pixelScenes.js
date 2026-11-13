// 점 그림(PixelField)의 장면 자료. 화면 없이 계산만 한다.
// 눈금 520개가 세 가지 모양으로 모인다: 구(모아 둔 자료) → 포스터(읽고 자리를 짚어 판정) → 영상(시점을 따라가며 판정).
// 포스터와 영상은 우리 예시 자료('빛의 기록' 포스터, 예시 숏폼)의 짜임을 본떴고, 근거 상자의 번호·판정 색은 결과 화면과 같다.
//
// 좌표: 구의 반지름이 1인 공간. x는 오른쪽, y는 아래쪽, z는 보는 사람 쪽이 +.
// 점 하나: [x, y, z, 종류]. 종류는 그릴 때의 생김새다(tick 눈금 · edge 테두리 · bar 굵은 글자 · word 글줄 · dot 그림 · dark 짙은 그림).
// 세 모양 모두 아래에서 위 순서로 정렬해 둔다 → 같은 번호의 점이 모양 사이를 짧게 옮겨 다닌다.

export const COUNT = 520;

const line = (list, x1, y1, x2, y2, count, kind, z = 0) => {
  for (let index = 0; index < count; index++) {
    const along = count === 1 ? 0.5 : index / (count - 1);
    list.push([x1 + (x2 - x1) * along, y1 + (y2 - y1) * along, z, kind]);
  }
};
// 가운데 (cx, cy), 반폭 hw·반높이 hh인 네모의 둘레를 따라 고르게
const frame = (list, cx, cy, hw, hh, count, kind, z = 0) => {
  const around = 4 * (hw + hh);
  for (let index = 0; index < count; index++) {
    let at = (index / count) * around;
    let x, y;
    if (at < 2 * hw) { x = -hw + at; y = -hh; }
    else if ((at -= 2 * hw) < 2 * hh) { x = hw; y = -hh + at; }
    else if ((at -= 2 * hh) < 2 * hw) { x = hw - at; y = hh; }
    else { x = -hw; y = hh - (at - 2 * hw); }
    list.push([cx + x, cy + y, z, kind]);
  }
};
const ring = (list, cx, cy, radius, count, kind, z = 0) => {
  for (let index = 0; index < count; index++) {
    const turn = (index / count) * Math.PI * 2;
    list.push([cx + Math.cos(turn) * radius, cy + Math.sin(turn) * radius, z, kind]);
  }
};
// 원 안을 고르게 채운다(황금각 나선)
const disc = (list, cx, cy, radius, count, kind, z = 0) => {
  for (let index = 0; index < count; index++) {
    const reach = radius * Math.sqrt((index + 0.5) / count), turn = index * 2.3999632297;
    list.push([cx + Math.cos(turn) * reach, cy + Math.sin(turn) * reach, z, kind]);
  }
};
const grid = (list, x1, y1, x2, y2, columns, rows, kind, z = 0) => {
  for (let row = 0; row < rows; row++) line(list, x1, y1 + (y2 - y1) * (rows === 1 ? 0.5 : row / (rows - 1)), x2, y1 + (y2 - y1) * (rows === 1 ? 0.5 : row / (rows - 1)), columns, kind, z);
};
const settle = list => {
  if (list.length !== COUNT) throw new Error(`pixel scene needs ${COUNT} points, got ${list.length}`);
  return list.sort((a, b) => b[1] - a[1] || a[0] - b[0]);
};

// ── 구: 황금각으로 고르게 놓은 눈금 ──
function sphere() {
  const list = [];
  for (let index = 0; index < COUNT; index++) {
    const y = 1 - (index / (COUNT - 1)) * 2, around = Math.sqrt(1 - y * y), turn = index * 2.3999632297;
    list.push([Math.cos(turn) * around, y, Math.sin(turn) * around, 'tick']);
  }
  return settle(list);
}

// ── 포스터(3:4): 바깥 종이, 안쪽 테두리, 머리글, 굵은 제목, 소개 글줄, 해와 두 건물, 날짜, 장소, 로고 ──
const SHEET = { hw: 0.645, hh: 0.86 };
function poster() {
  const list = [];
  frame(list, 0, 0, SHEET.hw, SHEET.hh, 96, 'edge');
  line(list, -0.5, -0.7, -0.05, -0.7, 10, 'word');
  grid(list, -0.5, -0.58, 0.18, -0.46, 16, 3, 'bar');
  line(list, -0.5, -0.37, 0.02, -0.37, 12, 'word');
  ring(list, 0.16, 0.02, 0.3, 40, 'dot');
  disc(list, 0.16, 0.02, 0.27, 36, 'dot');
  grid(list, -0.42, -0.1, -0.02, 0.42, 7, 9, 'dot');
  grid(list, -0.06, 0.12, 0.4, 0.42, 8, 5, 'dark');
  line(list, -0.5, 0.46, 0.5, 0.46, 22, 'edge');
  grid(list, -0.5, 0.6, -0.02, 0.655, 12, 2, 'bar');
  line(list, -0.5, 0.74, 0.1, 0.74, 14, 'word');
  ring(list, 0.42, 0.63, 0.075, 14, 'dark');
  frame(list, 0, 0, SHEET.hw - 0.07, SHEET.hh - 0.07, COUNT - list.length, 'edge');   // 남는 점은 안쪽 테두리로
  return settle(list);
}

// ── 영상(16:9 세 장이 깊이 방향으로 겹침): 앞 장에 재생 표시·자막·로고, 아래에 표본 장면과 시간 줄 ──
const FILM = { hw: 0.58, hh: 0.326, frames: [[-0.1, -0.3, -0.34], [0, -0.18, 0], [0.1, -0.06, 0.34]], track: [-0.62, 0.62], trackY: 0.56 };
const SAMPLES = { first: -0.558, step: 0.124, count: 10, y: 0.43, hw: 0.045, hh: 0.032 };
function video() {
  const list = [];
  const [back, middle, front] = FILM.frames;
  frame(list, back[0], back[1], FILM.hw, FILM.hh, 54, 'edge', back[2]);
  grid(list, back[0] - 0.44, back[1] - 0.2, back[0] + 0.44, back[1] + 0.2, 9, 4, 'dot', back[2]);
  frame(list, middle[0], middle[1], FILM.hw, FILM.hh, 58, 'edge', middle[2]);
  grid(list, middle[0] - 0.46, middle[1] - 0.22, middle[0] + 0.46, middle[1] + 0.22, 12, 5, 'dot', middle[2]);
  frame(list, front[0], front[1], FILM.hw, FILM.hh, 76, 'edge', front[2]);
  line(list, front[0] - 0.07, front[1] - 0.13, front[0] - 0.07, front[1] + 0.05, 7, 'dark', front[2]);   // 재생 세모
  line(list, front[0] - 0.07, front[1] - 0.13, front[0] + 0.09, front[1] - 0.04, 7, 'dark', front[2]);
  line(list, front[0] - 0.07, front[1] + 0.05, front[0] + 0.09, front[1] - 0.04, 7, 'dark', front[2]);
  grid(list, front[0] - 0.3, front[1] + 0.2, front[0] + 0.3, front[1] + 0.245, 16, 2, 'bar', front[2]);   // 자막
  frame(list, front[0] + 0.44, front[1] - 0.215, 0.06, 0.06, 12, 'dark', front[2]);                       // 로고
  for (let index = 0; index < SAMPLES.count; index++) frame(list, SAMPLES.first + index * SAMPLES.step, SAMPLES.y, SAMPLES.hw, SAMPLES.hh, 10, 'dot');   // 표본 장면
  line(list, FILM.track[0], FILM.trackY, FILM.track[1], FILM.trackY, 44, 'edge');                         // 시간 줄
  for (let index = 0; index < 9; index++) line(list, FILM.track[0] + index * 0.155, FILM.trackY + 0.025, FILM.track[0] + index * 0.155, FILM.trackY + 0.065, 3, 'edge');
  return settle(list);
}

export const SHAPES = { sphere: sphere(), poster: poster(), video: video() };

// 근거 상자: [x1, y1, x2, y2]와 놓인 깊이 z. 포스터의 번호·판정은 로그인·샘플 화면의 예시 목록과 같다
// (01 전시 제목 충족 · 02 주최 로고 확인 필요 · 03 전시 기간 불충족) → 세 가지 판정 색이 모두 나온다.
// at: 그 모양에 머무는 동안 상자가 그려지기 시작하는 때(초).
export const SCAN = { from: 0.4, to: 3.4, top: -0.9, bottom: 0.9 };   // 포스터를 위에서 아래로 읽는 띠
export const PLAY = { from: 0.5, to: 6 };                              // 영상의 시간 줄을 지나는 재생 선
const scanReaches = y => SCAN.from + (SCAN.to - SCAN.from) * (y - SCAN.top) / (SCAN.bottom - SCAN.top);
const front = FILM.frames[2];
export const MARKS = {
  poster: [
    { id: '01', tone: 'pass', box: [-0.545, -0.635, 0.225, -0.405], z: 0, at: scanReaches(-0.405) },
    { id: '02', tone: 'review', box: [0.315, 0.525, 0.525, 0.735], z: 0, at: scanReaches(0.735) + 0.3 },
    { id: '03', tone: 'fail', box: [-0.545, 0.555, 0.025, 0.7], z: 0, at: scanReaches(0.7) },
  ],
  video: [
    { id: '01', tone: 'pass', box: [front[0] - 0.335, front[1] + 0.165, front[0] + 0.335, front[1] + 0.28], z: front[2], at: PLAY.from + (PLAY.to - PLAY.from) * 0.3 },
    { id: '02', tone: 'review', box: [front[0] + 0.345, front[1] - 0.31, front[0] + 0.535, front[1] - 0.12], z: front[2], at: PLAY.from + (PLAY.to - PLAY.from) * 0.62 },
  ],
};
export const SHEET_SIZE = SHEET;
export const FILM_TRACK = { from: FILM.track[0], to: FILM.track[1], y: FILM.trackY };
export const FILM_FRAMES = { hw: FILM.hw, hh: FILM.hh, centers: FILM.frames };   // 뒤 → 앞 순서
// 시간 줄 위의 표본 장면(작은 네모 열 개)의 자리. 재생 선이 지나는 장면을 짚는 데 쓴다.
export const FILM_SAMPLES = SAMPLES;

// 한 바퀴의 시간표(초): 모양마다 머무는 시간과, 다음 모양으로 옮겨 가는 시간
export const HOLD = { sphere: 5.5, poster: 8, video: 7 };
export const MOVE = 2.2;
const ORDER = { poster: ['sphere', 'poster'], video: ['sphere', 'video'] };
const BOTH = ['sphere', 'poster', 'video'];

export function sceneOrder(scene) { return ORDER[scene] || BOTH; }
export function loopLength(scene) { return sceneOrder(scene).reduce((sum, shape) => sum + HOLD[shape] + MOVE, 0); }

// seconds 시점의 상태: { from, to, mix, hold }
//  머무는 중이면 mix는 0, hold는 그 모양에 머문 시간(초). 옮겨 가는 중이면 mix가 0→1, hold는 from 모양의 머무는 시간 전체.
export function phaseAt(seconds, scene) {
  const order = sceneOrder(scene), total = loopLength(scene);
  let rest = ((seconds % total) + total) % total;
  for (let index = 0; index < order.length; index++) {
    const from = order[index], to = order[(index + 1) % order.length];
    if (rest < HOLD[from]) return { from, to, mix: 0, hold: rest };
    rest -= HOLD[from];
    if (rest < MOVE) return { from, to, mix: rest / MOVE, hold: HOLD[from] };
    rest -= MOVE;
  }
  return { from: order[0], to: order[1], mix: 0, hold: 0 };
}

// 움직임을 줄인 환경에서 보여 줄 한 장: 검수가 끝난 모습(상자가 모두 그려진 뒤)
export function stillAt(scene) {
  const order = sceneOrder(scene), shape = order[1];
  let seconds = 0;
  for (const name of order) { if (name === shape) break; seconds += HOLD[name] + MOVE; }
  return seconds + HOLD[shape] - 1.2;
}
