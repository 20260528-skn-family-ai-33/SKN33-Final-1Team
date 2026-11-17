import React from 'react';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { logoProgressSteps } from './LogoFlowParts.jsx';
import '../../styles/review-progress.css';
import './videoReview.css';

// 로고 검수의 후보 찾기 진행 화면(2026-11-17). 종합 검수 진행 화면(VideoProgress.jsx)과 같은 짜임을 쓴다:
// 왼쪽 잉크 무대에 띠를 눕히고 돋보기가 진행률만큼 따라가며, 돋보기에는 찾고 있는 원본 로고를 담는다.
// 오른쪽 종이 면에는 제목·큰 진행률·단계·작업 정보를 선으로 나눠 쌓는다.
// 동영상 로고(media='video')는 띠가 1초 프레임이고 단계가 01 후보 찾기 · 02 정밀 대조다.
// 포스터 로고(media='poster', PosterLogoPage)도 오른쪽 구성은 같고, 무대만 필름 띠 대신 포스터 한 장을 눕혀 탐지 타일을 차례로 밝힌다(PosterScanner).
// 포스터도 후보를 찾으면 순위 높은 후보로 정밀 대조까지 자동으로 하므로 단계는 동영상과 같이 01 후보 찾기 · 02 정밀 대조다.
// 띠 칸·타일은 진행 정도만 알리는 장식이라 낭독기에서 숨긴다.
const FILM_CELLS = 12;
const stepStatusText = { completed: '완료', active: '진행 중', failed: '멈춤', pending: '대기' };
const percent = value => `${Math.min(100, Math.max(0, Math.round(value || 0)))}%`;
const TEXT = {
  video: {
    stageTitles: { queued: '로고 검수 순서를 기다리고 있어요.', frames: '영상에서 표본 프레임을 뽑고 있어요.' },
    searching: '영상에서 로고 후보를 찾고 있어요.',
    captions: { waiting: '순서 대기', preparing: '표본 프레임 준비', searching: '프레임별 후보 찾는 중', failed: '처리 멈춤' },
    detail: openai => openai ? 'OpenAI로 프레임별 위치를 찾고 있습니다.' : 'RunPod에서 프레임별 후보를 찾고 있습니다.',
    help: '후보를 찾으면 정밀 대조 화면으로 넘어갑니다. 페이지를 닫아도 서버에서 검수를 계속합니다.',
    compare: 'RunPod · 자동',
    back: linked => linked ? '동영상 검수로 돌아가기' : '자료 입력에서 다시 접수',
  },
  poster: {
    stageTitles: { queued: '로고 검수 순서를 기다리고 있어요.' },
    searching: '포스터에서 로고 후보를 찾고 있어요.',
    captions: { waiting: '순서 대기', preparing: '포스터 준비', searching: '포스터를 나눠 후보 찾는 중', failed: '처리 멈춤' },
    detail: openai => openai ? 'OpenAI로 포스터에서 위치를 찾고 있습니다.' : 'RunPod에서 포스터를 타일로 나눠 후보를 찾고 있습니다.',
    help: '후보를 찾으면 순위가 높은 후보로 정밀 대조까지 이어서 합니다. 페이지를 닫아도 서버에서 검수를 계속합니다.',
    compare: 'RunPod · 자동',
    back: linked => linked ? '포스터 검수로 돌아가기' : '자료 입력에서 다시 접수',
  },
};

function receivedAt(value) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime())
    ? date.toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '확인 중';
}

// 포스터 후보 찾기는 '후보 탐지 타일 5/11'처럼 타일 수를 알린다. 알 수 있으면 띠 칸 수와 지금 칸을 그 값에 맞춘다.
function tileProgress(detail) {
  const match = /타일\s*(\d+)\s*\/\s*(\d+)/.exec(detail || '');
  return match && Number(match[2]) > 0 ? { done: Math.min(Number(match[1]), Number(match[2])), total: Math.min(18, Number(match[2])) } : null;
}

