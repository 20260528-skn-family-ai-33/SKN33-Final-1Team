import React from 'react';
import '../styles/picker-art.css';

// 고르는 화면 타일의 3D 그림(포스터·동영상). CSS 3D 변환만 쓴다.
// 검수 대상(포스터 한 장 · 영상 프레임 묶음)을 비스듬히 세워 두고, 그 앞에 유리판(검수할 자리)·돋보기·자물쇠를 띄운다.
// 타일이 넘기는 마우스 기울기(--rx·--ry)에 따라 통째로 돌고, 마우스를 올리면 층 사이가 벌어진다(--pa-gap).
// 장식 그림이라 타일 쪽에서 aria-hidden으로 감싼다. 버튼 안에 들어가므로 span·i만 쓴다.

const TYPED = ['text', 'logo', 'both'];
// 검수할 자리(문구·로고). 그림 바탕(포스터 126×178 · 프레임 196×118) 기준 px이고, 처음 자세에서 '보이는 자리'로 적는다.
// box: 점선 상자 [왼쪽, 위, 너비, 높이] · lens: 돋보기 가운데 · check: 체크를 둘 상자 모서리(pair는 둘 다 검수할 때).
const SPOTS = {
  poster: {
    text: { box: [3, 98, 106, 47], lens: [88, 124], check: [3, 98] },
    logo: { box: [71, 142, 53, 32], lens: [112, 167], check: [71, 142], pair: [124, 142] },
  },
  video: {
    text: { box: [40, 78, 116, 23], lens: [130, 93], check: [40, 78] },
    logo: { box: [140, 10, 54, 32], lens: [184, 36], check: [140, 10], pair: [194, 10] },
  },
};

// 앞에 뜬 것은 비스듬히 보면(가로 30° · 세로 -14°, styles/picker-art.css의 .pa-world) 오른쪽 아래로 밀려 보인다.
// 처음 자세에서 바탕의 제자리에 겹쳐 보이도록, 뜬 높이만큼 왼쪽 위로 당겨 둔다. 마우스를 올려 층이 벌어지면 그만큼 어긋나 입체로 보인다.
const SURFACE = 11;   // 바탕 위 내용(제목·자막·로고)의 높이
const HEIGHT = { glass: 31.8, check: 43.2, lens: 59.4, play: 38.2 };   // 층별 높이(.pa-glass 등의 --z0 + --z + 두께)
const seen = (point, height) => [point[0] - (height - SURFACE) * 0.577, point[1] - (height - SURFACE) * 0.25];
const round = value => Math.round(value * 10) / 10;

// 유리판은 바탕보다 사방 5px 크고, 왼쪽 위로 조금 당겨 둔다(.pa-glass의 inset과 같은 값). 유리판 안의 자리는 이 원점 기준이다.
const GLASS_ORIGIN = [-11, -7];
const onGlass = point => { const [x, y] = seen(point, HEIGHT.glass); return [round(x - GLASS_ORIGIN[0]), round(y - GLASS_ORIGIN[1])]; };
// 점선 상자 안쪽은 유리를 뚫어, 검수할 자리만 흐려지지 않고 또렷하게 보인다.
const HOLE_INSET = 3;
function glassMask(spots) {
  const holes = spots.map(({ box }) => {
    const [left, top] = onGlass(box);
    return { position: `${left + HOLE_INSET}px ${top + HOLE_INSET}px`, size: `${box[2] - HOLE_INSET * 2}px ${box[3] - HOLE_INSET * 2}px` };
  });
  const layer = 'linear-gradient(#000 0 0)';
  return {
    maskImage: [...holes.map(() => layer), layer].join(', '),
    maskPosition: [...holes.map(hole => hole.position), '0 0'].join(', '),
    maskSize: [...holes.map(hole => hole.size), '100% 100%'].join(', '),
    maskRepeat: 'no-repeat',
    maskComposite: [...holes.map(() => 'exclude'), 'add'].join(', '),
  };
}

// 같은 모양을 얇게 여러 장 겹쳐 두께를 만든다(둥근 모양에도 옆면이 생긴다). 맨 앞 장이 얼굴이고 children을 담는다.
function Solid({ name, depth = 4, at, faceStyle, children }) {
  return <span className={`pa-solid ${name}`} style={at ? { left: round(at[0]), top: round(at[1]) } : undefined}>
    {Array.from({ length: depth }, (_, index) => index === depth - 1
      ? <i key={index} className="is-face" style={{ '--i': index, ...faceStyle }}>{children}</i>
      : <i key={index} style={{ '--i': index }} />)}
  </span>;
}

function PosterSheet() {
  return <Solid name="pa-sheet" depth={6}>
    <span className="pa-img"><i className="pa-hill is-far" /><i className="pa-hill is-near" /></span>
    <span className="pa-moon" />
    <span className="pa-title" />
    <span className="pa-line is-1" /><span className="pa-line is-2" />
    <span className="pa-logo"><i /><i /></span>
  </Solid>;
}

// back: 뒤에 겹친 프레임(1·2). 영상은 장면이 여러 장이라는 뜻으로 앞 프레임 뒤에 두 장을 더 둔다.
function VideoFrame({ back = 0, children }) {
  return <Solid name={`pa-frame${back ? ` is-back is-back-${back}` : ''}`} depth={6}>
    <span className="pa-sprockets is-top" /><span className="pa-sprockets is-bottom" />
    <span className="pa-screen">{!back && <><i className="pa-sun" /><i className="pa-city" /></>}</span>
    {!back && <><span className="pa-caption" /><span className="pa-logo"><i /><i /></span></>}
    {children}
  </Solid>;
}

