// 근거 상자를 그림에 맞춰 다듬는다(화면 표시용).
// 검사 모델이 알려 준 상자는 글자보다 헐겁거나, 글자를 조금 자르거나, 옆으로 어긋나 있을 수 있다.
// 상자 둘레의 그림을 읽어 글자·도형이 있는 줄과 칸을 찾고, 상자가 가리키는 내용에 변을 맞춘다.
//  · 서버 없이 브라우저 안에서만 계산한다(그림을 밖으로 보내지 않는다).
//  · 판정·기록·내보내는 좌표는 바꾸지 않는다. 화면에 그리는 상자만 달라진다.
//  · 자신이 없으면(사진 바탕, 민무늬, 그림 전체를 가리키는 상자, 어느 쪽인지 애매한 변) 원래 자리를 그대로 둔다.
//
// 방법: ① 상자를 짧은 변만큼 넓힌 창에서 이웃 화소와의 색 차이로 경계를 찾는다.
// ② 세로: 상자의 가로 범위 안에서 줄마다 '내용이 차지한 길이'(경계와 경계 사이를 이은 길이)를 재어 내용이 있는 줄 묶음을 찾는다.
//    줄 사이의 빈 줄에서 묶음이 끊긴다. 굵은 글자의 획 안쪽처럼 경계가 없는 자리도 양 끝 경계 사이라 내용으로 센다.
// ③ 가로: 정한 세로 범위 안에서 칸마다 같은 방법으로 내용이 있는 칸 묶음(낱말)을 찾는다. 글자 사이의 좁은 틈은 잇고 낱말 사이는 끊는다.
// ④ 묶음마다 상자 안에 든 비율을 본다. 온전히 들었거나 대부분 든 묶음은 받아들이고(변이 자르고 있었으면 끝까지 넓힌다),
//    살짝 걸친 묶음은 옆 줄·옆 낱말로 보고 버린다(그쪽 변을 내용까지 좁힌다). 그 사이여서 애매하면 그 변은 그대로 둔다.
// ⑤ 가로로 넓히는 것은 확실한 줄(온전히 든 줄)의 내용만 근거로 삼는다. 상자에 걸친 옆 줄이 더 길다고 상자를 넓히지 않는다.
//    좁히는 것은 상자 세로 범위 전체를 본다(걸친 줄의 내용도 자르지 않는다).
//    다만 변에 걸친 묶음이 상자 안의 묶음들과 같은 간격으로 이어지면(자간이 넓은 글의 첫 글자·끝 글자) 같은 줄의 글자로 보고 받아들인다.
// ⑥ 변마다 따로 받아들인다: 원래 변과 거의 같으면 그대로 두고, 너무 멀리 넓어지거나 묶음의 끝이 창 밖이라 보이지 않으면 믿지 않는다.

export const FIT_LIMIT = 960;   // 분석용 그림의 긴 변(px). 더 큰 그림은 호출하는 쪽에서 줄여 넘긴다.

// 조정값. 실제 포스터의 글자 줄 상자(OCR)를 정답으로 놓고 일부러 흐트러뜨린 상자를 되돌리는 측정으로 정했다.
//  pad: 내용 둘레에 두는 틈(줄 높이 대비) · rowLevel/columnLevel: 내용이 있는 줄·칸으로 보는 기준(상자 안의 내용이 많은 자리 대비)
//  letterGap: 한 낱말로 잇는 글자 사이 틈(줄 높이 대비)
//  hold: 이만큼 안쪽의 차이는 원래 변을 그대로 둔다 · grow: 한 변이 바깥으로 넓어질 수 있는 한도(둘 다 상자 짧은 변 대비)
//  상자 안에 온전히 든 내용이 따로 있을 때, 변에 잘린 묶음을 다루는 기준(묶음 가운데 상자 안에 든 비율):
//  take: 이 이상이면 낱말로 보고 끝까지 넓힌다(가로) · rowTake: 세로 기준. 1보다 크면 넓히지 않는다 · drop: 이 이하면 옆 내용으로 보고 버린다
//  joinGap: 변에 걸친 묶음과의 틈이 상자 안 묶음들 사이의 보통 틈의 이 배수 이하면 같은 줄로 본다(가로) · joinGrow: 그때 넓힐 수 있는 한도의 배수
export const FIT_TUNE = Object.freeze({ pad: 0.1, rowLevel: 0.15, columnLevel: 0.15, letterGap: 0.12, hold: 0.12, grow: 0.6, take: 0.7, rowTake: 2, drop: 0.3, joinGap: 1.4, joinGrow: 1.7 });

