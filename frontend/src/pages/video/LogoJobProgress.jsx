import React, { useRef, useState } from 'react';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { logoProgressSteps } from './LogoFlowParts.jsx';
import '../../styles/review-progress.css';
import './videoReview.css';

// 로고 검수의 진행 화면(2026-11-17). 종합 검수 진행 화면(VideoProgress.jsx)과 같은 짜임을 쓴다:
// 왼쪽 잉크 무대에 검수 대상을 눕히고 돋보기가 진행률만큼 따라가며, 돋보기에는 찾고 있는 원본 로고를 담는다.
// 오른쪽 종이 면에는 제목·큰 진행률·단계·작업 정보를 선으로 나눠 쌓는다. 단계는 01 후보 찾기 · 02 정밀 대조다.
// 무대는 검수 대상에 맞춘다: 포스터 로고(media='poster')는 포스터 한 장을 눕혀 탐지 타일을 차례로 밝히고(PosterScanner),
// 동영상 로고(media='video')는 1초마다 뽑은 표본 프레임을 카드로 포개 놓고 한 장씩 띄워 살핀다(FrameDeckScanner, 2026-11-18).
// 포스터도 후보를 찾으면 순위 높은 후보로 정밀 대조까지 자동으로 하므로 단계는 동영상과 같이 01 후보 찾기 · 02 정밀 대조다.
// 포스터 · 동영상 모두, 처음 접수한 작업의 자동 진행은 이 화면 하나가 이어서 보인다(2026-11-18): 후보를 찾은 뒤(status=candidates_ready)에도
// 자동 정밀 대조가 끝날 때까지 02 단계의 진행률을 보인다. 정밀 대조 진행 화면이 이어서 따로 뜨면 진행 화면이 두 번 나와 오류처럼 보였다.
// 위치를 직접 확인하고 다시 대조하는 재검수만 정밀 대조 화면(CompareProgress)이 보여 준다.
// 무대의 타일·프레임 카드는 진행 정도만 알리는 장식이라 낭독기에서 숨긴다.
const DECK_CARDS = 8;                 // 무대에 놓는 프레임 카드 수
const DECK_PICKS = [1, 4, 6];         // 정밀 대조 단계에서 차례로 띄우는 카드(어느 프레임인지는 장식이다)
const stepStatusText = { completed: '완료', active: '진행 중', failed: '멈춤', pending: '대기' };
const percent = value => `${Math.min(100, Math.max(0, Math.round(value || 0)))}%`;
// 정밀 대조 단계의 문구. 대조 대상(포스터 · 영상)만 바꿔 쓴다.
const COMPARE_TEXT = {
  title: subject => `원본 로고와 ${subject} 속 로고를 정밀 대조하고 있어요.`,
  waiting: '정밀 대조 차례를 기다리고 있어요.',
  detail: '고른 위치를 원본 로고와 비교하고 있습니다.',
  caption: '원본과 비교하는 중',
  help: '끝나면 이 화면에 결과가 바로 나타나요. 페이지를 닫아도 서버에서 계속 대조합니다.',
};
const TEXT = {
  video: {
    stageTitles: { queued: '로고 검수 순서를 기다리고 있어요.', frames: '영상에서 표본 프레임을 뽑고 있어요.' },
    searching: '영상에서 로고 후보를 찾고 있어요.',
    captions: { waiting: '순서 대기', preparing: '표본 프레임 준비', searching: '프레임별 후보 찾는 중', failed: '처리 멈춤' },
    detail: openai => openai ? 'OpenAI로 프레임별 위치를 찾고 있습니다.' : '영상의 프레임마다 로고 후보를 찾고 있습니다.',
    help: '후보를 찾으면 순위가 높은 후보로 정밀 대조까지 이어서 합니다. 페이지를 닫아도 서버에서 검수를 계속합니다.',
    compare: '검수 서버 · 자동',
    back: linked => linked ? '동영상 검수로 돌아가기' : '자료 입력에서 다시 접수',
    subject: '영상',
  },
  poster: {
    stageTitles: { queued: '로고 검수 순서를 기다리고 있어요.' },
    searching: '포스터에서 로고 후보를 찾고 있어요.',
    captions: { waiting: '순서 대기', preparing: '포스터 준비', searching: '포스터를 나눠 후보 찾는 중', failed: '처리 멈춤' },
    detail: openai => openai ? 'OpenAI로 포스터에서 위치를 찾고 있습니다.' : '포스터를 구역별로 나눠 로고 후보를 찾고 있습니다.',
    help: '후보를 찾으면 순위가 높은 후보로 정밀 대조까지 이어서 합니다. 페이지를 닫아도 서버에서 검수를 계속합니다.',
    compare: '검수 서버 · 자동',
    back: linked => linked ? '포스터 검수로 돌아가기' : '자료 입력에서 다시 접수',
    subject: '포스터',
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

// 동영상용 무대(2026-11-18): 영상에서 1초마다 뽑은 표본 프레임을 카드로 놓고, 시간 순서대로 조금씩 밀어 포갠다.
// 지금 살피는 프레임은 더미에서 떠오르고 돋보기(원본 로고)가 그 위에서 훑는다. 살핀 프레임은 밝게 남는다.
// 정밀 대조 단계(comparing)에서는 프레임을 모두 살핀 모습으로 두고, 몇 장을 차례로 띄워 원본과 견주는 모습을 보인다.
// 카드에는 실제 표본 프레임(video/f/<초>.jpg, 앞쪽 몇 초)을 싣는다. 아직 없으면(대기 · 프레임 준비 중이거나 영상이 짧으면) 빈 카드로 둔다.
// 카드 모양은 처음 읽힌 프레임의 가로세로 비율을 따른다(알기 전에는 세로 영상).
function FrameDeckScanner({ base, logos, phase, value, caption }) {
  const [ratio, setRatio] = useState(null);
  const comparing = phase === 'comparing';
  const searching = phase === 'searching';
  const position = Math.min(1, value / 100);
  const current = searching ? Math.min(DECK_CARDS - 1, Math.floor(position * DECK_CARDS))
    : comparing ? DECK_PICKS[Math.min(DECK_PICKS.length - 1, Math.floor(position * DECK_PICKS.length))] : -1;
  const ref = !logos.length ? null : comparing ? Math.min(logos.length - 1, Math.floor(position * logos.length)) : Math.max(0, current) % logos.length;
  const measure = event => {
    const { naturalWidth: width, naturalHeight: height } = event.currentTarget;
    if (width && height) setRatio(known => known || Math.min(1.9, Math.max(0.5, width / height)));
  };
  return <div className={`rv-video-scanner rv-logo-scanner vl-deck-scanner is-${comparing ? 'searching is-comparing' : phase}`} aria-hidden="true">
    <div className={`vl-deck${ratio > 1 ? ' is-wide' : ''}`} style={{ '--n': DECK_CARDS, '--at': Math.max(0, current), '--ratio': ratio || 0.5625 }}>
      {Array.from({ length: DECK_CARDS }, (_, index) => <span key={index} style={{ '--i': index }}
        className={`vl-deck-card${comparing || index < current ? ' is-read' : ''}${index === current ? ' is-current' : ''}`}>
        {(searching || comparing) && <img src={`${base}/video/f/${index}.jpg`} alt="" onLoad={measure}
          onError={event => { event.currentTarget.style.visibility = 'hidden'; }} />}
      </span>)}
      <div className="rv-video-lens">
        <span className="rv-video-lens-glass">
          {ref != null && <img className="rv-logo-lens-ref" src={`${base}/refs/${ref}.png`} alt="" />}
          <span className="rv-video-lens-scan" />
          {phase === 'failed' && <Icon name="warning" />}
        </span>
        <span className="rv-video-lens-handle" />
      </div>
    </div>
    <div className="rv-video-scanner-scale"><span>원본 로고 {logos.length}개</span><span>{caption}</span><span>{value}%</span></div>
  </div>;
}

// 포스터용 무대(2026-11-17): 포스터 한 장을 눕혀 두고 탐지 타일을 차례로 밝힌다. 돋보기는 지금 보는 타일 위에 있다.
function PosterScanner({ base, logos, phase, value, caption, tiles, size }) {
  // 타일 수는 후보 찾기 안내('타일 5/11')에서 안다. 정밀 대조로 넘어가도 같은 격자를 유지하도록 마지막으로 안 값을 기억한다.
  const knownTotal = useRef(null);
  if (tiles) knownTotal.current = tiles.total;
  const total = tiles?.total || knownTotal.current || 12;
  const ratio = size ? size[0] / size[1] : 0.72;
  const cols = Math.max(2, Math.round(Math.sqrt(total * ratio)));
  const rows = Math.ceil(total / cols);
  // 정밀 대조 단계(comparing): 후보 찾기는 끝났으므로 타일을 모두 읽은 모습으로 두고, 돋보기는 가운데에서 원본 로고를 훑는다.
  // 원본 로고가 여러 개면 진행률에 맞춰 차례로 보인다. 훑는 선은 후보 찾기와 같은 모양(is-searching)을 쓴다.
  const comparing = phase === 'comparing';
  const done = tiles ? tiles.done : Math.floor(value / 100 * total);
  const current = phase === 'searching' ? Math.min(total - 1, done) : -1;
  const ref = !logos.length ? null : comparing ? Math.min(logos.length - 1, Math.floor(value / 100 * logos.length)) : Math.max(0, current) % logos.length;
  const col = current >= 0 ? current % cols : (cols - 1) / 2, row = current >= 0 ? Math.floor(current / cols) : (rows - 1) / 2;
  return <div className={`rv-video-scanner rv-logo-scanner pl-scanner is-${comparing ? 'searching is-comparing' : phase}`} aria-hidden="true">
    <div className="pl-scan-sheet" style={{ aspectRatio: size ? size[0] + ' / ' + size[1] : '3 / 4', '--cols': cols, '--rows': rows }}>
      <img src={`${base}/poster.jpg`} alt="" onError={event => { event.currentTarget.style.visibility = 'hidden'; }} />
      <div className="pl-scan-tiles">{Array.from({ length: cols * rows }, (_, index) => <span key={index}
        className={`pl-scan-tile${comparing || index < current ? ' is-read' : ''}${index === current ? ' is-current' : ''}`} />)}</div>
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
  const found = meta.status === 'candidates_ready';
  const candidates = meta.status === 'failed' ? 'failed' : found ? 'completed' : 'active';
  const compare = !found ? 'pending' : meta.compare_status === 'completed' ? 'completed' : meta.compare_status === 'failed' ? 'failed'
    : meta.compare_status ? 'active' : 'pending';
  return [
    { key: 'logo_candidates', label: '로고 후보 찾기', status: candidates, detail: candidates === 'active' ? (meta.status === 'queued' ? '대기' : percent(meta.progress)) : undefined,
      title: (!found && meta.stage_detail) || '포스터 전체와 나눈 타일에서 로고 후보를 찾습니다.' },
    { key: 'logo_compare', label: '로고 정밀 대조', status: compare,
      detail: compare === 'active' ? (meta.compare_status === 'queued' ? '대기' : percent(meta.compare_progress)) : undefined,
      title: '순위가 높은 후보를 원본 로고와 대조합니다. 최종 판정은 담당자가 합니다.' },
  ];
}

// meta: /api/video-logos/{id} · /api/poster-logos/{id} 응답(후보 찾기 중이거나 실패한 작업, 자동 정밀 대조 중인 작업).
// linkedReview: 동영상·포스터 검수에 딸린 작업인가 · onRestart: 다시 접수할 화면으로 돌아가기.
export default function LogoJobProgress({ base, meta, linkedReview, onRestart, media = 'video' }) {
  const text = TEXT[media] || TEXT.video;
  const failed = meta.status === 'failed';
  const openai = meta.detector === 'openai';
  const logos = meta.logos || [];
  // 후보를 찾은 작업을 넘겨받으면 02 정밀 대조 단계다. 진행률은 그 단계의 서버 값(compare_progress)을 그대로 쓴다.
  const comparing = meta.status === 'candidates_ready';
  const value = Math.min(100, Math.max(0, Math.round((comparing ? meta.compare_progress : meta.progress) || 0)));
  const phase = failed ? 'failed' : comparing ? 'comparing' : meta.status === 'queued' ? 'waiting' : meta.stage === 'frames' ? 'preparing' : 'searching';
  const title = failed ? '로고 후보 찾기가 멈췄어요.'
    : comparing ? (meta.compare_status === 'running' ? COMPARE_TEXT.title(text.subject) : COMPARE_TEXT.waiting)
      : text.stageTitles[meta.stage] || text.searching;
  const detail = failed ? '멈춘 단계: 로고 후보 찾기' : comparing ? COMPARE_TEXT.detail : meta.stage_detail || text.detail(openai);
  const stepName = comparing ? '로고 정밀 대조' : '로고 후보 찾기';
  const steps = media === 'poster' ? posterSteps(meta) : logoProgressSteps([meta]);
  const tiles = media === 'poster' && phase === 'searching' ? tileProgress(meta.stage_detail) : null;
  const size = meta.poster?.size;
  const facts = [
    ['원본 로고', `${logos.length}개`],
    ['위치 찾기', openai ? 'OpenAI' : '검수 서버'],
    ['정밀 대조', text.compare],
    ['접수', receivedAt(meta.created_at)],
  ];
  return <div className="rv-video rv-logo-progress">
    <section className="rv-progress-page rv-video-progress" aria-labelledby="logo-progress-title">
      <div className={`rv-progress-card rv-video-progress-card is-${phase}`}>
        {media === 'poster'
          ? <PosterScanner base={base} logos={logos} phase={phase} value={value} caption={comparing ? COMPARE_TEXT.caption : text.captions[phase]} tiles={tiles} size={size} />
          : <FrameDeckScanner base={base} logos={logos} phase={phase} value={value} caption={comparing ? COMPARE_TEXT.caption : text.captions[phase]} />}
        <span className="rv-eyebrow">{failed ? 'REVIEW STOPPED' : 'LOGO REVIEW IN PROGRESS'}</span>
        <h1 id="logo-progress-title" tabIndex={-1}>{title}</h1>
        <span className="sr-only" role="status">{failed ? '로고 후보 찾기 단계에서 멈췄습니다.' : `${stepName} 단계 진행 중`}</span>
        <p className="rv-progress-message">{detail}</p>
        <div className="rv-progress-track" role="progressbar" aria-label="로고 검수 진행률" aria-valuemin={0} aria-valuemax={100}
          aria-valuenow={value} aria-valuetext={`${value}% · ${title}`}>
          <div className="rv-progress-fill" style={{ width: `${value}%` }} />
        </div>
        <div className="rv-progress-caption"><span>{failed ? '멈춘 시점의 후보 찾기 진행률' : `${stepName} 진행률`}</span><strong>{value}%</strong></div>
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
        </> : <p className="rv-progress-help">{comparing ? COMPARE_TEXT.help : text.help}</p>}
      </div>
    </section>
  </div>;
}
