import React from 'react';
import { PickerArt } from '../../components/PickerArt.jsx';
import { CAPTIONS, formatSeconds } from './sampleData.js';

// 예시 포스터·동영상 프레임은 SVG 글을 data URL 이미지(<img>)로 그린다.
// 화면에 SVG 요소를 직접 두면 진행 화면의 돋보기·확대 애니메이션이 프레임마다 글자·그라데이션을 다시 그려 화면이 버벅인다.
// 이미지로 두면 한 번만 그려 두고 옮기기만 한다(실제 진행 화면 ReviewProgress·VideoProgress도 <img>를 쓴다).
const FONT_KO = 'Apple SD Gothic Neo, Malgun Gothic, sans-serif';
const toDataUrl = svg => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;

// 예시 포스터: '빛의 기록' 전시(300×400 좌표). 기간을 일부러 11.29로 넣어 불일치 예시를 보여 준다.
const POSTER_URL = toDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 400" width="300" height="400">
<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1f2a44"/><stop offset=".55" stop-color="#3d4b73"/><stop offset="1" stop-color="#e7a97c"/></linearGradient></defs>
<rect width="300" height="400" fill="url(#sky)"/><circle cx="220" cy="92" r="34" fill="#f6dfb2" opacity=".9"/>
<g fill="#f6dfb2" opacity=".5"><circle cx="40" cy="40" r="1.6"/><circle cx="90" cy="70" r="1.2"/><circle cx="150" cy="30" r="1.4"/><circle cx="260" cy="160" r="1.2"/></g>
<path d="M0 300 L70 230 L120 270 L180 210 L300 300 L300 400 L0 400 Z" fill="#2a2f45" opacity=".55"/>
<text x="26" y="196" fill="#fff" font-family="${FONT_KO}" font-size="40" font-weight="800" letter-spacing="-1">빛의 기록</text>
<text x="28" y="222" fill="#f6dfb2" font-family="Arial, sans-serif" font-size="12" letter-spacing="3">RECORDS OF LIGHT</text>
<text x="28" y="300" fill="#fff" font-family="Arial, sans-serif" font-size="20" font-weight="700">11.20 – 11.29</text>
<text x="28" y="322" fill="#fff" font-family="${FONT_KO}" font-size="12">시립미술관 2층 기획전시실</text>
<rect x="0" y="352" width="300" height="48" fill="#fff"/><circle cx="218" cy="376" r="9" fill="#171717"/><rect x="231" y="370" width="38" height="12" rx="2" fill="#62645e"/>
<text x="28" y="381" fill="#62645e" font-family="${FONT_KO}" font-size="10">주최 · 빛의기록 문화재단</text></svg>`);

// 예시 동영상의 t초 프레임(320×180 좌표). 해가 내려가고, 시각에 맞는 화면 글자와 마지막 구간의 로고가 보인다.
function frameSvg(t) {
  const captions = CAPTIONS.filter(caption => t >= caption.from && t < caption.to).map(caption => caption.kind === 'title'
    ? `<text x="160" y="96" text-anchor="middle" fill="#fff" font-family="${FONT_KO}" font-size="30" font-weight="800">${caption.text}</text>`
    : caption.kind === 'ad'
      ? `<rect x="10" y="10" width="78" height="18" rx="3" fill="#171717" opacity=".75"/><text x="49" y="23" text-anchor="middle" fill="#fff" font-family="${FONT_KO}" font-size="10" font-weight="700">${caption.text}</text>`
      : `<rect x="60" y="146" width="200" height="20" rx="3" fill="#000" opacity=".45"/><text x="160" y="160" text-anchor="middle" fill="#fff" font-family="${FONT_KO}" font-size="11" font-weight="700">${caption.text}</text>`).join('');
  const logo = t >= 26 ? '<rect x="250" y="140" width="58" height="28" rx="4" fill="#fff"/><circle cx="264" cy="154" r="7" fill="#171717"/><rect x="275" y="149" width="26" height="10" rx="2" fill="#62645e"/>' : '';
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180" width="320" height="180">
<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#46507a"/><stop offset=".6" stop-color="#d9906a"/><stop offset="1" stop-color="#f0c08e"/></linearGradient></defs>
<rect width="320" height="180" fill="url(#sky)"/><circle cx="220" cy="${Math.max(60, 140 - t * 2.2)}" r="16" fill="#fbe3b5" opacity=".9"/>
<path d="M0 132 h30 v-18 h14 v18 h20 v-34 h12 v34 h26 v-22 h16 v22 h40 v-40 h10 v40 h28 v-26 h18 v26 h106 V180 H0z" fill="#252a3d" opacity=".85"/>
<path d="M0 150 C80 140 160 158 320 146 V180 H0z" fill="#1d2133" opacity=".7"/>${captions}${logo}</svg>`;
}
// 같은 초의 프레임은 한 번만 만든다(진행 화면이 tick마다 같은 주소를 다시 쓴다).
const frameUrls = new Map();
function frameUrl(t) {
  if (!frameUrls.has(t)) frameUrls.set(t, toDataUrl(frameSvg(t)));
  return frameUrls.get(t);
}

export function SamplePoster({ decorative = false }) {
  return <img src={POSTER_URL} width="300" height="400" draggable={false} decoding="async"
    alt={decorative ? '' : '예시 포스터: 빛의 기록 전시'} />;
}

export function SampleFrame({ t, decorative = true }) {
  return <img src={frameUrl(t)} width="320" height="180" draggable={false} decoding="async"
    alt={decorative ? '' : `예시 동영상 ${formatSeconds(t)} 프레임`} />;
}

export function LogoThumb() {
  return <svg viewBox="0 0 40 40" width="26" height="26" aria-hidden="true">
    <circle cx="15" cy="20" r="7" fill="#171717" /><rect x="24" y="15" width="12" height="10" rx="2" fill="#62645e" />
  </svg>;
}

// 첫 화면(자료 종류) 타일 그림. 실제 고르기 화면과 같은 3D 그림을 쓴다.
export function MediaArt({ kind }) {
  return <PickerArt media={kind} variant="media" />;
}

export function PlayIcon() {
  return <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z" fill="#fff" /></svg>;
}
