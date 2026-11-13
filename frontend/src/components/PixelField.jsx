import React, { useEffect, useRef } from 'react';
import { COUNT, SHAPES, MARKS, SCAN, PLAY, SHEET_SIZE, FILM_TRACK, FILM_FRAMES, FILM_SAMPLES, HOLD, phaseAt, stillAt } from './pixelScenes.js';

// 첫 화면 배경의 점 그림. 작은 눈금 520개가 구로 돌다가 포스터와 영상으로 모이고, 그 위에서 검수가 일어난다:
// 점들이 자리를 잡으면 그 아래로 종이(포스터)와 겹친 화면(영상)이 옅게 떠오르고,
// 읽는 띠가 지나가면 근거 상자가 그려지고 번호와 판정 표시가 붙는다(결과 화면의 상자·판정 색과 같다).
//   구(모아 둔 자료) → 포스터(위에서 아래로 읽고 자리를 짚는다) → 영상(시간 줄을 따라가며 짚는다) → 다시 구
// scene(선택): 'poster'면 구↔포스터, 'video'면 구↔영상만 돈다. 없으면 셋을 모두 돈다.
// quiet(선택): 글자·타일 뒤에 깔리는 자리에서 쓴다. 종이 면과 판정 색 없이 눈금과 가는 선만 그려 내용을 가리지 않는다.
// 화면 밖이거나 탭이 가려지면 멈추고, 움직임을 줄인 환경에서는 검수가 끝난 한 장만 그린다. 장식이라 낭독기에는 숨긴다.
// 참고한 연출: uiverse.io의 스캔 카드(SRIMANKS, 지나가는 띠와 선이 그려지는 모션)·레이더 무늬(ommanav_2060, 지나간 자리의 잔광),
// dribbble의 문서 스캔·항목 인식 화면(읽은 칸에 상자와 꼬리표가 붙는 연출). 코드는 가져오지 않고 우리 눈금과 판정 색으로 새로 그렸다.

const TONES = { pass: '23,125,69', review: '128,106,40', fail: '163,79,62' };
const PAPER = '#fbfaf7';
const STAGGER = 0.4;   // 옮겨 갈 때 위쪽 점이 먼저 떠나는 정도
const clamp = (value, low = 0, high = 1) => Math.min(high, Math.max(low, value));
const ease = value => (value < 0.5 ? 4 * value * value * value : 1 - Math.pow(-2 * value + 2, 3) / 2);
const pop = value => { const back = value - 1; return 1 + 2.4 * back * back * back + 1.4 * back * back; };   // 살짝 넘쳤다 돌아오는 등장

// 모양마다의 자세 [좌우로 돈 각, 앞뒤로 누운 각]: 구는 계속 돌고, 종이와 영상은 비스듬히 선 채 천천히 흔들린다
const POSE = {
  sphere: seconds => [seconds * 0.075, 0],
  poster: seconds => [-0.42 + 0.16 * Math.sin(seconds * 0.5), 0.14],
  video: seconds => [0.36 + 0.14 * Math.sin(seconds * 0.5 + 1), 0.12],
};
// 종류별 생김새 [가로, 세로, 진하기, 십자 눈금]. tick(구의 눈금)만 깊이에 따라 달라진다.
function look(kind, depth) {
  if (kind === 'tick') { const size = depth > 0.25 ? 3 : 1.5; return [size, size * (depth > 0.5 ? 3 : 1), 0.08 + (depth + 1) * 0.1, depth > 0.45 ? 1 : 0]; }
  const near = 1 + depth * 0.5;
  if (kind === 'bar') return [4.4, 4.4, 0.5 * near, 0];
  if (kind === 'word') return [6.5, 1.8, 0.38 * near, 0];
  if (kind === 'dark') return [2.8, 2.8, 0.46 * near, 0];
  if (kind === 'dot') return [2.2, 2.2, 0.33 * near, 0];
  return [2, 2, 0.3 * near, 0];
}

