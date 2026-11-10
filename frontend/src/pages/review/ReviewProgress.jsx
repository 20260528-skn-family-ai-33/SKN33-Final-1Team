import React, { useEffect, useRef, useState } from 'react';
import { Icon } from '../../components/Icon.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { scanFocus } from './progressScan.js';
import '../../styles/review-progress.css';

const statusText = { completed: '완료', active: '진행 중', waiting: '차례 대기', failed: '멈춤', pending: '대기' };

// 실제 포스터를 표시할 수 없을 때는 단계에 맞는 그림을 쓴다.
function PartArt({ kind }) {
  return <span className={`rv-scan-art is-${kind || 'document'}`}><i /><i /><i /><i /></span>;
}

// 포스터 위의 확대 위치와 렌즈의 중심은 같은 좌표를 쓴다.
function PosterScan({ image, working }) {
  const width = Math.min(460, 248 * image.width / image.height);
  return <figure className={`rv-poster-scan${working ? ' is-working' : ''}`}>
    <div className="rv-poster-scan-sheet" style={{ '--rv-poster-width': `${width}px` }}>
      <img src={image.url} alt="검수 대상 포스터" width={image.width} height={image.height} />
      <span className="rv-poster-scan-zoom" aria-hidden="true"><img src={image.url} alt="" /></span>
      <span className="rv-poster-scan-lens" aria-hidden="true" />
    </div>
  </figure>;
}

function ProgressScanner({ steps, image }) {
  const { index, phase } = scanFocus(steps);
  const poster = image?.url && image.width > 0 && image.height > 0;
  const part = steps[index];
  const lensIcon = phase === 'done' ? 'check' : phase === 'failed' ? 'warning' : phase === 'waiting' ? 'clock' : null;
  return <>
    {poster && <PosterScan image={image} working={phase === 'working'} />}
    <div className={`rv-scan is-${phase}${poster ? ' has-poster' : ''}`}>
      <div className="rv-scan-track">
        <ol className="rv-scan-parts" aria-label="처리 단계">
          {steps.map((step, i) => <li key={step.key} className={`rv-scan-part is-${step.status}${i === index ? ' is-current' : ''}`}
            aria-current={i === index && ['active', 'waiting'].includes(step.status) ? 'step' : undefined}>
            {!poster && <span className="rv-scan-tile" aria-hidden="true"><PartArt kind={step.kind} />
              {step.status === 'completed' && <span className="rv-scan-badge"><Icon name="check" /></span>}</span>}
            <strong className="rv-scan-name">{step.short || step.label}</strong>
            <span className="rv-scan-state">{i === index && phase === 'working' ? '지금 확인 중' : statusText[step.status]}</span>
            {step.short && step.short !== step.label && <span className="visually-hidden">: {step.label}</span>}
          </li>)}
        </ol>
        {!poster && <span className="rv-scan-lens" aria-hidden="true" style={{ left: `${(index + .5) / steps.length * 100}%` }}>
          <span className="rv-scan-glass">
            <span className="rv-scan-zoom"><PartArt kind={part.kind} /></span>
            <span className="rv-scan-line" />
            {lensIcon && <Icon name={lensIcon} />}
          </span>
          <span className="rv-scan-handle" />
        </span>}
      </div>
    </div>
  </>;
}

export default function ReviewProgress({ title, message, detail, percent, startedAt, steps = [], image, facts = [], onCancel, cancelLabel = '입력 반영 취소', cancelBusy = false, onRetry, error, helpText = '완료되면 다음 화면이 표시됩니다. 페이지를 그대로 두셔도 됩니다.' }) {
  const openedAt = useRef(Date.now());
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const suppliedStart = typeof startedAt === 'number' ? startedAt : Date.parse(startedAt);
  const start = Number.isFinite(suppliedStart) ? Math.min(openedAt.current, suppliedStart) : openedAt.current;
  const seconds = Math.max(0, Math.floor((now - start) / 1000));
  const elapsed = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  const value = Number.isFinite(percent) ? Math.min(100, Math.max(0, percent)) : null;
  const completed = steps.filter(step => step.status === 'completed').length;
  const shownFacts = [...facts, ...(steps.length ? [['처리 단계', `${completed} / ${steps.length} 완료`]] : [])];
  return <section className="rv-progress-page" aria-labelledby="review-process-title">
    <div className="rv-progress-card rv-scan-card">
      {steps.length ? <ProgressScanner steps={steps} image={image} />
        : <span className="rv-progress-symbol" aria-hidden="true"><Icon name="search" /></span>}
      <span className="rv-eyebrow">REVIEW IN PROGRESS</span>
      <h1 id="review-process-title" tabIndex={-1}>{title}</h1>
      <p className="rv-progress-message" role="status">{message}</p>
      {detail && <p className="rv-progress-detail">{detail}</p>}
      <div className="rv-progress-track" role="progressbar" aria-label="검수 처리 진행 상태"
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={value ?? undefined} aria-valuetext={message}>
        <div className={`rv-progress-fill${value === null ? ' is-unknown' : ''}`} style={value === null ? undefined : { width: `${value}%` }}>
          {(value === null || value > 0 && value < 100) && <span className="rv-progress-shine" />}
        </div>
      </div>
      <div className="rv-progress-caption"><span>{value === null ? '세부 진행률 확인 불가' : '처리 단계 기준 진행률'}</span><strong>{value === null ? '서버 상태 확인 중' : `${value}%`}</strong></div>
      <dl className="rv-progress-facts">
        {shownFacts.map(([label, text]) => <div key={label}><dt>{label}</dt><dd>{text}</dd></div>)}
        <div><dt>경과 시간</dt><dd role="timer" aria-live="off" aria-label="경과 시간"><time dateTime={`PT${seconds}S`}>{elapsed}</time></dd></div>
      </dl>
      <p className="rv-progress-help">{helpText}</p>
      <ErrorMessage message={error} />
      <div className="rv-progress-actions">
        {onRetry && <button type="button" className="rv-button secondary" onClick={onRetry}>진행 상태 다시 확인</button>}
        {onCancel && <button type="button" className="text-btn" disabled={cancelBusy} onClick={onCancel}>{cancelBusy ? '취소 확인 중…' : cancelLabel}</button>}
      </div>
    </div>
  </section>;
}
