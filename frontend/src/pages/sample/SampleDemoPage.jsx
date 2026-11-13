import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { useOptionalAuth } from '../../auth/AuthProvider.jsx';
import { Icon } from '../../components/Icon.jsx';
import { useTilt } from '../../components/LayerFigure.jsx';
import { PixelField } from '../../components/PixelField.jsx';
import { reviewModeLabels, SecurityArt, securityModeLabels, settleArt, tiltArt, TypeArt, VideoTypeArt } from '../review/ReviewTypePicker.jsx';
import { LogoThumb, MediaArt, PlayIcon, SampleFrame, SamplePoster } from './SampleArt.jsx';
import {
  CAPTIONS, firstIssue, formatSeconds, LOGO_STEPS, MEDIA_LABELS, POSTER_SIZE, RESULTS, REVIEW_STEPS, runningView, sampleItems,
  TICK_MS, TOTAL_TICKS, VERDICT_LABELS, VIDEO,
} from './sampleData.js';
import './sampleDemo.css';

// 로그인 없이 둘러보는 샘플 체험. 자료 종류 → 보안 여부 → 검수 항목 → 자료 입력 → 진행 → 결과를 실제 화면과 같은 순서로 보여 준다.
// 고른 값은 서버로 보내지 않고, 진행 화면은 연출이며 결과는 sampleData.js에 미리 만든 예시다.
const START = { screen: 'media', media: null, security: null, mode: null, selected: null };

