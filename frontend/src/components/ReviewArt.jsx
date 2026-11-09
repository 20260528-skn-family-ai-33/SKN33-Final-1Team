import React, { useId } from 'react';
import '../styles/review-art.css';

// 서비스 안내 그림. 모든 그림에 검수 진행 화면과 같은 돋보기(흰 테 · 잉크 윤곽 · 초록 읽기 선)를 쓰고,
// 그려진 포스터는 미리보기 포스터의 색(짙은 초록·모래·크림·벽돌)을 쓴다. 장식용이라 화면 낭독기에는 숨긴다.

const safe = id => id.replace(/[^a-zA-Z0-9_-]/g, '');

// 돋보기: 원 중심(cx, cy), 반지름 r. 안쪽(children)은 유리 안에 잘려 보인다.
function Lens({ cx, cy, r, clip, children }) {
  const handle = r * .72, end = r * 1.62;
  return <g className="cc-art-lens-at" transform={`translate(${cx} ${cy})`}>
    <g className="cc-art-lens">
      <line x1={handle} y1={handle} x2={end} y2={end} className="cc-art-handle-edge" />
      <line x1={handle} y1={handle} x2={end} y2={end} className="cc-art-handle" />
      <circle r={r} className="cc-art-glass" />
      <g clipPath={`url(#${clip})`}>{children}<line x1={-r} x2={r} y1={-r * .45} y2={-r * .45} className="cc-art-scan" /></g>
      <circle r={r} className="cc-art-rim" />
      <circle r={r + 2.6} className="cc-art-ring" />
    </g>
  </g>;
}

function Poster() {
  // 92×124 포스터 한 장(왼쪽 위 0,0). 돋보기 안 확대에도 같은 그림을 다시 그린다(<use> 복제본에는 CSS가 안 맞을 수 있다).
  return <g>
    <rect width="92" height="124" rx="3" className="cc-art-poster-ground" />
    <circle cx="74" cy="84" r="36" className="cc-art-poster-sand" />
    <rect x="10" y="62" width="42" height="48" transform="rotate(-10 31 86)" className="cc-art-poster-cream" />
    <circle cx="31" cy="86" r="12" className="cc-art-poster-brick" />
    <path d="M23 86h16M31 78v16" className="cc-art-poster-cross" />
    <rect x="10" y="14" width="54" height="9" rx="1" className="cc-art-poster-title" />
    <rect x="10" y="28" width="36" height="4" rx="1" className="cc-art-poster-title is-soft" />
    <rect x="10" y="36" width="44" height="4" rx="1" className="cc-art-poster-title is-soft" />
  </g>;
}

// 포스터 올리기 영역: 테이프로 붙인 포스터, 제목에서 찾은 요구사항(노란 상자), 돋보기.
export function PosterDropArt() {
  const id = safe(useId()), clip = `${id}-clip`, sheet = `${id}-sheet`;
  return <svg className="cc-art cc-art-poster" viewBox="0 0 220 170" aria-hidden="true" focusable="false">
    <defs><clipPath id={clip}><circle r="23" /></clipPath><clipPath id={sheet}><rect width="92" height="124" rx="3" /></clipPath></defs>
    <g transform="translate(62 18)">
      <g className="cc-art-lift">
        <rect x="7" y="7" width="92" height="124" rx="3" className="cc-art-drop" />
        <g clipPath={`url(#${sheet})`}><Poster /></g>
        <rect width="92" height="124" rx="3" className="cc-art-outline" />
        <rect x="6" y="10" width="62" height="17" rx="2" className="cc-art-evidence" />
        <rect x="-8" y="-5" width="26" height="10" transform="rotate(-24 5 0)" className="cc-art-tape" />
        <rect x="74" y="-5" width="26" height="10" transform="rotate(22 87 0)" className="cc-art-tape" />
      </g>
    </g>
    <Lens cx={160} cy={124} r={23} clip={clip}>
      <g transform="scale(1.7) translate(-62 -60)"><Poster /></g>
    </Lens>
  </svg>;
}

function VideoScreen() {
  // 64×116 세로 화면(왼쪽 위 0,0): 장면, 재생 표시, 자막 두 줄.
  return <g>
    <rect width="64" height="116" rx="9" className="cc-art-phone" />
    <rect x="6" y="10" width="52" height="92" rx="3" className="cc-art-poster-ground" />
    <circle cx="44" cy="40" r="17" className="cc-art-poster-sand" />
    <path d="M26 48l14 8-14 8z" className="cc-art-play" />
    <rect x="12" y="78" width="40" height="5" rx="1.5" className="cc-art-caption" />
    <rect x="17" y="87" width="30" height="5" rx="1.5" className="cc-art-caption" />
  </g>;
}

