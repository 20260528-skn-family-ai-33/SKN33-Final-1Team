import test from 'node:test';
import assert from 'node:assert/strict';
import { fitBox, fitBoxOnPixels, FIT_TUNE, FIT_GENTLE } from './evidenceBoxFit.js';

// 밝은 민무늬 그림
function paper(width = 320, height = 240, color = [250, 250, 248]) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let index = 0; index < width * height; index++) data.set([...color, 255], index * 4);
  return { data, width, height };
}

// 글자 줄 흉내: 어두운 덩어리(글자)를 좁은 틈으로 이어 그린다. 돌려주는 값: 줄이 차지한 상자 [x1, y1, x2, y2]
function line(image, x, y, { count = 8, letter = 12, tall = 24, gap = 2, color = [24, 24, 24] } = {}) {
  for (let index = 0; index < count; index++) {
    const left = x + index * (letter + gap);
    for (let py = y; py < y + tall; py++) for (let px = left; px < left + letter; px++) image.data.set([...color, 255], (py * image.width + px) * 4);
  }
  return [x, y, x + count * letter + (count - 1) * gap, y + tall];
}

const contains = (outer, inner) => outer[0] <= inner[0] && outer[1] <= inner[1] && outer[2] >= inner[2] && outer[3] >= inner[3];
function assertNear(actual, expected, slack, message) {
  actual.forEach((value, index) => assert.ok(Math.abs(value - expected[index]) <= slack, `${message}: [${actual}] ↔ [${expected}]`));
}

test('헐거운 상자는 글자 줄에 맞게 좁힌다', () => {
  const image = paper();
  const words = line(image, 60, 80);
  const result = fitBox(image, [44, 66, 190, 122]);
  assert.equal(result.changed, true);
  assert.ok(contains(result.box, words), `글자를 자르지 않는다: [${result.box}]`);
  assertNear(result.box, words, 4, '글자 줄 둘레에 작은 틈만 남긴다');
});

test('글자를 자른 상자는 글자 끝까지 넓힌다', () => {
  const image = paper();
  const words = line(image, 60, 80);
  const result = fitBox(image, [64, 85, 166, 99]);
  assert.equal(result.changed, true);
  assert.ok(contains(result.box, words), `글자를 모두 담는다: [${result.box}]`);
  assertNear(result.box, words, 4, '글자 줄 둘레에 작은 틈만 남긴다');
});

test('이미 맞는 상자는 건드리지 않는다', () => {
  const image = paper();
  const words = line(image, 60, 80);
  const box = [words[0] - 1, words[1] - 1, words[2] + 1, words[3] + 1];
  const result = fitBox(image, box);
  assert.equal(result.changed, false);
  assert.equal(result.box, box);
});

test('살짝 걸친 옆 줄은 빼고, 온전히 든 줄에 맞춘다', () => {
  const image = paper();
  const first = line(image, 60, 80);
  line(image, 60, 116);   // 아랫줄: 상자에 6px만 걸친다
  const result = fitBox(image, [48, 70, 184, 122]);
  assert.equal(result.changed, true);
  assert.ok(contains(result.box, first));
  assert.ok(result.box[3] <= first[3] + 4, `아랫줄을 담지 않는다: [${result.box}]`);
});

test('어느 쪽인지 애매하게 걸친 옆 줄이 있으면 그 변은 그대로 둔다', () => {
  const image = paper();
  line(image, 60, 80);
  line(image, 60, 116);   // 아랫줄: 상자에 절반 넘게 걸친다(같이 가리킨 것일 수도, 옆 줄일 수도 있다)
  const result = fitBox(image, [48, 66, 184, 131]);
  assert.equal(result.box[3], 131, '아래 변은 받은 자리 그대로');
  assert.ok(result.box[1] >= 76 && result.box[1] <= 80, `위쪽 빈 곳은 좁힌다: [${result.box}]`);
});

test('온전한 줄이 있으면 걸친 옆 줄 쪽으로 넓히지 않는다', () => {
  const image = paper();
  const first = line(image, 60, 80, { count: 5 });
  line(image, 60, 116, { count: 14 });   // 아랫줄이 훨씬 길다
  const result = fitBox(image, [48, 70, 150, 131]);
  assert.ok(result.box[2] <= 150, `긴 아랫줄을 따라 오른쪽으로 넓히지 않는다: [${result.box}]`);
  assert.ok(result.box[0] >= first[0] - 4 && result.box[0] <= first[0], `왼쪽 빈 곳은 좁힌다: [${result.box}]`);
});

test('민무늬·그림 전체·잘못된 입력은 받은 상자를 그대로 돌려준다', () => {
  const image = paper();
  assert.equal(fitBox(image, [40, 40, 200, 120]).why, 'flat');
  line(image, 60, 80);
  assert.equal(fitBox(image, [4, 4, 316, 236]).why, 'whole');
  assert.equal(fitBox(image, [60, 80, 62, 82]).why, 'small');
  assert.equal(fitBox(image, [60, 80, Number.NaN, 120]).why, 'invalid');
  assert.equal(fitBox(null, [60, 80, 120, 120]).why, 'invalid');
  const box = [44, 66, 190, 122];
  assert.deepEqual(fitBoxOnPixels(null, box), { box, changed: false, why: 'unreadable' });
});

test('줄여 읽은 그림에서도 원본 픽셀 단위로 돌려준다', () => {
  const image = paper();
  const words = line(image, 60, 80);
  const result = fitBoxOnPixels({ image, scale: 0.5 }, [88, 132, 380, 244]);   // 원본은 분석 그림의 2배
  assert.equal(result.changed, true);
  assertNear(result.box, words.map(value => value * 2), 8, '원본 픽셀 단위');
});

test('글자 인식 상자용 조정값은 더 조심스럽다', () => {
  assert.ok(FIT_GENTLE.hold > FIT_TUNE.hold && FIT_GENTLE.grow < FIT_TUNE.grow);
  const image = paper();
  const words = line(image, 60, 80);
  const box = [words[0] - 9, words[1] - 9, words[2] + 9, words[3] + 9];   // 9px 여유: 모델 상자면 다듬고, 글자 인식 상자면 둔다
  assert.equal(fitBox(image, box, FIT_TUNE).changed, true);
  assert.equal(fitBox(image, box, FIT_GENTLE).changed, false);
});
