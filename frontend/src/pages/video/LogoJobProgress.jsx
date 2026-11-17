import React from 'react';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { logoProgressSteps } from './LogoFlowParts.jsx';
import '../../styles/review-progress.css';
import './videoReview.css';

// 로고 검수의 후보 찾기 진행 화면(2026-11-17). 종합 검수 진행 화면(VideoProgress.jsx)과 같은 짜임을 쓴다:
// 왼쪽 잉크 무대에 필름 띠를 눕히고 돋보기가 진행률만큼 따라가며, 돋보기에는 찾고 있는 원본 로고를 담는다.
// 오른쪽 종이 면에는 제목·큰 진행률·단계(01 후보 찾기, 02 정밀 대조)·작업 정보를 선으로 나눠 쌓는다.
// 필름 칸은 실제 프레임이 아니라 진행 정도만 알리는 장식이라 낭독기에서 숨긴다.
const FILM_CELLS = 12;
const stepStatusText = { completed: '완료', active: '진행 중', failed: '멈춤', pending: '대기' };
const stageTitles = { queued: '로고 검수 순서를 기다리고 있어요.', frames: '영상에서 표본 프레임을 뽑고 있어요.' };
const phaseCaptions = { waiting: '순서 대기', preparing: '표본 프레임 준비', searching: '프레임별 후보 찾는 중', failed: '처리 멈춤' };

function receivedAt(value) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime())
    ? date.toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '확인 중';
}

function LogoScanner({ base, logos, phase, value }) {
  const position = value / 100;
  const current = phase === 'searching' ? Math.min(FILM_CELLS - 1, Math.floor(position * FILM_CELLS)) : -1;
  // 원본 로고가 여러 개면 돋보기가 칸을 옮길 때마다 다음 로고를 보인다.
  const ref = logos.length ? Math.max(0, current) % logos.length : null;
  return <div className={`rv-video-scanner rv-logo-scanner is-${phase}`} aria-hidden="true"
    style={{ '--cell-h': '61px', '--cell-gap': '5px', '--cells': FILM_CELLS }}>
    <div className="rv-video-film">
      <div className="rv-video-film-track">
        <div className="rv-video-film-cells">{Array.from({ length: FILM_CELLS }, (_, index) => <span key={index} style={{ '--i': index }}
          className={`rv-video-film-cell${index < current ? ' is-read' : ''}${index === current ? ' is-current' : ''}`} />)}</div>
        <div className="rv-video-lens" style={{ left: `${current >= 0 ? 7 + position * 86 : 7}%` }}>
          <span className="rv-video-lens-glass">
            {ref != null && <img className="rv-logo-lens-ref" src={`${base}/refs/${ref}.png`} alt="" />}
            <span className="rv-video-lens-scan" />
            {phase === 'failed' && <Icon name="warning" />}
          </span>
          <span className="rv-video-lens-handle" />
        </div>
      </div>
    </div>
    <div className="rv-video-scanner-scale"><span>원본 로고 {logos.length}개</span><span>{phaseCaptions[phase]}</span><span>{value}%</span></div>
  </div>;
}

// meta: /api/video-logos/{id} 응답(후보 찾기 중이거나 실패한 작업). onRestart: 다시 접수할 화면으로 돌아가기.
export default function LogoJobProgress({ base, meta, linkedReview, onRestart }) {
  const failed = meta.status === 'failed';
  const openai = meta.detector === 'openai';
  const logos = meta.logos || [];
  const value = Math.min(100, Math.max(0, Math.round(meta.progress || 0)));
  const phase = failed ? 'failed' : meta.status === 'queued' ? 'waiting' : meta.stage === 'frames' ? 'preparing' : 'searching';
  const title = failed ? '로고 후보 찾기가 멈췄어요.' : stageTitles[meta.stage] || '영상에서 로고 후보를 찾고 있어요.';
  const detail = failed ? '멈춘 단계: 로고 후보 찾기'
    : meta.stage_detail || (openai ? 'OpenAI로 프레임별 위치를 찾고 있습니다.' : 'RunPod에서 프레임별 후보를 찾고 있습니다.');
  const steps = logoProgressSteps([meta]);
  const facts = [
    ['원본 로고', `${logos.length}개`],
    ['위치 찾기', openai ? 'OpenAI' : 'RunPod'],
    ['정밀 대조', 'RunPod · 자동'],
    ['접수', receivedAt(meta.created_at)],
  ];
  return <div className="rv-video rv-logo-progress">
    <section className="rv-progress-page rv-video-progress" aria-labelledby="logo-progress-title">
      <div className={`rv-progress-card rv-video-progress-card is-${phase}`}>
        <LogoScanner base={base} logos={logos} phase={phase} value={value} />
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
        <dl className="rv-video-facts">{facts.map(([label, text]) => <div key={label}><dt>{label}</dt><dd>{text}</dd></div>)}</dl>
        {failed ? <>
          <ErrorMessage message={meta.error || '후보 찾기에 실패했습니다.'} />
          <div className="rv-progress-actions"><button type="button" className="rv-button primary" onClick={onRestart}>
            {linkedReview ? '동영상 검수로 돌아가기' : '자료 입력에서 다시 접수'}<Icon name="arrow" /></button></div>
        </> : <p className="rv-progress-help">후보를 찾으면 정밀 대조 화면으로 넘어갑니다. 페이지를 닫아도 서버에서 검수를 계속합니다.</p>}
      </div>
    </section>
  </div>;
}
