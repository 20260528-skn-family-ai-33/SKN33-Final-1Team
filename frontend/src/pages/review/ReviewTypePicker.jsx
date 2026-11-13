import React, { useEffect } from 'react';
import { Icon } from '../../components/Icon.jsx';
import { PixelField } from '../../components/PixelField.jsx';
import '../../styles/review-type-picker.css';

// 자료 입력 전에 고르는 검수 유형. text는 요구사항 문구 검수, logo는 원본 로고 대조, both는 둘 다(화면 이름 '종합 검수').
export const reviewModeLabels = { text: '문구 검수', logo: '로고 검수', both: '종합 검수' };
// 검수 유형보다 먼저 묻는 보안 자료 여부. 보안 자료는 기존 RunPod 파이프라인만, 일반 자료는 OpenAI API로 처리한다
// (요구사항 추출·포스터 검사·로고 위치 탐지. 로고 정밀 대조는 둘 다 RunPod) — workflow.js modelRouteFor 참고.
export const securityModeLabels = { secure: '보안 자료', general: '일반 자료' };

const TYPE_TILES = [
  { value: 'text', key: '1', label: '문구 검수' },
  { value: 'logo', key: '2', label: '로고 검수' },
  { value: 'both', key: '3', label: '종합 검수' },
];

function Check({ x, y }) {
  return <g>
    <circle cx={x} cy={y} r="13" fill="#177d45" />
    <path d={`M${x - 6} ${y} l4.5 4.5 L${x + 7} ${y - 4}`} stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" fill="none" />
  </g>;
}

// 타일 그림의 공통 포스터. highlight로 문구·로고 부분을 진하게 그린다.
export function Poster({ id, text = true, logo = true }) {
  const sky = `rv-type-sky-${id}`;
  const shadow = `rv-type-shadow-${id}`;
  return <>
    <defs>
      <linearGradient id={sky} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#f3d9b1" /><stop offset="1" stopColor="#e7a97c" /></linearGradient>
      <filter id={shadow} x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="6" stdDeviation="6" floodColor="#000" floodOpacity=".14" /></filter>
    </defs>
    <ellipse cx="104" cy="236" rx="70" ry="7" fill="#000" opacity=".08" />
    <g filter={`url(#${shadow})`}>
      <rect x="34" y="14" width="140" height="210" rx="8" fill="#fff" stroke="#dcdcd6" />
      <rect x="44" y="24" width="120" height="92" rx="4" fill={`url(#${sky})`} />
      <circle cx="130" cy="50" r="12" fill="#fff6e6" opacity=".9" />
      <path d="M44 116 L80 76 L104 98 L124 82 L164 116 Z" fill="#c97e55" />
      <rect x="44" y="130" width="96" height="12" rx="2" fill={text ? '#171717' : '#d9d9d4'} />
      <rect x="44" y="150" width="110" height="6" rx="3" fill={text ? '#c9c9c3' : '#e4e4df'} />
      <rect x="44" y="162" width="80" height="6" rx="3" fill={text ? '#c9c9c3' : '#e4e4df'} />
      <circle cx="128" cy="200" r="8" fill={logo ? '#171717' : '#d9d9d4'} />
      <rect x="139" y="195" width="20" height="10" rx="2" fill={logo ? '#62645e' : '#e4e4df'} />
    </g>
  </>;
}

// 포스터 한 장 위에 검수할 부분(문구·로고)을 점선과 체크로 표시한 그림.
export function TypeArt({ mode }) {
  const text = mode !== 'logo';
  const logo = mode !== 'text';
  return <svg viewBox="0 0 230 250" fill="none" focusable="false">
    <Poster id={mode} text={text} logo={logo} />
    {text && <rect x="38" y="124" width="124" height="50" rx="6" stroke="#171717" strokeWidth="2.4" strokeDasharray="6 4" />}
    {logo && <rect x="114" y="186" width="54" height="28" rx="5" stroke="#171717" strokeWidth="2.4" strokeDasharray="6 4" />}
    {mode === 'text' && <g>
      <circle cx="170" cy="150" r="34" fill="#fff" stroke="#171717" strokeWidth="6" />
      <rect x="150" y="140" width="40" height="8" rx="2" fill="#171717" />
      <rect x="150" y="154" width="30" height="5" rx="2.5" fill="#9a9b95" />
      <path d="M194 176 L214 198" stroke="#171717" strokeWidth="10" strokeLinecap="round" />
    </g>}
    {mode === 'logo' && <g>
      <circle cx="176" cy="118" r="38" fill="#fff" stroke="#171717" strokeWidth="6" />
      <circle cx="164" cy="118" r="12" fill="#171717" />
      <rect x="180" y="111" width="22" height="14" rx="3" fill="#62645e" />
      <path d="M150 146 L132 168" stroke="#171717" strokeWidth="10" strokeLinecap="round" />
      <Check x={203} y={86} />
    </g>}
    {text && <Check x={66} y={124} />}
    {mode === 'both' && <Check x={168} y={186} />}
  </svg>;
}