export default function SampleDemoPage() {
  const navigate = useNavigate();
  const auth = useOptionalAuth();
  const [state, setState] = useState(START);
  const [tick, setTick] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const update = patch => setState(previous => ({ ...previous, ...patch }));
  const { screen, media, security, mode } = state;
  const realPath = media === 'video' ? '/video' : '/review/new';
  const signedIn = auth?.status === 'authenticated';

  // 진행 화면: TICK_MS마다 한 칸씩 진행하고, 끝나면 결과 화면으로 넘어가며 첫 문제 항목을 펼친다.
  useEffect(() => {
    if (screen !== 'running') return undefined;
    const startedAt = Date.now();
    let count = 0;
    let finish = null;
    setTick(0); setElapsed(0);
    const timer = setInterval(() => {
      count += 1;
      setTick(count);
      setElapsed(Math.floor((Date.now() - startedAt) / 1000));
      if (count >= TOTAL_TICKS) {
        clearInterval(timer);
        finish = setTimeout(() => setState(previous => ({ ...previous, screen: 'results', selected: firstIssue(previous.media, previous.mode) })), 700);
      }
    }, TICK_MS);
    return () => { clearInterval(timer); clearTimeout(finish); };
  }, [screen]);

  // 화면이 바뀌면 새 화면 제목으로 초점을 옮긴다.
  useEffect(() => {
    document.getElementById('page-title')?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [screen]);

  const restart = () => setState(START);
  const reviewName = !media ? '새로운 검수 (샘플)' : media === 'video' ? '빛의 기록 · 숏폼 예고편 (샘플)' : '빛의 기록 · 전시 포스터 (샘플)';
  const steps = mode === 'logo' ? LOGO_STEPS : REVIEW_STEPS;
  const stepAt = screen === 'input' ? 0 : screen === 'running' ? 1 : 2;
  const context = <>
    {media && <span className="sd-kiosk-context">{MEDIA_LABELS[media]}</span>}
    {security && <span className="sd-kiosk-context">{security === 'secure' && <Icon name="lock" />}{securityModeLabels[security]}</span>}
  </>;

  return <div className="sd">
    <header className="sd-site">
      <span className="sd-brand"><svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5l14 14M19 5L5 19" stroke="#171717" strokeWidth="4" strokeLinecap="round" /></svg>
        Creative Checker<small>REVIEW STUDIO</small></span>
      <div className="sd-site-actions">
        <button type="button" className="sd-btn" onClick={() => navigate('/')}>체험 끝내기</button>
        <button type="button" className="sd-btn is-primary" onClick={() => navigate(realPath)}>{signedIn ? '실제 검수하기' : '로그인하고 실제 검수하기'}</button>
      </div>
    </header>
    <div className="sd-band"><span><b>샘플 체험</b>예시 자료로 검수 흐름을 둘러보는 화면입니다. 결과는 미리 만들어 둔 예시이고 서버로 아무것도 보내지 않아요.</span>
      <button type="button" className="sd-btn" onClick={restart}>처음부터 다시</button></div>
    <div className="sd-bar">
      <div className="sd-identity">
        <div className="sd-field"><span className="sd-label">검수 이름</span><span className="sd-name">{reviewName}</span></div>
        <div className="sd-field"><span className="sd-label">저장할 프로젝트</span><span className="sd-chip"><Icon name="check" />샘플 프로젝트</span></div>
      </div>
      {['input', 'running', 'results'].includes(screen) && <ol className="sd-steps" aria-label="검수 단계">
        {steps.map((label, index) => <li key={label} className={index === stepAt ? 'is-current' : index < stepAt ? 'is-done' : ''}
          aria-current={index === stepAt ? 'step' : undefined}><i>{String(index + 1).padStart(2, '0')}</i>{label}</li>)}
      </ol>}
    </div>
    <main className="sd-main">
      {screen === 'media' && <Kiosk title="어떤 샘플 자료를 검수할까요?" onPick={value => update({ media: value, screen: 'security' })}
        tiles={[{ value: 'poster', label: '포스터', art: <MediaArt kind="poster" /> }, { value: 'video', label: '동영상', art: <MediaArt kind="video" /> }]} />}
      {screen === 'security' && <Kiosk title="보안이 필요한 자료인가요?" context={context}
        back={{ label: '자료 종류 다시 고르기', onClick: restart }} onPick={value => update({ security: value, screen: 'type' })}
        tiles={[{ value: 'secure', label: securityModeLabels.secure, art: <SecurityArt secure media={media} /> },
          { value: 'general', label: securityModeLabels.general, art: <SecurityArt secure={false} media={media} /> }]} />}
      {screen === 'type' && <Kiosk title="검수할 항목을 선택해 주세요" context={context}
        back={{ label: '보안 여부 다시 고르기', onClick: () => update({ security: null, screen: 'security' }) }}
        onPick={value => update({ mode: value, screen: 'input' })}
        tiles={['text', 'logo', 'both'].map(value => ({ value, label: reviewModeLabels[value],
          art: media === 'video' ? <VideoTypeArt mode={value} /> : <TypeArt mode={value} /> }))} />}
      {screen === 'input' && <InputScreen state={state} onType={() => update({ mode: null, screen: 'type' })} onStart={() => update({ screen: 'running' })} />}
      {screen === 'running' && <RunningScreen state={state} tick={tick} elapsed={elapsed} />}
      {screen === 'results' && <ResultsScreen state={state} onSelect={id => update({ selected: id })} onReal={() => navigate(realPath)} />}
    </main>
  </div>;
}

// 키오스크처럼 큰 그림 타일만 두고, 누르면 바로 다음 화면으로 넘어간다. 숫자 키로도 고른다.
function Kiosk({ title, tiles, onPick, back, context }) {
  const latest = useRef({ tiles, onPick });
  latest.current = { tiles, onPick };
  useEffect(() => {
    function choose(event) {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      const index = Number(event.key) - 1;
      const tile = latest.current.tiles[index];
      if (!Number.isInteger(index) || !tile) return;
      event.preventDefault();
      latest.current.onPick(tile.value);
    }
    window.addEventListener('keydown', choose);
    return () => window.removeEventListener('keydown', choose);
  }, []);

  return <section className="sd-kiosk" aria-labelledby="page-title">
    {back && <button type="button" className="sd-kiosk-back" onClick={back.onClick}><span aria-hidden="true">←</span> {back.label}</button>}
    <PixelField monochrome />
    <div className="sd-kiosk-heading"><p className="op-eyebrow"><span aria-hidden="true" />SAMPLE / 샘플 체험{context}</p>
      <h1 id="page-title" tabIndex={-1}>{title}</h1></div>
    <div className="sd-tiles">
      {tiles.map(tile => <button key={tile.value} type="button" className="sd-tile" aria-label={tile.label} onClick={() => onPick(tile.value)}
        onPointerMove={tiltArt} onPointerLeave={settleArt}>
        <span className="op-tile-glow" aria-hidden="true" />
        <span className="sd-tile-art" aria-hidden="true">{tile.art}</span>
        <span className="sd-tile-label">{tile.label}</span>
      </button>)}
    </div>
    <p className="sd-kiosk-foot">샘플 체험에서는 어떤 걸 골라도 서버로 보내지 않아요</p>
  </section>;
}

function ModeRow({ security, mode }) {
  return <div className="sd-mode-row">
    <span className={`sd-mode-security${security === 'secure' ? ' is-secure' : ''}`}>{security === 'secure' && <Icon name="lock" />}{securityModeLabels[security]}</span>
    <strong>{reviewModeLabels[mode]}</strong>
  </div>;
}

function InputScreen({ state, onType, onStart }) {
  const { media, security, mode } = state;
  const video = media === 'video';
  const checksText = mode !== 'logo';
  const checksLogo = mode !== 'text';
  return <section aria-labelledby="page-title">
    <h1 id="page-title" className="sd-sr-only" tabIndex={-1}>{MEDIA_LABELS[media]} {reviewModeLabels[mode]} 자료 입력 (샘플)</h1>
    <ModeRow security={security} mode={mode} />
    <div className="sd-grid">
      <div className="sd-media">
        {video ? <>
          <div className="sd-stage sd-stage--large"><div className="sd-frame-wrap"><SampleFrame t={0} decorative={false} />
            <div className="sd-play"><span><PlayIcon /></span></div><span className="sd-duration">{formatSeconds(VIDEO.seconds)}</span></div></div>
          <div className="sd-filebar"><span><strong>{VIDEO.name}</strong><small>{VIDEO.size} · {formatSeconds(VIDEO.seconds)} · {VIDEO.width}×{VIDEO.height}</small></span></div>
        </> : <div className="sd-stage sd-stage--large"><div className="sd-poster-wrap"><SamplePoster /></div></div>}
      </div>
      <aside className="sd-panel sd-form" aria-label="검수 요구사항과 첨부 자료">
        {checksText && <>
          <div className="sd-block"><h2>검수 요구사항</h2>
            <div className="sd-req-text">{RESULTS[media].text.map(item => <p key={item.id}>{item.req}</p>)}</div></div>
          <div className="sd-block"><h2>요구사항 문서 첨부</h2>
            <div className="sd-file"><span className="sd-thumb">PDF</span>{video ? '숏폼_광고_요구사항.pdf' : '전시_홍보물_요구사항.pdf'}<small>예시 · 1쪽</small></div></div>
        </>}
        {checksLogo && <div className="sd-block"><h2>원본 로고 첨부</h2>
          <div className="sd-file"><span className="sd-thumb"><LogoThumb /></span>주최 로고.png<small>예시</small></div></div>}
        {checksText && video && <div className="sd-block"><details className="sd-ad"><summary>광고·협찬 설정</summary>
          <p>게시물 제목·캡션과 화면 문구로 광고 표시 규정을 판정합니다. 예시: 협찬 광고 · 캡션 “#빛의기록 #광고”</p></details></div>}
      </aside>
    </div>
    <div className="sd-actionbar">
      <button type="button" className="sd-btn" onClick={onType}>유형 바꾸기</button>
      <button type="button" className="sd-btn is-primary" onClick={onStart}>검수 시작하기<Icon name="arrow" /></button>
    </div>
  </section>;
}

// 실제 진행 화면처럼 포스터를 3D로 눕히고, 단계(depth 0~2)에 따라 유리판 '읽은 자리'·'판정'이 떠오른다. 무대는 마우스를 따라 조금 기운다.
function PosterScan({ depth = 0 }) {
  const tilt = useTilt();
  return <figure className="sd-scan" data-depth={depth}>
    <div className="sd-scan-stage" ref={tilt}><div className="sd-scan-sheet"><SamplePoster decorative />
      <span className="sd-scan-zoom"><SamplePoster decorative /></span><span className="sd-scan-lens" />
      <span className="sd-plane is-source" aria-hidden="true"><b>01 원본</b></span>
      <span className="sd-plane is-read" aria-hidden="true"><i /><b>02 읽은 자리</b></span>
      <span className="sd-plane is-verdict" aria-hidden="true"><b>03 판정</b></span>
      {[[0, 0], [100, 0], [0, 100], [100, 100]].map(([left, top]) => <span key={`${left}-${top}`} className="sd-post"
        aria-hidden="true" style={{ left: `${left}%`, top: `${top}%` }} />)}
    </div></div>
    <figcaption><span>검수 대상 포스터</span><span>첨부 이미지</span></figcaption>
  </figure>;
}

function FilmScanner({ scanPart, caption, depth = 0 }) {
  const tilt = useTilt();
  return <div className="sd-film-scanner" aria-hidden="true" data-depth={depth} ref={tilt}>
    <div className="sd-film"><div className="sd-film-track">
      <div className="sd-film-cells">{Array.from({ length: 8 }, (_, index) =>
        <span key={index} className={`sd-film-cell${scanPart * 8 > index + .2 ? ' is-read' : ''}`}><SampleFrame t={Math.round(index * 29 / 7)} /></span>)}</div>
      <div className="sd-film-lens" style={{ left: `${7 + scanPart * 86}%` }}>
        <span className="sd-film-lens-glass"><SampleFrame t={Math.round(scanPart * 29)} /><span className="sd-film-lens-scan" /></span>
        <span className="sd-film-lens-handle" /></div>
    </div></div>
    <div className="sd-film-scale"><span>0:00</span><span>{caption}</span><span>{formatSeconds(VIDEO.seconds)}</span></div>
  </div>;
}

function RunningScreen({ state, tick, elapsed }) {
  const { media, mode } = state;
  const video = media === 'video';
  const logoOnly = mode === 'logo';
  const view = runningView(tick, state);
  const time = formatSeconds(elapsed);
  // 유리판이 떠오르는 정도: 화면을 훑는 단계가 있으면 그 진행에, 없으면(포스터 문구 검수) 진행률에 맞춘다.
  const depth = video || logoOnly ? (view.scanPart >= 1 ? 2 : view.scanPart > 0 ? 1 : 0) : view.percent >= 67 ? 2 : view.percent >= 33 ? 1 : 0;
  const facts = logoOnly
    ? video ? [['영상 길이', formatSeconds(VIDEO.seconds)], ['해상도', `${VIDEO.width}×${VIDEO.height}`], ['원본 로고', '1개'], ['경과 시간', time]]
      : [['해상도', POSTER_SIZE], ['원본 로고', '1개'], ['검수 범위', '로고 위치 후보'], ['경과 시간', time]]
    : video ? [['영상 길이', formatSeconds(VIDEO.seconds)], ['해상도', `${VIDEO.width}×${VIDEO.height}`], ['검수 기준', '3개'], ['처리 속도', view.speed]]
      : [['해상도', POSTER_SIZE], ['검수 기준', '3개'], ['검수 범위', '문구·값 · 시각 조건'], ['경과 시간', time]];
  return <section className="sd-progress" aria-labelledby="page-title">
    {/* 진행 카드 전체를 알림 영역으로 두면 0.28초마다 바뀌는 숫자까지 읽어 무거워진다. 단계 제목이 바뀔 때만 알린다. */}
    <p className="sd-sr-only" role="status">{view.title}</p>
    <div className="sd-progress-card">
      {video ? <FilmScanner scanPart={view.scanPart} caption={view.filmCaption} depth={depth} /> : <PosterScan depth={depth} />}
      <span className="sd-eyebrow">{video ? 'VIDEO' : 'POSTER'}{logoOnly ? ' LOGO' : ''} REVIEW IN PROGRESS</span>
      <h1 id="page-title" tabIndex={-1}>{view.title}</h1>
      <p className="sd-progress-message">{view.message}</p>
      <div className="sd-progress-track" role="progressbar" aria-label="검수 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={view.percent}>
        <div style={{ width: `${view.percent}%` }} /></div>
      <div className="sd-progress-caption"><span>처리 단계 기준 진행률</span><strong>{view.percent}%</strong></div>
      <ol className="sd-progress-steps">{view.steps.map((step, index) => <li key={step.label} className={`is-${step.status}`}>
        <span>{step.status === 'completed' ? <Icon name="check" /> : String(index + 1).padStart(2, '0')}</span>{step.label}</li>)}</ol>
      <dl className="sd-progress-facts">{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      <p className="sd-progress-help">{video ? '완료되면 결과 화면으로 이동합니다. 실제 검수는 페이지를 닫아도 서버에서 계속됩니다.' : '완료되면 다음 화면이 표시됩니다. 페이지를 그대로 두셔도 됩니다.'}</p>
    </div>
    {mode === 'both' && <section className="sd-logo-card" aria-label="로고 검수 진행">
      <div className="sd-logo-head"><h2>{video ? '동영상 로고 검수' : '포스터 로고 검수'}</h2>
        <p>후보 찾기 → 위치 확정 → 정밀 대조 순서로 진행합니다. 기준 문구 검수와 별도이며, 로고 일치 여부는 사람이 판단합니다.</p></div>
      <div className="sd-logo-body">
        <div className="sd-logo-row"><strong>{video ? '빛의 기록 · 숏폼 예고편' : '빛의 기록 · 전시 포스터'} 로고 검수</strong><span className="sd-logo-state">{view.logo.state}</span></div>
        <div className="sd-logo-track"><div style={{ width: `${view.logo.percent}%` }} /></div>
        <p className="sd-logo-detail">{view.logo.detail}</p>
      </div>
    </section>}
  </section>;
}

// 근거 상자. 좌표(x1,y1,x2,y2)를 그림 크기(width·height) 대비 비율로 놓는다.
function EvidenceBoxes({ items, width, height, selected, onSelect }) {
  return items.map(item => {
    const [x1, y1, x2, y2] = item.box;
    return <button key={item.id} type="button" className={`sd-box is-${item.verdict}${selected === item.id ? ' is-selected' : ''}`}
      style={{ left: `${x1 / width * 100}%`, top: `${y1 / height * 100}%`, width: `${(x2 - x1) / width * 100}%`, height: `${(y2 - y1) / height * 100}%` }}
      aria-label={`${item.id.toUpperCase()} 근거 위치: ${item.req}`} aria-pressed={selected === item.id} onClick={() => onSelect(item.id)}>
      <span>{item.id.toUpperCase()}</span></button>;
  });
}

function ResultList({ items, labels, selected, onSelect }) {
  return <ul className="sd-items">{items.map((item, index) => <li key={item.id}>
    <button type="button" className={selected === item.id ? 'is-selected' : ''} aria-expanded={selected === item.id} onClick={() => onSelect(item.id)}>
      <span className="sd-index">{String(index + 1).padStart(2, '0')}</span><span>{item.req}</span>
      <span className={`sd-verdict is-${item.verdict}`}>{labels[item.verdict]}</span></button>
    {selected === item.id && <div className="sd-detail"><b>읽은 내용</b> {item.observed}<br /><b>판정 이유</b> {item.why}
      {item.span && <><br /><b>보인 시간 (표본 기준)</b> {formatSeconds(item.span[0])}–{formatSeconds(item.span[1])} · 대표 프레임 {formatSeconds(item.at)}</>}</div>}
  </li>)}</ul>;
}

function ResultsScreen({ state, onSelect, onReal }) {
  const { media, mode, selected } = state;
  const video = media === 'video';
  const items = sampleItems(media, mode);
  const labels = VERDICT_LABELS[media];
  const count = verdict => items.filter(item => item.verdict === verdict).length;
  // 포스터는 같은 항목을 다시 누르면 접고, 동영상은 늘 한 항목의 프레임을 보여 준다.
  const select = id => onSelect(!video && selected === id ? null : id);
  const logoCard = mode !== 'text' && <section className="sd-panel sd-logo-result" aria-label="로고 정밀 대조 예시">
    <div className="sd-panel-head"><strong>로고 정밀 대조 (예시)</strong><span>위치 확정 → 정밀 대조</span></div>
    <div className="sd-logo-result-row"><span className="sd-thumb"><LogoThumb /></span>
      주최 로고 · 확정한 위치 1곳 · {video ? '4초 묶음 · 선명도 낮음 근거 1건' : '형태 일치, 색 차이 근거 1건'}
      <span className="sd-verdict is-review">확인 필요</span></div>
  </section>;
  const cta = <div className="sd-cta"><div><strong>내 {video ? '동영상으로' : '포스터로'} 실제 검수를 해 볼까요?</strong>
    <p>로그인하면 같은 흐름으로 내 자료를 검수하고 결과·보고서를 저장할 수 있어요.</p></div>
    <button type="button" className="sd-btn" onClick={onReal}>실제 검수 시작하기</button></div>
  const heading = <h1 id="page-title" className="sd-sr-only" tabIndex={-1}>{MEDIA_LABELS[media]} 검수 결과 (샘플)</h1>;

  if (!video) {
    return <section aria-labelledby="page-title">{heading}
      <div className="sd-summary">{['pass', 'fail', 'review'].map(verdict => <span key={verdict} className="sd-pill">
        <span className={`sd-dot is-${verdict}`} />{labels[verdict]} {count(verdict)}</span>)}</div>
      <div className="sd-grid sd-grid--result">
        <div className="sd-media"><div className="sd-stage sd-stage--result"><div className="sd-poster-wrap"><SamplePoster />
          <EvidenceBoxes items={items} width={300} height={400} selected={selected} onSelect={select} /></div></div></div>
        <aside className="sd-panel" aria-label="검수 결과"><div className="sd-panel-head"><strong>검수 결과</strong><span>{items.length}개 항목</span></div>
          <ResultList items={items} labels={labels} selected={selected} onSelect={select} /></aside>
      </div>
      {logoCard}{cta}
    </section>;
  }

  // 동영상 결과: 근거 프레임 + 구간 타임라인 / 검수 요구사항 목록(실제 VideoResults.jsx와 같은 배치).
  const current = items.find(item => item.id === selected) || items[0];
  const at = current?.at ?? 0;
  const percent = seconds => `${seconds / VIDEO.seconds * 100}%`;
  return <section aria-labelledby="page-title">{heading}
    <p className="sd-notice">자동 검수를 완료했어요. 판정은 검사한 표본 시각을 기준으로 합니다.</p>
    <div className="sd-grid sd-grid--result">
      <div>
        <div className="sd-media"><div className="sd-stage sd-stage--video"><div className="sd-frame-wrap"><SampleFrame t={at} decorative={false} />
          {current && <EvidenceBoxes items={[current]} width={320} height={180} selected={current.id} onSelect={select} />}
          <span className="sd-frame-chip">{formatSeconds(at)} · 1초 표본{current ? ` · ${current.id.toUpperCase()}` : ''}</span></div></div></div>
        <section className="sd-panel sd-timeline" aria-label="구간 타임라인">
          <div className="sd-timeline-head">구간 타임라인<span>{formatSeconds(at)} / {formatSeconds(VIDEO.seconds)}</span></div>
          <div className="sd-timeline-track" aria-hidden="true">
            {CAPTIONS.map(caption => <span key={caption.text} className="sd-timeline-segment" style={{ left: percent(caption.from), width: percent(caption.to - caption.from) }} />)}
            {items.map(item => <span key={item.id} className={`sd-timeline-segment is-evidence is-${item.verdict}${item.id === current?.id ? '' : ' is-dim'}`}
              style={{ left: percent(item.span[0]), width: percent(item.span[1] - item.span[0]) }} />)}
            <span className="sd-timeline-head-line" style={{ left: percent(at) }} />
          </div>
          <div className="sd-timeline-scale" aria-hidden="true">{[0, .25, .5, .75, 1].map(part => <span key={part}>{formatSeconds(VIDEO.seconds * part)}</span>)}</div>
          <div className="sd-timeline-shortcuts"><span>근거 시점</span>{items.map(item => <button key={item.id} type="button"
            className={item.id === current?.id ? 'is-on' : ''} aria-pressed={item.id === current?.id} onClick={() => select(item.id)}>
            {formatSeconds(item.at)} {item.id.toUpperCase()}</button>)}</div>
        </section>
      </div>
      <aside className="sd-panel" aria-label="검수 요구사항">
        <div className="sd-panel-head is-stacked"><strong>검수 요구사항</strong><span>항목을 펼치면 판정 근거가 표시됩니다. · {items.length}개 항목</span></div>
        <div className="sd-filters" aria-hidden="true"><span className="is-on">전체 <b>{items.length}</b></span>
          {['pass', 'review', 'fail'].map(verdict => <span key={verdict}>{labels[verdict]} <b>{count(verdict)}</b></span>)}</div>
        <ResultList items={items} labels={labels} selected={current?.id} onSelect={select} />
      </aside>
    </div>
    {logoCard}{cta}
  </section>;
}