// 글자 인식(OCR)이 읽은 상자용. 이미 정확한 편이라 뚜렷이 어긋난 변만 움직이고, 넓히는 폭도 줄인다.
export const FIT_GENTLE = Object.freeze({ ...FIT_TUNE, hold: 0.25, grow: 0.5 });

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

// profile에서 level 이상인 자리를 이어 묶음([시작, 끝], 끝 포함)으로 만든다. gap 이하로 떨어진 묶음은 하나로 잇는다.
function runs(profile, level, gap) {
  const found = [];
  for (let index = 0; index < profile.length; index++) {
    if (profile[index] < level) continue;
    const last = found[found.length - 1];
    if (last && index - last[1] - 1 <= gap) last[1] = index;
    else found.push([index, index]);
  }
  return found;
}

// 한 방향(세로 또는 가로)에서 상자의 [from, to) 범위를 묶음에 맞춘다.
// 돌려주는 값: { start, end, core, span } 또는 null(볼 내용이 없다).
//   start·end: 내용이 시작하고 끝나는 자리(여백 없이) 또는 null(그 변은 그대로 둔다)
//   core: 받아들인 묶음 가운데 믿을 만한 것들이 차지한 [시작, 끝](없으면 null) · span: 받아들인 묶음 전체가 차지한 [시작, 끝]
//   joined: [시작 쪽, 끝 쪽] — 변에 걸친 묶음이 상자 안 묶음들과 같은 간격으로 이어져 같은 줄로 받아들였는가
// profile은 창 좌표, from·to도 창 좌표다. openStart·openEnd: 창이 그림 끝이 아니라 내용이 더 이어질 수 있는 쪽.
// joinGap(선택): 0보다 크면 '같은 간격으로 이어지는 묶음'을 같은 줄로 본다(가로에서만 쓴다).
function settle(profile, from, to, gap, openStart, openEnd, share, take, drop, joinGap = 0) {
  const inside = profile.slice(Math.max(0, from), Math.min(profile.length, to));
  if (!inside.length) return null;
  const peak = [...inside].sort((a, b) => a - b)[Math.floor(inside.length * 0.85)];   // 상자 안에서 내용이 많은 자리의 값
  if (peak < 2) return null;
  const level = Math.max(1, Math.round(peak * share));
  const touching = [];
  let whole = false;   // 상자 안에 온전히 든 묶음이 있는가
  for (const run of runs(profile, level, gap)) {
    const length = run[1] - run[0] + 1;
    const overlap = Math.min(to, run[1] + 1) - Math.max(from, run[0]);
    if (overlap <= 0) continue;
    const full = overlap >= length - 1;
    whole ||= full;
    touching.push({ run, full, part: overlap / length });
  }
  // 온전히 든 묶음이 따로 있으면 그것이 상자가 가리키는 내용이다. 변에 잘린 묶음은 옆 줄·옆 낱말일 가능성이 크므로
  //   살짝 걸쳤으면(drop 이하) 버리고, 대부분 들어와 있으면(take 이상) 받아들이고, 그 사이면 그 변을 그대로 둔다.
  // 온전히 든 묶음이 없으면 상자가 찾는 내용을 자르고 있는 것이다 → 절반 넘게 걸친 묶음은 받아들이고 나머지는 버린다.
  // 같은 간격으로 이어지는 묶음: 상자 안에 온전히 든 묶음이 셋 이상이면 그 사이의 보통 틈을 재고,
  // 변에 걸친 첫·끝 묶음이 그 틈만큼만 떨어져 있으면 같은 줄의 글자로 본다(자간이 넓은 글에서 변이 첫 글자를 자른 경우).
  const joined = [false, false];
  if (joinGap > 0 && touching.length >= 4) {
    const gaps = [];
    for (let index = 1; index < touching.length; index++) if (touching[index - 1].full && touching[index].full) gaps.push(touching[index].run[0] - touching[index - 1].run[1] - 1);
    if (gaps.length >= 2) {
      const limit = Math.max(1, gaps.sort((a, b) => a - b)[Math.floor(gaps.length / 2)] * joinGap);
      const head = touching[0], tail = touching[touching.length - 1];
      joined[0] = !head.full && head.run[0] < from && touching[1].run[0] - head.run[1] - 1 <= limit;
      joined[1] = !tail.full && tail.run[1] + 1 > to && tail.run[0] - touching[touching.length - 2].run[1] - 1 <= limit;
    }
  }
  const same = item => (joined[0] && item === touching[0]) || (joined[1] && item === touching[touching.length - 1]);
  const kept = touching.filter(item => same(item) || (whole ? item.full || item.part > drop : item.part >= 0.5));
  if (!kept.length) return null;
  const sure = item => !whole || item.full || item.part >= take || same(item);
  const first = kept[0], last = kept[kept.length - 1];
  // 묶음이 상자 안에서 시작하면 거기까지 좁힌다. 상자 변이 묶음을 자르고 있으면 믿을 만할 때만 묶음 끝까지 넓히고, 애매하면 그대로 둔다.
  // 경계는 내용 바로 바깥 화소에 생기므로 한 칸 안쪽이 실제 내용의 시작이다.
  let start = first.run[0] >= from || sure(first) ? first.run[0] + 1 : null;
  let end = last.run[1] < to || sure(last) ? last.run[1] + 1 : null;
  if (first.run[0] === 0 && openStart) start = null;                    // 끝이 보이지 않는 쪽은 건드리지 않는다
  if (last.run[1] === profile.length - 1 && openEnd) end = null;
  const certain = kept.filter(sure);
  return { start, end, joined, span: [first.run[0] + 1, last.run[1] + 1],
    core: certain.length ? [certain[0].run[0] + 1, certain[certain.length - 1].run[1] + 1] : null };
}