function LogoScanner({ base, logos, phase, value, caption, tiles }) {
  const cells = tiles?.total || FILM_CELLS;
  const position = tiles ? tiles.done / Math.max(1, tiles.total) : value / 100;
  const current = phase === 'searching' ? Math.min(cells - 1, Math.floor(position * cells)) : -1;
  // 원본 로고가 여러 개면 돋보기가 칸을 옮길 때마다 다음 로고를 보인다.
  const ref = logos.length ? Math.max(0, current) % logos.length : null;
  return <div className={`rv-video-scanner rv-logo-scanner is-${phase}`} aria-hidden="true"
    style={{ '--cell-h': '61px', '--cell-gap': '5px', '--cells': cells }}>
    <div className="rv-video-film">
      <div className="rv-video-film-track">
        <div className="rv-video-film-cells">{Array.from({ length: cells }, (_, index) => <span key={index} style={{ '--i': index }}
          className={`rv-video-film-cell${index < current ? ' is-read' : ''}${index === current ? ' is-current' : ''}`} />)}</div>
        <div className="rv-video-lens" style={{ left: `${current >= 0 ? 7 + Math.min(1, position) * 86 : 7}%` }}>
          <span className="rv-video-lens-glass">
            {ref != null && <img className="rv-logo-lens-ref" src={`${base}/refs/${ref}.png`} alt="" />}
            <span className="rv-video-lens-scan" />
            {phase === 'failed' && <Icon name="warning" />}
          </span>
          <span className="rv-video-lens-handle" />
        </div>
      </div>
    </div>
    <div className="rv-video-scanner-scale"><span>원본 로고 {logos.length}개</span><span>{caption}</span><span>{tiles ? `타일 ${tiles.done}/${tiles.total}` : `${value}%`}</span></div>
  </div>;
}

// 포스터용 무대(2026-11-17): 포스터 한 장을 눕혀 두고 탐지 타일을 차례로 밝힌다. 돋보기는 지금 보는 타일 위에 있다.
function PosterScanner({ base, logos, phase, value, caption, tiles, size }) {
  const total = tiles?.total || 12;
  const ratio = size ? size[0] / size[1] : 0.72;
  const cols = Math.max(2, Math.round(Math.sqrt(total * ratio)));
  const rows = Math.ceil(total / cols);
  const done = tiles ? tiles.done : Math.floor(value / 100 * total);
  const current = phase === 'searching' ? Math.min(total - 1, done) : -1;
  const ref = logos.length ? Math.max(0, current) % logos.length : null;
  const col = current >= 0 ? current % cols : (cols - 1) / 2, row = current >= 0 ? Math.floor(current / cols) : (rows - 1) / 2;
  return <div className={`rv-video-scanner rv-logo-scanner pl-scanner is-${phase}`} aria-hidden="true">
    <div className="pl-scan-sheet" style={{ aspectRatio: size ? size[0] + ' / ' + size[1] : '3 / 4', '--cols': cols, '--rows': rows }}>
      <img src={`${base}/poster.jpg`} alt="" onError={event => { event.currentTarget.style.visibility = 'hidden'; }} />
      <div className="pl-scan-tiles">{Array.from({ length: cols * rows }, (_, index) => <span key={index}
        className={`pl-scan-tile${index < current ? ' is-read' : ''}${index === current ? ' is-current' : ''}`} />)}</div>
      <div className="rv-video-lens" style={{ left: `${(col + 0.5) / cols * 100}%`, top: `${(row + 0.5) / rows * 100}%` }}>
        <span className="rv-video-lens-glass">
          {ref != null && <img className="rv-logo-lens-ref" src={`${base}/refs/${ref}.png`} alt="" />}
          <span className="rv-video-lens-scan" />
          {phase === 'failed' && <Icon name="warning" />}
        </span>
        <span className="rv-video-lens-handle" />
      </div>
    </div>
    <div className="rv-video-scanner-scale"><span>원본 로고 {logos.length}개</span><span>{caption}</span><span>{tiles ? `${tiles.done} / ${tiles.total}` : `${value}%`}</span></div>
  </div>;
}

// 포스터 로고의 단계: 후보 찾기 → 정밀 대조(자동). 위치 직접 확인은 결과를 본 뒤 필요할 때만 여는 화면이라 단계에 넣지 않는다.
function posterSteps(meta) {
  const candidates = meta.status === 'failed' ? 'failed' : meta.status === 'candidates_ready' ? 'completed' : 'active';
  return [
    { key: 'logo_candidates', label: '로고 후보 찾기', status: candidates, detail: candidates === 'active' ? (meta.status === 'queued' ? '대기' : percent(meta.progress)) : undefined,
      title: meta.stage_detail || '포스터 전체와 나눈 타일에서 로고 후보를 찾습니다.' },
    { key: 'logo_compare', label: '로고 정밀 대조', status: 'pending', title: '순위가 높은 후보를 원본 로고와 대조합니다. 최종 판정은 담당자가 합니다.' },
  ];
}