// 숏폼 올리기 영역: 세로 화면 뒤로 지나가는 필름, 자막에서 찾은 문구(노란 상자), 돋보기.
export function VideoDropArt() {
  const id = safe(useId()), clip = `${id}-clip`;
  const holes = Array.from({ length: 10 }, (_, index) => 6 + index * 17);
  return <svg className="cc-art cc-art-video" viewBox="0 0 220 170" aria-hidden="true" focusable="false">
    <defs><clipPath id={clip}><circle r="23" /></clipPath></defs>
    <g transform="translate(16 60)">
      <rect width="188" height="38" rx="3" className="cc-art-film" />
      {holes.map(x => <g key={x}><rect x={x} y="4" width="8" height="5" rx="1" className="cc-art-film-hole" /><rect x={x} y="29" width="8" height="5" rx="1" className="cc-art-film-hole" /></g>)}
    </g>
    <g transform="translate(78 14)">
      <g className="cc-art-lift">
        <rect x="7" y="7" width="64" height="116" rx="9" className="cc-art-drop" />
        <VideoScreen />
        <rect width="64" height="116" rx="9" className="cc-art-outline" />
        <rect x="8" y="74" width="48" height="22" rx="2" className="cc-art-evidence" />
      </g>
    </g>
    <Lens cx={160} cy={124} r={23} clip={clip}>
      <g transform="scale(1.8) translate(-30 -84)"><VideoScreen /></g>
    </Lens>
  </svg>;
}

// 없는 페이지: 벽에 테이프 자국만 남은 빈 포스터 자리, 그 자리를 비춰 보는 돋보기.
export function MissingPosterArt() {
  const id = safe(useId()), clip = `${id}-clip`;
  return <svg className="cc-art cc-art-missing" viewBox="0 0 260 190" aria-hidden="true" focusable="false">
    <defs><clipPath id={clip}><circle r="30" /></clipPath></defs>
    <line x1="18" y1="172" x2="242" y2="172" className="cc-art-floor" />
    <rect x="78" y="22" width="104" height="138" rx="3" className="cc-art-gap" />
    <rect x="66" y="16" width="30" height="11" transform="rotate(-24 81 21)" className="cc-art-tape" />
    <rect x="164" y="16" width="30" height="11" transform="rotate(22 179 21)" className="cc-art-tape" />
    <path d="M70 158l12 14 6-9z" className="cc-art-poster-ground" />
    <Lens cx={150} cy={104} r={30} clip={clip}>
      <rect x="-24" y="-40" width="60" height="90" rx="3" className="cc-art-gap" />
    </Lens>
  </svg>;
}

// 빈 작업 목록: 비어 있는 검수 바구니와 옆에 내려 둔 돋보기.
export function EmptyTasksArt() {
  const id = safe(useId()), clip = `${id}-clip`;
  return <svg className="cc-art cc-art-tasks" viewBox="0 0 220 150" aria-hidden="true" focusable="false">
    <defs><clipPath id={clip}><circle r="20" /></clipPath></defs>
    <rect x="34" y="40" width="96" height="70" rx="4" transform="rotate(-6 82 75)" className="cc-art-sheet-ghost" />
    <path d="M24 86h132l-10 44H34z" className="cc-art-tray" />
    <path d="M24 86h132" className="cc-art-outline-line" />
    <rect x="58" y="100" width="64" height="8" rx="4" className="cc-art-tray-slot" />
    <Lens cx={178} cy={70} r={20} clip={clip}>
      <rect x="-30" y="-8" width="60" height="4" rx="2" className="cc-art-line" />
      <rect x="-30" y="2" width="44" height="4" rx="2" className="cc-art-line" />
    </Lens>
  </svg>;
}

// 검색·필터 결과 없음: 노란 표시가 하나도 없는 목록 종이와 돋보기.
export function NoMatchArt() {
  const id = safe(useId()), clip = `${id}-clip`;
  const rows = [16, 30, 44, 58];
  return <svg className="cc-art cc-art-nomatch" viewBox="0 0 120 92" aria-hidden="true" focusable="false">
    <defs><clipPath id={clip}><circle r="16" /></clipPath></defs>
    <rect x="18" y="6" width="62" height="80" rx="4" className="cc-art-sheet" />
    {rows.map(y => <g key={y}><rect x="27" y={y} width="7" height="7" rx="1.5" className="cc-art-box" /><rect x="39" y={y + 1.5} width="32" height="4" rx="2" className="cc-art-line" /></g>)}
    <Lens cx={82} cy={58} r={16} clip={clip}>
      <rect x="-18" y="-6" width="36" height="4" rx="2" className="cc-art-line" />
      <rect x="-18" y="4" width="26" height="4" rx="2" className="cc-art-line" />
    </Lens>
  </svg>;
}