// image: { data: RGBA 바이트 배열, width, height }(canvas의 ImageData와 같은 모양) · box: [x1, y1, x2, y2] (image의 픽셀 단위)
// 돌려주는 값: { box, changed, why }. changed가 false이면 box는 받은 상자 그대로다.
// trace: 진단용. 배열을 넘기면 방향마다 본 값(profile, 상자 범위)을 담아 준다.
export function fitBox(image, box, tune = FIT_TUNE, trace = null) {
  const keep = why => ({ box, changed: false, why });
  if (!image?.data || !(image.width > 0) || !(image.height > 0) || !Array.isArray(box) || box.length !== 4 || !box.every(Number.isFinite)) return keep('invalid');
  const { data, width: W, height: H } = image;
  const x1 = clamp(Math.round(box[0]), 0, W), y1 = clamp(Math.round(box[1]), 0, H);
  const x2 = clamp(Math.round(box[2]), 0, W), y2 = clamp(Math.round(box[3]), 0, H);
  const bw = x2 - x1, bh = y2 - y1;
  if (bw < 6 || bh < 6 || W < 32 || H < 32) return keep('small');
  if (bw * bh > 0.55 * W * H) return keep('whole');   // 그림 대부분을 가리키는 상자는 영역 자체가 근거다

  // ① 살펴볼 창과 경계: 오른쪽·아래 이웃과의 색 차이(세 색 가운데 가장 큰 차이)
  const unit = Math.min(bw, bh);
  const reach = clamp(Math.round(unit * 0.9), 8, Math.round(0.1 * Math.max(W, H)));
  const wx1 = Math.max(0, x1 - reach), wy1 = Math.max(0, y1 - reach), wx2 = Math.min(W, x2 + reach), wy2 = Math.min(H, y2 + reach);
  const ww = wx2 - wx1, wh = wy2 - wy1;
  const strength = new Uint8Array(ww * wh);
  const histogram = new Uint32Array(256);
  let counted = 0;
  for (let y = 0; y < wh; y++) {
    const iy = wy1 + y, row = iy * W, below = Math.min(H - 1, iy + 1) * W;
    for (let x = 0; x < ww; x++) {
      const ix = wx1 + x, here = (row + ix) * 4, right = (row + Math.min(W - 1, ix + 1)) * 4, down = (below + ix) * 4;
      let value = 0;
      for (let channel = 0; channel < 3; channel++) {
        const across = Math.abs(data[right + channel] - data[here + channel]), along = Math.abs(data[down + channel] - data[here + channel]);
        if (across > value) value = across;
        if (along > value) value = along;
      }
      strength[y * ww + x] = value;
      // 문턱값은 원래 상자 안의 경계로 정한다(상자 안에 든 것이 찾는 내용이다)
      if (ix >= x1 && ix < x2 && iy >= y1 && iy < y2) { histogram[value] += 1; counted += 1; }
    }
  }
  let seen = 0, strong = 0;
  for (let value = 255; value >= 0; value--) { seen += histogram[value]; if (seen >= counted * 0.05) { strong = value; break; } }
  if (strong < 30) return keep('flat');   // 상자 안에 뚜렷한 경계가 없다(민무늬·흐린 사진)
  const threshold = Math.max(20, strong * 0.35);

  // 창 좌표의 상자. 세로 → 가로 → 세로 순으로 맞춘다(가로 범위가 바뀌면 줄의 내용도 달라진다).
  let fx1 = x1 - wx1, fy1 = y1 - wy1, fx2 = x2 - wx1, fy2 = y2 - wy1;
  const gx1 = fx1, gy1 = fy1, gx2 = fx2, gy2 = fy2;   // 받은 상자(창 좌표)
  // ②③ 줄·칸마다 내용이 차지한 길이: 경계와 경계 사이가 bridge 이하면 그 사이도 내용으로 센다
  // 경계 표시는 내용 바로 앞 화소(왼쪽·위쪽)에 생기므로, 범위의 시작은 한 칸 앞에서부터 읽는다.
  const bridge = clamp(unit, 4, 96);
  const rows = () => {   // 지금 가로 범위 안에서 줄마다
    const profile = new Uint16Array(wh), left = clamp(fx1 - 1, 0, ww), right = clamp(fx2, 0, ww);
    for (let y = 0; y < wh; y++) {
      let count = 0, last = -1;
      for (let x = left; x < right; x++) {
        if (strength[y * ww + x] < threshold) continue;
        count += last >= 0 && x - last <= bridge ? x - last : 1;
        last = x;
      }
      profile[y] = count;
    }
    return profile;
  };
  const columns = (from, to) => {   // 주어진 세로 범위 안에서 칸마다(세로 범위가 곧 줄이므로 그 안의 경계는 모두 잇는다)
    const profile = new Uint16Array(ww), top = clamp(from - 1, 0, wh), bottom = clamp(to, 0, wh);
    for (let x = 0; x < ww; x++) {
      let count = 0, last = -1;
      for (let y = top; y < bottom; y++) {
        if (strength[y * ww + x] < threshold) continue;
        count += last >= 0 ? y - last : 1;
        last = y;
      }
      profile[x] = count;
    }
    return profile;
  };
  // ⑤ 변마다 따로 받아들인다: 원래 변과 거의 같으면(hold) 그대로 두고, 바깥으로 너무 멀리 가면(grow) 믿지 않는다.
  // 이미 잘 맞는 변은 움직이지 않고, 옆 줄·옆 그림까지 한 덩어리로 읽힌 경우에도 상자가 불어나지 않는다.
  const hold = Math.max(1.5, unit * tune.hold), grow = Math.max(3, unit * tune.grow);
  const lower = (given, proposed, limit = grow) => (Math.abs(proposed - given) < hold || given - proposed > limit ? given : proposed);   // 위·왼쪽 변
  const upper = (given, proposed, limit = grow) => (Math.abs(proposed - given) < hold || proposed - given > limit ? given : proposed);   // 아래·오른쪽 변
  let found = false, core = null;   // core: 확실한 줄이 차지한 세로 범위
  for (const direction of ['rows', 'columns', 'rows']) {
    if (direction === 'rows') {
      // 줄은 얇아서 헐거운 상자에 옆 줄이 대부분 들어오기도 한다 → 온전한 줄이 있으면 세로로는 넓히지 않는다(rowTake)
      const profile = rows();
      trace?.push({ direction, from: fy1, to: fy2, offset: wy1, profile: Array.from(profile) });
      const next = settle(profile, fy1, fy2, unit >= 36 ? 1 : 0, wy1 > 0, wy2 < H, tune.rowLevel, tune.rowTake, tune.drop);
      if (next) {
        // 둘레의 틈은 받은 상자가 아니라 찾은 내용(확실한 줄이 있으면 그 줄)의 높이로 정한다 — 헐거운 상자라고 틈이 커지지 않게
        const basis = next.core || next.span;
        const pad = clamp(Math.round((basis[1] - basis[0]) * tune.pad), 1, 8);
        if (next.start !== null) fy1 = lower(gy1, clamp(next.start - pad, 0, wh));
        if (next.end !== null) fy2 = upper(gy2, clamp(next.end + pad, 0, wh));
        core = next.core && [Math.max(fy1, next.core[0]), Math.min(fy2, next.core[1])];
        if (core && core[1] - core[0] < 3) core = null;
        found = true;
      }
    } else {
      const partly = core && (core[0] > fy1 || core[1] < fy2);   // 상자에 걸친 줄이 따로 있다
      const line = core ? core[1] - core[0] : fy2 - fy1;
      const pad = clamp(Math.round(line * tune.pad), 1, 8);
      const letters = clamp(Math.round(line * tune.letterGap), 1, 14);   // 글자 사이의 좁은 틈은 잇고, 낱말 사이는 끊는다
      const settleColumns = (from, to) => settle(columns(from, to), fx1, fx2, letters, wx1 > 0, wx2 < W, tune.columnLevel, tune.take, tune.drop, tune.joinGap);
      const all = settleColumns(fy1, fy2);
      trace?.push({ direction, from: fx1, to: fx2, offset: wx1, rows: [fy1, fy2], core, all });
      if (all) {
        let left = all.start, right = all.end;
        // 넓히는 쪽은 확실한 줄만으로 다시 본다. 확실한 줄이 넓히라고 하지 않으면 그 변은 그대로 둔다.
        const sure = partly && ((left !== null && left < fx1) || (right !== null && right > fx2)) ? settleColumns(core[0], core[1]) : all;
        if (left !== null && left < fx1) left = sure && sure.start !== null && sure.start < fx1 ? Math.max(left, sure.start) : null;
        if (right !== null && right > fx2) right = sure && sure.end !== null && sure.end > fx2 ? Math.min(right, sure.end) : null;
        // 같은 간격으로 이어지는 글자 하나를 마저 담는 것은 조금 더 멀리까지 허용한다
        if (left !== null) fx1 = lower(gx1, clamp(left - pad, 0, ww), sure?.joined[0] ? grow * tune.joinGrow : grow);
        if (right !== null) fx2 = upper(gx2, clamp(right + pad, 0, ww), sure?.joined[1] ? grow * tune.joinGrow : grow);
        found = true;
      }
    }
    if (fx2 - fx1 < 4 || fy2 - fy1 < 4) return keep('collapsed');
  }
  if (!found) return keep('nothing');
  const nx1 = wx1 + fx1, ny1 = wy1 + fy1, nx2 = wx1 + fx2, ny2 = wy1 + fy2;
  if (nx1 === x1 && ny1 === y1 && nx2 === x2 && ny2 === y2) return keep('same');   // 네 변 모두 이미 맞는다

  // 원래 상자와 너무 달라지면(전혀 다른 곳·터무니없는 크기) 믿지 않는다
  const area = (nx2 - nx1) * (ny2 - ny1), before = bw * bh;
  const overlap = Math.max(0, Math.min(nx2, x2) - Math.max(nx1, x1)) * Math.max(0, Math.min(ny2, y2) - Math.max(ny1, y1));
  if (area > before * 2.6 || area < before * 0.08 || overlap < Math.min(area, before) * 0.5) return keep('far');
  return { box: [nx1, ny1, nx2, ny2], changed: true, why: 'fitted' };
}

