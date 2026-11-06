import React, { useEffect, useRef, useState } from 'react';
import { Icon } from '../../components/Icon.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import '../../styles/review-progress.css';

export default function ReviewProgress({ title, message, detail, percent, startedAt, steps = [], onCancel, onRetry, error, helpText = '완료되면 다음 화면이 표시됩니다. 페이지를 그대로 두셔도 됩니다.' }) {
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
  return <section className="rv-progress-page" aria-labelledby="review-process-title">
    <div className="rv-progress-card">
      <span className="rv-progress-symbol" aria-hidden="true"><span className="rv-progress-spinner" /><Icon name="search" /></span>
      <span className="rv-eyebrow">REVIEW IN PROGRESS</span>
      <h1 id="review-process-title" tabIndex={-1}>{title}</h1>
      <p className="rv-progress-message" role="status">{message}</p>
      {detail && <p className="rv-progress-detail">{detail}</p>}
      <p className="rv-progress-elapsed" role="timer" aria-live="off" aria-label="경과 시간"><span>경과 시간</span><time dateTime={`PT${seconds}S`}>{elapsed}</time></p>
      <div className="rv-progress-track" role="progressbar" aria-label="검수 처리 진행 상태"
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={value ?? undefined} aria-valuetext={message}>
        <div className={value === null ? 'is-indeterminate' : ''} style={value === null ? undefined : { width: `${value}%` }} />
      </div>
      <div className="rv-progress-caption"><span>{value === null ? '서버 처리 상태 확인 중' : '처리 단계 기준 진행률'}</span><strong>{value === null ? '처리 중' : `${value}%`}</strong></div>
      {steps.length > 0 && <ol className="rv-progress-steps">{steps.map((step, i) => <li key={step.key} className={`is-${step.status}`}>
        <span>{step.status === 'completed' ? <Icon name="check" /> : String(i + 1).padStart(2, '0')}</span>{step.label}
      </li>)}</ol>}
      <p className="rv-progress-help">{helpText}</p>
      <ErrorMessage message={error} />
      <div className="rv-progress-actions">
        {onRetry && <button type="button" className="rv-button secondary" onClick={onRetry}>진행 상태 다시 확인</button>}
        {onCancel && <button type="button" className="text-btn" onClick={onCancel}>입력 반영 취소</button>}
      </div>
    </div>
  </section>;
}