export function PixelField({ monochrome = false, scene, quiet = false }) {
  const canvas = useRef(null);

  useEffect(() => {
    const node = canvas.current;
    const context = node.getContext('2d');
    if (!context) return undefined;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const ink = monochrome ? '75,75,75' : '70,75,65';
    let frame = 0;
    let clock = HOLD.sphere - 2.4;   // 처음 들어오면 구를 잠깐 보여 주고 곧 모이기 시작한다
    let drawnAt = 0;
    let onScreen = true;
    let width = 0;
    let height = 0;

    function draw(seconds) {
      context.clearRect(0, 0, width, height);
      const radius = Math.min(width, height) * 0.43;
      const unit = clamp(radius / 260, 0.72, 1.3);
      const phase = phaseAt(seconds, scene);
      const moving = phase.mix > 0;
      // 자세: 한 점을 화면 좌표와 깊이로 옮긴다
      const turn = name => { const [yaw, pitch] = POSE[name](seconds); return [Math.cos(yaw), Math.sin(yaw), Math.cos(pitch), Math.sin(pitch)]; };
      const place = (x0, y0, z0, [cosY, sinY, cosP, sinP]) => {
        const x = x0 * cosY + z0 * sinY, z = z0 * cosY - x0 * sinY;
        return [x, y0 * cosP - z * sinP, y0 * sinP + z * cosP];
      };
      const toScreen = (x, y, depth) => { const scale = 1 + depth * 0.18; return [width / 2 + x * radius * scale, height / 2 + y * radius * scale]; };
      const poseFrom = turn(phase.from), poseTo = moving ? turn(phase.to) : null;
      const from = SHAPES[phase.from], to = moving ? SHAPES[phase.to] : null;
      // 읽는 띠(포스터)와 재생 선(영상)의 지금 자리. 머무는 동안에만 있다.
      const scanY = !moving && phase.from === 'poster' && phase.hold > SCAN.from && phase.hold < SCAN.to + 0.5
        ? SCAN.top + (SCAN.bottom - SCAN.top) * clamp((phase.hold - SCAN.from) / (SCAN.to - SCAN.from)) : null;
      const playX = !moving && phase.from === 'video' && phase.hold > PLAY.from - 0.3
        ? FILM_TRACK.from + (FILM_TRACK.to - FILM_TRACK.from) * clamp((phase.hold - PLAY.from) / (PLAY.to - PLAY.from)) : null;

      // 머무는 동안의 검수 장면은 처음 0.6초에 떠오르고, 끝나기 0.5초 전부터 옅어져 점들이 떠날 때는 남지 않는다.
      const fade = moving ? 0 : clamp((HOLD[phase.from] - phase.hold) / 0.5);
      const risen = fade * clamp(phase.hold / 0.6);
      const flat = (x0, y0, z0) => { const [x, y, depth] = place(x0, y0, z0, poseFrom); return toScreen(x, y, depth); };
      // 면 하나: 옅은 흰 종이에 가는 테두리. lift면 아래로 부드러운 그림자를 둔다.
      const plane = (x1, y1, x2, y2, z, strength, lift) => {
        const corners = [flat(x1, y1, z), flat(x2, y1, z), flat(x2, y2, z), flat(x1, y2, z)];
        context.beginPath();
        corners.forEach(([x, y], at) => (at ? context.lineTo(x, y) : context.moveTo(x, y)));
        context.closePath();
        if (lift) { context.shadowColor = `rgba(30,30,25,${0.2 * strength})`; context.shadowBlur = 36 * unit; context.shadowOffsetY = 16 * unit; }
        context.fillStyle = `rgba(255,255,255,${strength})`; context.fill();
        context.shadowColor = 'transparent'; context.shadowBlur = 0; context.shadowOffsetY = 0;
        context.strokeStyle = `rgba(${ink},${0.3 * strength})`; context.lineWidth = 1; context.stroke();
      };
      if (risen > 0 && !quiet && phase.from === 'poster') plane(-SHEET_SIZE.hw, -SHEET_SIZE.hh, SHEET_SIZE.hw, SHEET_SIZE.hh, 0, 0.7 * risen, true);
      if (risen > 0 && !quiet && phase.from === 'video') {
        FILM_FRAMES.centers.forEach(([cx, cy, cz], at) => plane(cx - FILM_FRAMES.hw, cy - FILM_FRAMES.hh, cx + FILM_FRAMES.hw, cy + FILM_FRAMES.hh, cz, [0.34, 0.46, 0.66][at] * risen, at === 2));
      }

      for (let index = 0; index < COUNT; index++) {
        const point = from[index];
        let [x, y, depth] = place(point[0], point[1], point[2], poseFrom);
        let [wide, tall, alpha, cross] = look(point[3], depth);
        if (to) {
          // 위쪽 점부터 차례로 떠나, 보는 사람 쪽으로 살짝 부풀었다가 새 자리에 앉는다
          const go = ease(clamp(phase.mix * (1 + STAGGER) - STAGGER * (1 - index / COUNT)));
          const target = to[index], [tx, ty, tz] = place(target[0], target[1], target[2], poseTo);
          x += (tx - x) * go; y += (ty - y) * go; depth += (tz - depth) * go + Math.sin(Math.PI * go) * 0.22;
          const [w2, h2, a2, c2] = look(target[3], depth);
          wide += (w2 - wide) * go; tall += (h2 - tall) * go; alpha += (a2 - alpha) * go; cross += (c2 - cross) * go;
        } else if (scanY !== null) {
          const behind = scanY - point[1];   // 띠가 막 지나간 자리는 잠깐 진해진다
          if (behind >= 0 && behind < 0.26) alpha *= 1 + 1.5 * (1 - behind / 0.26);
        } else if (playX !== null && point[1] > 0.36 && Math.abs(point[0] - playX) < 0.07) alpha *= 1.8;   // 재생 선 아래의 표본 장면
        const [left, top] = toScreen(x, y, depth);
        if (quiet && point[3] !== 'tick') alpha *= 0.8;
        context.fillStyle = `rgba(${ink},${Math.min(0.8, alpha)})`;
        context.fillRect(left - (wide * unit) / 2, top - (tall * unit) / 2, wide * unit, tall * unit);
        if (cross > 0.02) {
          // 구의 앞쪽 눈금에는 가는 십자를 겹친다
          context.strokeStyle = `rgba(${monochrome ? '90,90,90' : '90,95,85'},${0.11 * cross})`;
          context.lineWidth = 1;
          context.beginPath();
          context.moveTo(left - 4, top); context.lineTo(left + 7, top);
          context.moveTo(left, top - 4); context.lineTo(left, top + 8);
          context.stroke();
        }
      }
      if (moving) return;

      const hush = quiet ? 0.5 : 1;   // 조용한 모드에서는 읽는 띠와 재생 선도 옅게
      if (scanY !== null) {
        const show = hush * fade * clamp((SCAN.to + 0.5 - phase.hold) / 0.5) * clamp((phase.hold - SCAN.from) / 0.3);
        const reach = SHEET_SIZE.hw + 0.05;
        const [ax, ay] = flat(-reach, scanY, 0), [bx, by] = flat(reach, scanY, 0), [cx, cy] = flat(reach, scanY - 0.2, 0), [dx, dy] = flat(-reach, scanY - 0.2, 0);
        const glow = context.createLinearGradient((dx + cx) / 2, (dy + cy) / 2, (ax + bx) / 2, (ay + by) / 2);
        glow.addColorStop(0, `rgba(${ink},0)`); glow.addColorStop(1, `rgba(${ink},${0.09 * show})`);
        context.fillStyle = glow;
        context.beginPath(); context.moveTo(ax, ay); context.lineTo(bx, by); context.lineTo(cx, cy); context.lineTo(dx, dy); context.closePath(); context.fill();
        context.strokeStyle = `rgba(${ink},${0.6 * show})`; context.lineWidth = 1.2 * unit;
        context.beginPath(); context.moveTo(ax, ay); context.lineTo(bx, by); context.stroke();
      }
      if (playX !== null) {
        const show = hush * fade * clamp((phase.hold - PLAY.from + 0.3) / 0.3);
        const [ax, ay] = flat(playX, FILM_TRACK.y - 0.2, 0), [bx, by] = flat(playX, FILM_TRACK.y + 0.085, 0);
        context.strokeStyle = `rgba(${ink},${0.6 * show})`; context.lineWidth = 1.2 * unit;
        context.beginPath(); context.moveTo(ax, ay); context.lineTo(bx, by); context.stroke();
        context.fillStyle = `rgba(${ink},${0.7 * show})`;
        context.beginPath(); context.arc(bx, by, 2.6 * unit, 0, Math.PI * 2); context.fill();
        // 재생 선이 지나는 표본 장면을 짚는다
        const sample = clamp(Math.round((playX - FILM_SAMPLES.first) / FILM_SAMPLES.step), 0, FILM_SAMPLES.count - 1);
        const sx = FILM_SAMPLES.first + sample * FILM_SAMPLES.step, grow = 0.016;
        const spot = [flat(sx - FILM_SAMPLES.hw - grow, FILM_SAMPLES.y - FILM_SAMPLES.hh - grow, 0), flat(sx + FILM_SAMPLES.hw + grow, FILM_SAMPLES.y - FILM_SAMPLES.hh - grow, 0),
          flat(sx + FILM_SAMPLES.hw + grow, FILM_SAMPLES.y + FILM_SAMPLES.hh + grow, 0), flat(sx - FILM_SAMPLES.hw - grow, FILM_SAMPLES.y + FILM_SAMPLES.hh + grow, 0)];
        context.strokeStyle = `rgba(${ink},${0.55 * show})`; context.lineWidth = 1.2 * unit;
        context.beginPath(); spot.forEach(([x, y], at) => (at ? context.lineTo(x, y) : context.moveTo(x, y))); context.closePath(); context.stroke();
      }
      for (const mark of MARKS[phase.from] || []) {
        const since = phase.hold - mark.at;
        if (since <= 0) continue;
        const grow = ease(clamp(since / 0.55)), tone = TONES[mark.tone];
        const [x1, y1, x2, y2] = mark.box;
        const corners = [flat(x1, y1, mark.z), flat(x2, y1, mark.z), flat(x2, y2, mark.z), flat(x1, y2, mark.z)];
        let around = 0;
        for (let side = 0; side < 4; side++) { const next = corners[(side + 1) % 4]; around += Math.hypot(next[0] - corners[side][0], next[1] - corners[side][1]); }
        context.beginPath();
        corners.forEach(([x, y], at) => (at ? context.lineTo(x, y) : context.moveTo(x, y)));
        context.closePath();
        if (grow >= 1 && !quiet) { context.fillStyle = `rgba(${tone},${0.06 * fade})`; context.fill(); }
        // 상자는 왼쪽 위에서 시계 방향으로 그려진다
        context.setLineDash([around * grow, around]);
        context.strokeStyle = quiet ? `rgba(${ink},${0.36 * fade})` : `rgba(${tone},${0.92 * fade})`;
        context.lineWidth = (quiet ? 1 : 1.5) * unit; context.lineJoin = 'round';
        context.stroke();
        context.setLineDash([]);
        if (quiet) continue;   // 조용한 모드: 가는 상자 선만 남긴다
        // 번호 꼬리표(상자 왼쪽 위). 작은 그림에서도 번호가 읽히게 꼬리표와 판정 표시는 일정 크기 아래로 줄이지 않는다.
        const sign = Math.max(unit, 0.9);
        const tag = clamp(since / 0.2) * fade, [tx, ty] = corners[0];
        context.fillStyle = `rgba(${tone},${0.94 * tag})`;
        context.fillRect(tx - 0.75 * unit, ty - 12.5 * sign, 19 * sign, 12.5 * sign);
        context.fillStyle = `rgba(251,250,247,${tag})`;
        context.font = `700 ${Math.round(9 * sign)}px Consolas, 'SFMono-Regular', monospace`;
        context.textAlign = 'center'; context.textBaseline = 'middle';
        context.fillText(mark.id, tx - 0.75 * unit + 9.5 * sign, ty - 6 * sign);
        // 판정 표시(상자 오른쪽 위): 충족은 체크, 확인 필요는 느낌표, 불충족은 가위표
        const seal = clamp((since - 0.55) / 0.3);
        if (seal > 0) {
          const [sx, sy] = corners[1], size = 6.5 * sign * pop(seal);
          context.fillStyle = `rgba(${tone},${fade})`;
          context.beginPath(); context.arc(sx, sy, size, 0, Math.PI * 2); context.fill();
          context.strokeStyle = PAPER; context.globalAlpha = fade; context.lineWidth = 1.5 * sign; context.lineCap = 'round';
          context.beginPath();
          if (mark.tone === 'pass') { context.moveTo(sx - size * 0.45, sy + size * 0.02); context.lineTo(sx - size * 0.12, sy + size * 0.36); context.lineTo(sx + size * 0.46, sy - size * 0.32); }
          else if (mark.tone === 'fail') { context.moveTo(sx - size * 0.34, sy - size * 0.34); context.lineTo(sx + size * 0.34, sy + size * 0.34); context.moveTo(sx + size * 0.34, sy - size * 0.34); context.lineTo(sx - size * 0.34, sy + size * 0.34); }
          else { context.moveTo(sx, sy - size * 0.48); context.lineTo(sx, sy + size * 0.1); context.moveTo(sx, sy + size * 0.44); context.lineTo(sx, sy + size * 0.45); }
          context.stroke();
          context.globalAlpha = 1; context.lineCap = 'butt';
        }
      }
    }
    function tick(now) {
      const step = Math.min(0.1, (now - drawnAt) / 1000);
      // 모양이 바뀌는 동안은 매 프레임, 머무는 동안은 초당 30장쯤만 그린다
      const quick = phaseAt(clock, scene).mix > 0;
      if ((quick || now - drawnAt > 32) && onScreen && !document.hidden) {
        drawnAt = now;
        clock += step;
        draw(clock);
      }
      frame = requestAnimationFrame(tick);
    }
    function restart() {
      cancelAnimationFrame(frame);
      if (reduced.matches) { draw(stillAt(scene)); return; }
      draw(clock);
      if (onScreen) { drawnAt = performance.now(); frame = requestAnimationFrame(tick); }
    }

    const resize = new ResizeObserver(([entry]) => {
      width = entry.contentRect.width;
      height = entry.contentRect.height;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      node.width = Math.round(width * ratio);
      node.height = Math.round(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      draw(reduced.matches ? stillAt(scene) : clock);
    });
    resize.observe(node);
    const visibility = new IntersectionObserver(([entry]) => {
      onScreen = entry.isIntersecting;
      restart();
    });
    visibility.observe(node);
    reduced.addEventListener('change', restart);
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      visibility.disconnect();
      reduced.removeEventListener('change', restart);
    };
  }, [monochrome, scene, quiet]);

  return <canvas ref={canvas} className="op-pixel-field" aria-hidden="true" />;
}