// meta: /api/video-logos/{id} · /api/poster-logos/{id} 응답(후보 찾기 중이거나 실패한 작업).
// linkedReview: 동영상·포스터 검수에 딸린 작업인가 · onRestart: 다시 접수할 화면으로 돌아가기.
export default function LogoJobProgress({ base, meta, linkedReview, onRestart, media = 'video' }) {
  const text = TEXT[media] || TEXT.video;
  const failed = meta.status === 'failed';
  const openai = meta.detector === 'openai';
  const logos = meta.logos || [];
  const value = Math.min(100, Math.max(0, Math.round(meta.progress || 0)));
  const phase = failed ? 'failed' : meta.status === 'queued' ? 'waiting' : meta.stage === 'frames' ? 'preparing' : 'searching';
  const title = failed ? '로고 후보 찾기가 멈췄어요.' : text.stageTitles[meta.stage] || text.searching;
  const detail = failed ? '멈춘 단계: 로고 후보 찾기' : meta.stage_detail || text.detail(openai);
  const steps = media === 'poster' ? posterSteps(meta) : logoProgressSteps([meta]);
  const tiles = media === 'poster' && phase === 'searching' ? tileProgress(meta.stage_detail) : null;
  const size = meta.poster?.size;
  const facts = [
    ['원본 로고', `${logos.length}개`],
    ['위치 찾기', openai ? 'OpenAI' : 'RunPod'],
    ['정밀 대조', text.compare],
    ['접수', receivedAt(meta.created_at)],
  ];
  return <div className="rv-video rv-logo-progress">
    <section className="rv-progress-page rv-video-progress" aria-labelledby="logo-progress-title">
      <div className={`rv-progress-card rv-video-progress-card is-${phase}`}>
        {media === 'poster'
          ? <PosterScanner base={base} logos={logos} phase={phase} value={value} caption={text.captions[phase]} tiles={tiles} size={size} />
          : <LogoScanner base={base} logos={logos} phase={phase} value={value} caption={text.captions[phase]} tiles={tiles} />}
        <span className="rv-eyebrow">{failed ? 'REVIEW STOPPED' : 'LOGO REVIEW IN PROGRESS'}</span>
        <h1 id="logo-progress-title" tabIndex={-1}>{title}</h1>
        <span className="sr-only" role="status">{failed ? '로고 후보 찾기 단계에서 멈췄습니다.' : '로고 후보 찾기 단계 진행 중'}</span>
        <p className="rv-progress-message">{detail}</p>
        <div className="rv-progress-track" role="progressbar" aria-label="로고 검수 진행률" aria-valuemin={0} aria-valuemax={100}
          aria-valuenow={value} aria-valuetext={`${value}% · ${title}`}>
          <div className="rv-progress-fill" style={{ width: `${value}%` }} />
        </div>
        <div className="rv-progress-caption"><span>{failed ? '멈춘 시점의 후보 찾기 진행률' : '로고 후보 찾기 진행률'}</span><strong>{value}%</strong></div>
        <ol className="rv-progress-steps rv-video-steps" aria-label="처리 단계">{steps.map((step, i) => <li key={step.key} className={`is-${step.status}`}
          aria-current={step.status === 'active' ? 'step' : undefined} title={step.title}>
          <span aria-hidden="true">{step.status === 'completed' ? <Icon name="check" /> : step.status === 'failed' ? <Icon name="warning" /> : String(i + 1).padStart(2, '0')}</span>
          {step.label}<span className="sr-only"> {stepStatusText[step.status]}</span>
          {step.detail && <small>{step.detail}</small>}
        </li>)}</ol>
        <dl className="rv-video-facts">{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
        {failed ? <>
          <ErrorMessage message={meta.error || '후보 찾기에 실패했습니다.'} />
          <div className="rv-progress-actions"><button type="button" className="rv-button primary" onClick={onRestart}>
            {text.back(linkedReview)}<Icon name="arrow" /></button></div>
        </> : <p className="rv-progress-help">{text.help}</p>}
      </div>
    </section>
  </div>;
}