// 그림 요소(img)를 분석용 크기로 그려 화소를 읽는다. 다른 출처의 그림이라 읽을 수 없으면 null.
// 돌려주는 값: { image, scale } — image는 fitBox에 넘길 화소, scale은 원본 픽셀 → 분석 픽셀 배율.
export function readPixels(element, limit = FIT_LIMIT) {
  const width = element?.naturalWidth, height = element?.naturalHeight;
  if (!(width > 0) || !(height > 0) || typeof document === 'undefined') return null;
  const scale = Math.min(1, limit / Math.max(width, height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  try {
    const context = canvas.getContext('2d', { willReadFrequently: true });
    context.drawImage(element, 0, 0, canvas.width, canvas.height);
    return { image: context.getImageData(0, 0, canvas.width, canvas.height), scale: canvas.width / width };
  } catch {
    return null;   // 다른 출처의 그림은 화소를 읽을 수 없다. 원래 상자를 그대로 쓴다.
  }
}

// 원본 픽셀 단위의 상자를 다듬어 원본 픽셀 단위로 돌려준다. pixels는 readPixels의 결과.
export function fitBoxOnPixels(pixels, box, tune = FIT_TUNE) {
  if (!pixels) return { box, changed: false, why: 'unreadable' };
  const { image, scale } = pixels;
  const result = fitBox(image, box.map(value => value * scale), tune);
  return result.changed ? { ...result, box: result.box.map(value => value / scale) } : { box, changed: false, why: result.why };
}
