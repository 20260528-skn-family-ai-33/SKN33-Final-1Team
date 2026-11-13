import React, { useEffect, useRef } from 'react';

// 첫 화면 배경의 점 구체: 작은 눈금 520개를 구 위에 고르게 놓고 천천히 돌린다.
// 화면 밖이거나 탭이 가려지면 멈추고, 움직임을 줄인 환경에서는 한 장만 그린다. 장식이라 낭독기에는 숨긴다.
const POINTS = Array.from({ length: 520 }, (_, index) => {
  const y = 1 - (index / 519) * 2;
  const ring = Math.sqrt(1 - y * y);
  const turn = index * 2.3999632297; // 황금각: 점이 한쪽에 몰리지 않는다
  return [Math.cos(turn) * ring, y, Math.sin(turn) * ring];
});

export function PixelField({ monochrome = false }) {
  const canvas = useRef(null);

  useEffect(() => {
    const node = canvas.current;
    const context = node.getContext('2d');
    if (!context) return undefined;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    let angle = 0;
    let drawnAt = 0;
    let onScreen = true;
    let width = 0;
    let height = 0;

    function draw() {
      context.clearRect(0, 0, width, height);
      const radius = Math.min(width, height) * 0.43;
      const cos = Math.cos(angle);
      const sin = Math.sin(angle);
      for (const [px, py, pz] of POINTS) {
        const x = px * cos + pz * sin;
        const depth = pz * cos - px * sin;
        const scale = 1 + depth * 0.18;
        const left = width / 2 + x * radius * scale;
        const top = height / 2 + py * radius * scale;
        const size = depth > 0.25 ? 3 : 1.5;
        context.fillStyle = `rgba(${monochrome ? '75,75,75' : '70,75,65'},${0.08 + (depth + 1) * 0.1})`;
        context.fillRect(left, top, size, size * (depth > 0.5 ? 3 : 1));
        if (depth > 0.45) {
          // 앞쪽 점에는 가는 십자 눈금을 겹친다
          context.strokeStyle = monochrome ? 'rgba(90,90,90,.11)' : 'rgba(90,95,85,.11)';
          context.beginPath();
          context.moveTo(left - 4, top);
          context.lineTo(left + 7, top);
          context.moveTo(left, top - 4);
          context.lineTo(left, top + 8);
          context.stroke();
        }
      }
    }
    function tick(now) {
      if (now - drawnAt > 40 && onScreen && !document.hidden) {
        drawnAt = now;
        angle += 0.003;
        draw();
      }
      frame = requestAnimationFrame(tick);
    }
    function restart() {
      cancelAnimationFrame(frame);
      draw();
      if (!reduced.matches && onScreen) frame = requestAnimationFrame(tick);
    }

    const resize = new ResizeObserver(([entry]) => {
      width = entry.contentRect.width;
      height = entry.contentRect.height;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      node.width = Math.round(width * ratio);
      node.height = Math.round(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      draw();
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
  }, [monochrome]);

  return <canvas ref={canvas} className="op-pixel-field" aria-hidden="true" />;
}