// 돋보기. at: 보이는 자리의 가운데 · show: 'text'(확대한 문구 줄) | 'logo'(확대한 로고)
function Lens({ at, show }) {
  const [x, y] = seen(at, HEIGHT.lens);
  return <span className="pa-lens" style={{ left: round(x - 31), top: round(y - 31) }}>
    <span className="pa-handle"><Solid name="pa-handle-bar" depth={4} /></span>
    <Solid name="pa-ring" depth={4}><span className={`pa-lens-glass is-${show}`}><i /><i /></span></Solid>
  </span>;
}

function Lock({ open = false }) {
  return <span className={`pa-lock${open ? ' is-open' : ''}`}>
    <Solid name="pa-shackle" depth={5} />
    <Solid name="pa-lock-body" depth={8}><span className="pa-keyhole" /></Solid>
    {open && <Solid name="pa-check" depth={4} at={[57, 30]} />}
  </span>;
}

// 문서 분석 진행 그림: 요구사항 문서를 돋보기가 읽어 내려가고, 읽은 요구사항이 오른쪽에 카드로 뽑혀 나온다.
// stage는 서버가 알려 준 문서 분석 단계다. 읽는 중(extracting)에만 돋보기와 읽는 띠가 움직이고 카드가 차례로 나온다.
// 정리 단계(formatting)부터는 카드 세 장이 다 나와 있고, 그 전(업로드·대기)에는 문서와 돋보기만 떠 있다.
// 카드가 나오는 박자는 '일하는 중'이라는 표시일 뿐 진행률이 아니다. 진행률은 그림 아래 막대가 보여 준다.
const DOC_LINES = [1, 2, 3, 4, 5, 6, 7];
const DOC_CARDS = [1, 2, 3];
export function DocumentArt({ stage }) {
  const phase = stage === 'extracting' ? 'reading' : stage === 'formatting' || stage === 'completed' ? 'sorting' : 'waiting';
  return <span className="pa is-document" data-phase={phase}>
    <span className="pa-world">
      <span className="pa-floor" />
      <span className="pa-obj">
        <Solid name="pa-sheet is-back" depth={6} />
        <Solid name="pa-sheet" depth={6}>
          <span className="pa-doc-head" />
          {DOC_LINES.map(line => <span key={line} className={`pa-doc-line is-${line}`} />)}
          <span className="pa-doc-read" />
        </Solid>
        <Lens at={[86, 44]} show="text" />
        {DOC_CARDS.map(card => <Solid key={card} name={`pa-card is-${card}`} depth={3}>
          <span className="pa-card-dot" /><span className="pa-card-line" />
        </Solid>)}
      </span>
    </span>
  </span>;
}

// media: 'poster' | 'video'
// variant: 'media'(자료 종류) | 'secure' · 'general'(보안 여부) | 'text' · 'logo' · 'both'(검수 항목)
export function PickerArt({ media = 'poster', variant = 'media' }) {
  const video = media === 'video';
  const typed = TYPED.includes(variant);
  const spots = typed ? ['text', 'logo'].filter(key => variant === 'both' || variant === key).map(key => SPOTS[media][key]) : [];
  const lens = typed && variant !== 'both' ? spots[0].lens : null;
  // 돋보기가 유리판에 떨구는 그림자: 빛이 왼쪽 위에서 오므로 돋보기보다 오른쪽 아래로 조금 나온다.
  const lensShade = lens && onGlass([lens[0] + 6, lens[1] + 5]);
  // 재생 단추는 처음 자세에서 프레임 한가운데(98, 59)에 겹쳐 보이게 둔다.
  const play = seen([98, 59], HEIGHT.play);
  return <span className={`pa is-${media} is-${variant}`} data-text={!typed || variant !== 'logo' ? 'on' : 'off'}
    data-logo={!typed || variant !== 'text' ? 'on' : 'off'}>
    <span className="pa-world">
      <span className="pa-floor" />
      <span className="pa-obj">
        {video ? <>
          <VideoFrame back={2} /><VideoFrame back={1} />
          <VideoFrame>{variant === 'media' && <span className="pa-shade is-play" />}</VideoFrame>
        </> : <>
          {variant === 'media' && <Solid name="pa-sheet is-back" depth={6} />}
          <PosterSheet />
        </>}
        {variant === 'media' && video && <Solid name="pa-play" depth={4} at={[play[0] - 22, play[1] - 22]}><span className="pa-play-mark" /></Solid>}
        {variant === 'secure' && <><span className="pa-band">대외비</span><Lock /></>}
        {variant === 'general' && <Lock open />}
        {typed && <Solid name="pa-glass" depth={3} faceStyle={glassMask(spots)}>
          {spots.map(spot => { const [left, top] = onGlass(spot.box);
            return <span key={spot.box.join()} className="pa-pick" style={{ left, top, width: spot.box[2], height: spot.box[3] }} />; })}
          {lensShade && <span className="pa-shade is-lens" style={{ left: lensShade[0] - 31, top: lensShade[1] - 31 }} />}
        </Solid>}
        {spots.map(spot => { const corner = variant === 'both' && spot.pair ? spot.pair : spot.check;
          return <Solid key={corner.join()} name="pa-check" depth={4} at={seen(corner, HEIGHT.check)} />; })}
        {lens && <Lens at={lens} show={variant} />}
      </span>
    </span>
  </span>;
}
