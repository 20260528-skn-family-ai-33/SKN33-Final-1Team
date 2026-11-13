import React, { useEffect } from 'react';
import { Icon } from '../../components/Icon.jsx';
import { PickerArt } from '../../components/PickerArt.jsx';
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

// 타일 그림은 3D 그림(components/PickerArt.jsx) 하나로 그린다. 아래 셋은 화면이 쓰는 이름을 그대로 둔 얇은 포장이다.
// 포스터 위에 검수할 부분(문구·로고)을 유리판의 점선과 체크, 돋보기로 표시한 그림.
export function TypeArt({ mode }) {
  return <PickerArt media="poster" variant={mode} />;
}

// 영상 프레임 위에 검수할 부분(자막 줄·로고)을 표시한 그림.
export function VideoTypeArt({ mode }) {
  return <PickerArt media="video" variant={mode} />;
}

// 보안 자료는 잠긴 자물쇠와 대외비 띠, 일반 자료는 열린 자물쇠로 표시한다. media='video'면 포스터 대신 영상 프레임 위에 그린다.
export function SecurityArt({ secure, media = 'poster' }) {
  return <PickerArt media={media} variant={secure ? 'secure' : 'general'} />;
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
  // 타일 바닥의 점 무늬가 마우스 둘레에서만 드러나게 마우스 자리를 넘긴다.
  tile.style.setProperty('--mx', `${Math.round(event.clientX - area.left)}px`);
  tile.style.setProperty('--my', `${Math.round(event.clientY - area.top)}px`);
}
export function settleArt(event) {
  for (const name of ['--rx', '--ry', '--mx', '--my']) event.currentTarget.style.removeProperty(name);
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
        <span className="op-tile-glow" aria-hidden="true" />
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
