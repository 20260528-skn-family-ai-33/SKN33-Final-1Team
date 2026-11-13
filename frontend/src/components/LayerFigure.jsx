import React, { useEffect, useRef } from 'react';
import posterUrl from '../assets/sample-poster.png';
import frame1 from '../assets/landing-video-frame-1.jpg';
import frame2 from '../assets/landing-video-frame-2.jpg';
import frame3 from '../assets/landing-video-frame-3.jpg';
import frame4 from '../assets/landing-video-frame-4.jpg';

// 소개 화면의 3D 도해: 검수 대상(포스터 한 장 · 영상의 장면들) 위에 '읽은 자리'와 '판정'을 유리판으로 띄운다.
// 세 장은 아래에서 위로 01 원본 → 02 읽은 자리 → 03 판정. 세로 기둥이 같은 자리를 세 장에 걸쳐 잇는다.
// CSS 3D 변환만 쓰는 장식 그림이라 화면 낭독기에는 숨기고, 내용은 옆 문구가 전한다.
// 포스터와 영상은 같은 예시 전시('빛의 기록')의 자료다: 포스터는 예시 포스터(sample-poster.png), 장면은 예시 동영상(sample-shortform.mp4)의 프레임.
// 한쪽에만 실제 전시 포스터를 쓰면 두 그림의 결이 달라 보여서 둘 다 예시 자료로 맞췄다.

// 근거 상자: 판 크기에 대한 비율(왼쪽, 위, 너비, 높이 %)과 그 위에 뜨는 판정
// 포스터(3:4): 01 전시 제목 · 02 주최 로고 · 03 전시 기간
const posterMarks = [
  { id: '01', tone: 'pass', label: '충족', box: [8.6, 14.6, 63.2, 13.6] },
  { id: '02', tone: 'review', label: '확인 필요', box: [80.7, 82.2, 10.7, 8] },
  { id: '03', tone: 'pass', label: '충족', box: [8.6, 82.6, 38.9, 6] },
];
const videoFrames = [[frame1, '0:02'], [frame2, '0:05'], [frame3, '0:07'], [frame4, '0:10']];
const videoMarks = [
  { id: '01', tone: 'pass', label: '충족', box: [6.6, 34.6, 11.8, 11.6] },
  { id: '02', tone: 'fail', label: '위반 · 0:05', box: [33.2, 9.6, 8.6, 7.6] },
  { id: '03', tone: 'review', label: '확인 필요', box: [91.6, 70.4, 4.4, 9.6] },
];

// 마우스가 지나가면 쌓인 판이 조금 따라 돈다. 움직임을 줄인 환경과 터치 화면에서는 고정한다.
// 검수 진행 화면의 3D 포스터도 같은 방식으로 기울인다.
export function useTilt() {
  const figure = useRef(null);
  useEffect(() => {
    const node = figure.current;
    if (!node || !window.matchMedia('(hover: hover) and (prefers-reduced-motion: no-preference)').matches) return undefined;
    let frame = 0;
    function follow(event) {
      const area = node.getBoundingClientRect();
      const x = (event.clientX - area.left) / area.width - 0.5;
      const y = (event.clientY - area.top) / area.height - 0.5;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        node.style.setProperty('--turn', `${(x * 10).toFixed(2)}deg`);
        node.style.setProperty('--lean', `${(-y * 6).toFixed(2)}deg`);
      });
    }
    function settle() {
      cancelAnimationFrame(frame);
      node.style.removeProperty('--turn');
      node.style.removeProperty('--lean');
    }
    node.addEventListener('pointermove', follow);
    node.addEventListener('pointerleave', settle);
    return () => {
      node.removeEventListener('pointermove', follow);
      node.removeEventListener('pointerleave', settle);
      cancelAnimationFrame(frame);
    };
  }, []);
  return figure;
}

function Layers({ kind, marks, children }) {
  const figure = useTilt();
  return <div className={`lp-3d lp-3d--${kind}`} ref={figure} aria-hidden="true">
    <div className="lp-3d-stack">
      <div className="lp-3d-plane lp-3d-source">{children}<span className="lp-3d-no">01</span></div>
      <div className="lp-3d-plane lp-3d-glass lp-3d-evidence"><span className="lp-3d-no">02</span>
        {marks.map(mark => <span key={mark.id} className="lp-3d-box"
          style={{ left: `${mark.box[0]}%`, top: `${mark.box[1]}%`, width: `${mark.box[2]}%`, height: `${mark.box[3]}%` }}><b>{mark.id}</b></span>)}
      </div>
      <div className="lp-3d-plane lp-3d-glass lp-3d-verdict"><span className="lp-3d-no">03</span>
        {marks.map(mark => <span key={mark.id} className={`lp-3d-chip is-${mark.tone}`}
          style={{ left: `${mark.box[0]}%`, top: `${mark.box[1] + mark.box[3] / 2}%` }}><i />{mark.label}</span>)}
      </div>
      {marks.map(mark => <span key={mark.id} className="lp-3d-post"
        style={{ left: `${mark.box[0]}%`, top: `${mark.box[1] + mark.box[3] / 2}%` }} />)}
    </div>
  </div>;
}

// 포스터 검수: 포스터 한 장 → 읽은 문구·로고 자리 → 항목별 판정
export function PosterLayers() {
  return <Layers kind="poster" marks={posterMarks}>
    <img src={posterUrl} alt="" width="300" height="400" loading="lazy" draggable={false} />
  </Layers>;
}

// 동영상 검수: 시간순 장면들 → 자막·로고가 나온 자리와 시각 → 항목별 판정
export function VideoLayers() {
  return <Layers kind="video" marks={videoMarks}>
    <div className="lp-3d-film">{videoFrames.map(([src, time]) => <span key={time} className="lp-3d-frame">
      <img src={src} alt="" width="84" height="146" loading="lazy" draggable={false} /><em>{time}</em>
    </span>)}</div>
  </Layers>;
}