// 동영상 검수의 타일 그림(샘플 체험 목업과 같은 그림). 필름 구멍이 있는 가로 영상 프레임에 자막 줄과 로고를 둔다.
export function VideoFrame({ text = true, logo = true }) {
  return <>
    <rect x="8" y="58" width="214" height="128" rx="10" fill="#171717" />
    <rect x="18" y="68" width="194" height="108" rx="4" fill="#e7a97c" />
    <path d="M18 176 L70 128 L104 152 L140 116 L212 176 Z" fill="#c97e55" />
    <rect x="58" y="150" width="114" height="12" rx="3" fill={text ? '#171717' : '#d9d9d4'} opacity={text ? 1 : .8} />
    <circle cx="176" cy="88" r="7" fill={logo ? '#171717' : '#d9d9d4'} />
    <rect x="186" y="83" width="18" height="10" rx="2" fill={logo ? '#62645e' : '#e4e4df'} />
    {[0, 1, 2, 3, 4, 5, 6].map(i => <g key={i}>
      <rect x={16 + i * 30} y="62" width="14" height="3" rx="1" fill="#464642" />
      <rect x={16 + i * 30} y="181" width="14" height="3" rx="1" fill="#464642" />
    </g>)}
  </>;
}

// 영상 프레임 위에 검수할 부분(자막 줄·로고)을 점선과 체크로 표시한 그림.
export function VideoTypeArt({ mode }) {
  const text = mode !== 'logo';
  const logo = mode !== 'text';
  return <svg viewBox="0 0 230 250" fill="none" focusable="false">
    <VideoFrame text={text} logo={logo} />
    {text && <><rect x="52" y="144" width="126" height="24" rx="5" stroke="#fff" strokeWidth="2.4" strokeDasharray="6 4" /><Check x={56} y={144} /></>}
    {logo && <><rect x="168" y="76" width="42" height="24" rx="5" stroke="#fff" strokeWidth="2.4" strokeDasharray="6 4" /><Check x={208} y={76} /></>}
  </svg>;
}

// 보안 자료는 잠긴 자물쇠와 대외비 띠, 일반 자료는 열린 자물쇠로 표시한다. media='video'면 포스터 대신 영상 프레임 위에 그린다.
export function SecurityArt({ secure, media = 'poster' }) {
  return <svg viewBox="0 0 230 250" fill="none" focusable="false">
    {media === 'video' ? <VideoFrame /> : <Poster id={secure ? 'secure' : 'general'} />}
    {secure ? <g>
      <rect x="40" y="62" width="128" height="22" rx="3" fill="#a34f3e" transform="rotate(-12 104 73)" />
      <text x="104" y="78" textAnchor="middle" transform="rotate(-12 104 73)" fill="#fff" fontSize="13" fontWeight="700"
        fontFamily="'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif" letterSpacing="2">대외비</text>
      <path d="M154 146 v-16 a20 20 0 0 1 40 0 v16" stroke="#171717" strokeWidth="9" strokeLinecap="round" />
      <rect x="140" y="144" width="68" height="56" rx="10" fill="#171717" />
      <circle cx="174" cy="166" r="6" fill="#fff" />
      <rect x="171" y="168" width="6" height="16" rx="3" fill="#fff" />
    </g> : <g>
      <path d="M154 146 v-16 a20 20 0 0 1 40 0 v-6" stroke="#9a9b95" strokeWidth="9" strokeLinecap="round" transform="rotate(-18 154 146)" />
      <rect x="140" y="144" width="68" height="56" rx="10" fill="#fff" stroke="#9a9b95" strokeWidth="5" />
      <circle cx="174" cy="166" r="6" fill="#9a9b95" />
      <rect x="171" y="168" width="6" height="16" rx="3" fill="#9a9b95" />
      <Check x={204} y={140} />
    </g>}
  </svg>;
}

