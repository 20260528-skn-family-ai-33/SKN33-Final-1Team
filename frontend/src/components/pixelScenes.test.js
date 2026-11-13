import test from 'node:test';
import assert from 'node:assert/strict';
import { COUNT, SHAPES, MARKS, SCAN, PLAY, SHEET_SIZE, FILM_TRACK, HOLD, MOVE, sceneOrder, loopLength, phaseAt, stillAt } from './pixelScenes.js';

test('세 모양 모두 눈금 520개로 이루어지고 구의 범위 안에 놓인다', () => {
  for (const [name, points] of Object.entries(SHAPES)) {
    assert.equal(points.length, COUNT, name);
    for (const [x, y, z, kind] of points) {
      assert.ok([x, y, z].every(Number.isFinite) && Math.abs(x) <= 1.001 && Math.abs(y) <= 1.001 && Math.abs(z) <= 1.001, `${name} [${x}, ${y}, ${z}]`);
      assert.ok(['tick', 'edge', 'bar', 'word', 'dot', 'dark'].includes(kind), `${name} ${kind}`);
    }
    // 아래에서 위 순서로 정렬해 둔다(같은 번호의 점이 모양 사이를 짧게 옮겨 다니게)
    for (let index = 1; index < points.length; index++) assert.ok(points[index - 1][1] >= points[index][1], `${name} order at ${index}`);
  }
  assert.ok(SHAPES.sphere.every(point => point[3] === 'tick'));
  assert.ok(SHAPES.poster.every(point => point[2] === 0), '포스터는 한 장의 평면');
  assert.equal(new Set(SHAPES.video.map(point => point[2])).size, 3, '영상은 세 장이 깊이로 겹친다');
});

test('근거 상자는 제 모양 안에 있고, 읽는 띠·재생 선이 지나간 뒤에 그려진다', () => {
  for (const mark of MARKS.poster) {
    const [x1, y1, x2, y2] = mark.box;
    assert.ok(x1 < x2 && y1 < y2 && x1 >= -SHEET_SIZE.hw && x2 <= SHEET_SIZE.hw && y1 >= -SHEET_SIZE.hh && y2 <= SHEET_SIZE.hh, mark.id);
    assert.ok(mark.at > SCAN.from && mark.at + 0.85 < HOLD.poster - 0.5, `${mark.id}는 머무는 동안 다 그려진다`);
    // 띠가 상자 아래 변을 지난 뒤에 시작한다
    const scanY = SCAN.top + (SCAN.bottom - SCAN.top) * (mark.at - SCAN.from) / (SCAN.to - SCAN.from);
    assert.ok(scanY >= y2 - 1e-9, mark.id);
    // 상자 안에 굵은 글자나 짙은 그림이 들어 있다(빈 곳을 가리키지 않는다)
    assert.ok(SHAPES.poster.some(([x, y, , kind]) => (kind === 'bar' || kind === 'dark') && x > x1 && x < x2 && y > y1 && y < y2), `${mark.id} 안에 내용`);
  }
  for (const mark of MARKS.video) {
    const [x1, y1, x2, y2] = mark.box;
    assert.ok(mark.at > PLAY.from && mark.at + 0.85 < HOLD.video - 0.5, mark.id);
    assert.ok(SHAPES.video.some(([x, y, z, kind]) => z === mark.z && (kind === 'bar' || kind === 'dark') && x > x1 && x < x2 && y > y1 && y < y2), `${mark.id} 안에 내용`);
  }
  assert.deepEqual(MARKS.poster.map(mark => mark.tone), ['pass', 'review', 'fail']);   // 로그인·샘플 화면의 예시 목록과 같은 판정
  assert.ok(FILM_TRACK.from < FILM_TRACK.to);
});

test('한 바퀴는 머무름과 옮겨 감이 번갈아 이어지고 끝에서 처음으로 돌아온다', () => {
  assert.deepEqual(sceneOrder(), ['sphere', 'poster', 'video']);
  assert.deepEqual(sceneOrder('poster'), ['sphere', 'poster']);
  assert.deepEqual(sceneOrder('video'), ['sphere', 'video']);
  const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} ↔ ${expected}`);
  near(loopLength(), HOLD.sphere + HOLD.poster + HOLD.video + MOVE * 3);
  assert.deepEqual(phaseAt(0), { from: 'sphere', to: 'poster', mix: 0, hold: 0 });
  const halfway = phaseAt(HOLD.sphere + MOVE / 2);
  assert.equal(halfway.from, 'sphere'); assert.equal(halfway.to, 'poster'); near(halfway.mix, 0.5); assert.equal(halfway.hold, HOLD.sphere);
  const inPoster = phaseAt(HOLD.sphere + MOVE + 1);
  assert.equal(inPoster.from, 'poster'); assert.equal(inPoster.to, 'video'); assert.equal(inPoster.mix, 0); near(inPoster.hold, 1);
  const last = phaseAt(loopLength() - MOVE / 4);
  assert.equal(last.from, 'video'); assert.equal(last.to, 'sphere'); near(last.mix, 0.75);
  const again = phaseAt(loopLength() + 1);
  assert.equal(again.from, 'sphere'); assert.equal(again.mix, 0); near(again.hold, 1);
  assert.equal(phaseAt(HOLD.sphere + MOVE + 1, 'video').from, 'video');
  assert.equal(phaseAt(-1).from, 'video', '음수 시각도 한 바퀴 안으로 접는다');
});

test('움직임을 줄인 환경의 한 장은 상자가 모두 그려진 검수 끝 장면이다', () => {
  for (const scene of [undefined, 'poster', 'video']) {
    const phase = phaseAt(stillAt(scene), scene), shape = sceneOrder(scene)[1];
    assert.equal(phase.from, shape); assert.equal(phase.mix, 0);
    assert.ok(MARKS[shape].every(mark => phase.hold - mark.at > 0.85), '상자와 판정 표시가 다 그려진 뒤');
    assert.ok(HOLD[shape] - phase.hold >= 0.5, '옅어지기 전');
  }
});
