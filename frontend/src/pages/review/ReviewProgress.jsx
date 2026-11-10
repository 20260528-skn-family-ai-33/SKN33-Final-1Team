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
  const ready = image?.url && image.width > 0 && image.height > 0;
  const width = ready ? Math.min(460, 248 * image.width / image.height) : 0;
  return <figure className={`rv-poster-scan${working ? ' is-working' : ''}`}>
    <div className="rv-poster-scan-stage">
      {ready ? <div className="rv-poster-scan-sheet" style={{ '--rv-poster-width': `${width}px` }}>
        <img src={image.url} alt="검수 대상 포스터" width={image.width} height={image.height} />
        <span className="rv-poster-scan-zoom" aria-hidden="true"><img src={image.url} alt="" /></span>
        <span className="rv-poster-scan-lens" aria-hidden="true" />
      </div> : <span className="rv-poster-scan-placeholder"><Icon name="image" />
        {image?.unavailable ? '포스터 미리보기를 표시할 수 없습니다.' : '포스터 미리보기를 불러오고 있어요.'}</span>}
    </div>
    <figcaption><span>검수 대상 포스터</span><span>첨부 이미지</span></figcaption>
  </figure>;
}

function ProgressScanner({ steps }) {
  const { index, phase } = scanFocus(steps);
  const part = steps[index];
  const lensIcon = phase === 'done' ? 'check' : phase === 'failed' ? 'warning' : phase === 'waiting' ? 'clock' : null;
  return <div className={`rv-scan is-${phase}`}>
      <div className="rv-scan-track">
        <ol className="rv-scan-parts" aria-label="처리 단계">
          {steps.map((step, i) => <li key={step.key} className={`rv-scan-part is-${step.status}${i === index ? ' is-current' : ''}`}
            aria-current={i === index && ['active', 'waiting'].includes(step.status) ? 'step' : undefined}>
            <span className="rv-scan-tile" aria-hidden="true"><PartArt kind={step.kind} />
              {step.status === 'completed' && <span className="rv-scan-badge"><Icon name="check" /></span>}</span>
            <strong className="rv-scan-name">{step.short || step.label}</strong>
            <span className="rv-scan-state">{i === index && phase === 'working' ? '지금 확인 중' : statusText[step.status]}</span>
            {step.short && step.short !== step.label && <span className="visually-hidden">: {step.label}</span>}
          </li>)}
        </ol>
        <span className="rv-scan-lens" aria-hidden="true" style={{ left: `${(index + .5) / steps.length * 100}%` }}>
          <span className="rv-scan-glass">
            <span className="rv-scan-zoom"><PartArt kind={part.kind} /></span>
            <span className="rv-scan-line" />
            {lensIcon && <Icon name={lensIcon} />}
          </span>
          <span className="rv-scan-handle" />
        </span>
      </div>
    </div>;
}

export default function ReviewProgress({ title, message, detail, percent, startedAt, steps = [], poster = false, queued = false,
  image, facts = [], onCancel, cancelLabel = '입력 반영 취소', cancelBusy = false, onRetry, error,
  helpText = '완료되면 다음 화면이 표시됩니다. 페이지를 그대로 두셔도 됩니다.' }) {
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
  const value = Number.isFinite(percent) ? Math.min(100, Math.max(0, percent)) : poster ? 0 : null;
  const completed = steps.filter(step => step.status === 'completed').length;
  const shownFacts = poster || !steps.length ? facts : [...facts, ['처리 단계', `${completed} / ${steps.length} 완료`]];
  const phases = [{ key: 'queue', label: '순서 대기', status: queued ? 'active' : 'completed' }, ...steps];
  return <section className={`rv-progress-page${poster ? ' rv-poster-progress' : ''}`} aria-labelledby="review-process-title">
    <div className="rv-progress-card rv-scan-card">
      {poster ? <PosterScan image={image} working={steps.some(step => step.status === 'active')} />
        : steps.length ? <ProgressScanner steps={steps} />
        : <span className="rv-progress-symbol" aria-hidden="true"><Icon name="search" /></span>}
      <span className="rv-eyebrow">{poster ? 'POSTER REVIEW IN PROGRESS' : 'REVIEW IN PROGRESS'}</span>
      <h1 id="review-process-title" tabIndex={-1}>{title}</h1>
      <p className="rv-progress-message" role="status">{poster ? detail || `${completed} / ${steps.length}개 처리 단계 완료` : message}</p>
      {!poster && detail && <p className="rv-progress-detail">{detail}</p>}
      <div className="rv-progress-track" role="progressbar" aria-label="검수 처리 진행 상태"
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={value ?? undefined}
        aria-valuetext={poster ? `처리 단계 기준 ${value}% · ${message}` : message}>
        <div className={`rv-progress-fill${value === null ? ' is-unknown' : ''}`} style={value === null ? undefined : { width: `${value}%` }}>
          {!poster && (value === null || value > 0 && value < 100) && <span className="rv-progress-shine" />}
        </div>
      </div>
      <div className="rv-progress-caption"><span>{value === null ? '세부 진행률 확인 불가' : '처리 단계 기준 진행률'}</span><strong>{value === null ? '서버 상태 확인 중' : `${value}%`}</strong></div>
      {poster && <ol className="rv-progress-steps" aria-label="처리 단계 목록">{phases.map((phase, index) =>
        <li key={phase.key} className={`is-${phase.status}`} aria-current={phase.status === 'active' ? 'step' : undefined}>
          <span aria-hidden="true">{phase.status === 'completed' ? <Icon name="check" /> : String(index + 1).padStart(2, '0')}</span>
          {phase.short || phase.label}
          {phase.status === 'completed' && <small className="sr-only"> 완료</small>}
        </li>)}</ol>}
      <dl className="rv-progress-facts">
        {shownFacts.map(([label, text]) => <div key={label}><dt>{label}</dt><dd>{text}</dd></div>)}
        <div><dt>경과 시간</dt><dd role="timer" aria-live="off" aria-label="경과 시간"><time dateTime={`PT${seconds}S`}>{elapsed}</time></dd></div>
      </dl>
      <p className="rv-progress-help">{helpText}</p>
      <ErrorMessage message={error} />
      {(onRetry || onCancel) && <div className="rv-progress-actions">
        {onRetry && <button type="button" className="rv-button secondary" onClick={onRetry}>진행 상태 다시 확인</button>}
        {onCancel && <button type="button" className="text-btn" disabled={cancelBusy} onClick={onCancel}>{cancelBusy ? '취소 확인 중…' : cancelLabel}</button>}
      </div>}
    </div>
  </section>;
}