// 타일 그림은 자료 종류(포스터·동영상)별로 한 번만 만든다.
const TILE_MEDIA = ['poster', 'video'];
const securityTiles = Object.fromEntries(TILE_MEDIA.map(media => [media, [
  { value: 'secure', key: '1', label: '보안 자료', art: <SecurityArt secure media={media} /> },
  { value: 'general', key: '2', label: '일반 자료', art: <SecurityArt secure={false} media={media} /> },
]]));
const typeTiles = Object.fromEntries(TILE_MEDIA.map(media => [media, TYPE_TILES.map(tile => ({ ...tile,
  art: media === 'video' ? <VideoTypeArt mode={tile.value} /> : <TypeArt mode={tile.value} /> }))]));

// 마우스를 따라 타일 그림이 입체로 기운다(--rx·--ry). 움직임을 줄인 환경과 터치 화면에서는 기울이지 않는다.
export function tiltArt(event) {
  if (!window.matchMedia('(hover: hover) and (prefers-reduced-motion: no-preference)').matches) return;
  const tile = event.currentTarget;
  const area = tile.getBoundingClientRect();
  tile.style.setProperty('--ry', `${(((event.clientX - area.left) / area.width - 0.5) * 16).toFixed(2)}deg`);
  tile.style.setProperty('--rx', `${((0.5 - (event.clientY - area.top) / area.height) * 12).toFixed(2)}deg`);
}
export function settleArt(event) {
  event.currentTarget.style.removeProperty('--rx');
  event.currentTarget.style.removeProperty('--ry');
}

// 키오스크처럼 큰 그림 타일만 두고, 누르면 바로 다음 화면으로 넘어간다. 숫자 키로도 고른다.
function KioskChoice({ title, kicker, tiles, labels, onSelect, onBack, backLabel, context, disabled = false }) {
  useEffect(() => {
    function choose(event) {
      if (disabled || event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.target.closest?.('input, textarea, select, [contenteditable="true"]')) return;
      const tile = tiles.find(item => item.key === event.key);
      if (!tile) return;
      event.preventDefault();
      onSelect(tile.value);
    }
    window.addEventListener('keydown', choose);
    return () => window.removeEventListener('keydown', choose);
  }, [disabled, onSelect, tiles]);

  return <section className="rv-type-picker" aria-labelledby="page-title">
    <PixelField monochrome />
    {onBack && <button type="button" className="rv-type-back" disabled={disabled} onClick={onBack}>
      <Icon name="arrow" />{backLabel}</button>}
    <div className="rv-type-heading">
      <p className="op-eyebrow"><span aria-hidden="true" />{kicker}{context && <span className="rv-type-context">{context}</span>}</p>
      <h1 id="page-title" tabIndex={-1}>{title}</h1>
    </div>
    <div className="rv-type-tiles">
      {tiles.map(tile => <button key={tile.value} type="button" className="rv-type-tile" disabled={disabled}
        aria-label={labels[tile.value]} onClick={() => onSelect(tile.value)} onPointerMove={tiltArt} onPointerLeave={settleArt}>
        <span className="rv-type-art" aria-hidden="true">{tile.art}</span>
        <span className="rv-type-label">{tile.label}{tile.note && <small>({tile.note})</small>}</span>
      </button>)}
    </div>
  </section>;
}

// media: 'poster'(기본) | 'video'. 동영상 검수 화면은 영상 프레임 그림을 쓴다.
export function SecurityPicker({ onSelect, disabled = false, media = 'poster' }) {
  return <KioskChoice title="보안이 필요한 자료인가요?" kicker="SECURITY / 자료 구분" tiles={securityTiles[media] || securityTiles.poster} labels={securityModeLabels}
    onSelect={onSelect} disabled={disabled} />;
}

export default function ReviewTypePicker({ onSelect, onBack, security, disabled = false, media = 'poster' }) {
  return <KioskChoice title="검수할 항목을 선택해 주세요" kicker="REVIEW TYPE / 검수 유형" tiles={typeTiles[media] || typeTiles.poster} labels={reviewModeLabels}
    onSelect={onSelect} onBack={onBack} backLabel="보안 여부 다시 고르기" disabled={disabled}
    context={security && <>{security === 'secure' && <Icon name="lock" />}{securityModeLabels[security]}</>} />;
}
