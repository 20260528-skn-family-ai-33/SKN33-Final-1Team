import React from 'react';
import { Icon } from '../../components/Icon.jsx';
import ImageStage from '../../components/ImageStage.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { stageLabels } from '../../reviewConstants.js';

export default function RunningPage({ state, pollError, retryPoll, navigate, run, preview, dimensions }) {
  const failed = state.review?.status === 'failed';
  const progress = Math.min(100, Math.max(0, state.review?.progress || 0));
  const stages = [['preprocessing', '이미지와 기준 확인'], ['ocr', '문구와 위치 읽기'], ['review', '기준과 추출 문구 비교'], ['completed', '항목별 결과 정리']];
  const current = ['queued', ...stages.map(([key]) => key)].indexOf(state.review?.stage);
  return <section aria-labelledby="page-title">
    <div className="rv-workspace-intro"><div><span className="rv-eyebrow">03 / REVIEW IN PROGRESS</span><h1 id="page-title" tabIndex={-1}>{failed ? '검수를 완료하지 못했습니다.' : '확정한 기준으로 검수하고 있어요.'}</h1></div><p>읽은 문구와 위치를 검수 기준에 연결합니다.<br />완료되면 결과 작업 화면으로 이동합니다.</p></div>
    <div className="rv-running-layout"><ImageStage src={preview} width={dimensions?.width} height={dimensions?.height} title={state.file?.name || '검수 이미지'} subtitle={`기준 v${state.snapshot?.version || '—'} · 실행 중인 원본`} alt="현재 검수 중인 이미지" />
      <section className="rv-panel rv-running-inspector" aria-label="서버 실행 상태"><span className="rv-eyebrow">{failed ? 'REVIEW INTERRUPTED' : pollError ? 'CONNECTION CHECK' : 'CHECKING YOUR CREATIVE'}</span><div className="rv-running-status"><strong>{progress}<small>%</small></strong><span className="rv-running-pulse"><Icon name={failed ? 'warning' : 'search'} /></span></div>
        <h2>{state.review?.progress_message || stageLabels[state.review?.stage] || '검수 자료를 전달하고 있어요.'}</h2><p aria-live="polite">{failed ? '오류 내용을 확인하고 같은 기준으로 다시 시도해 주세요.' : '처음 실행할 때는 준비에 시간이 걸릴 수 있습니다.'}</p>
        <div className="rv-running-track" role="progressbar" aria-label="검수 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><div style={{ width: `${progress}%` }} /></div>
        <ol className="rv-running-stages">{stages.map(([key,title], i) => <li key={key} className={current === i + 1 ? 'is-active' : current > i + 1 ? 'is-done' : ''}><span>{current > i + 1 ? <Icon name="check" /> : String(i + 1).padStart(2,'0')}</span><span>{title}</span>{current === i + 1 && !failed && <small>처리 중</small>}</li>)}</ol>
        <dl className="rv-running-context"><div><dt>확정한 기준</dt><dd>v{state.snapshot?.version || '—'}</dd></div><div><dt>검수 대상</dt><dd>{state.snapshot?.criteria.length || 0}개 항목</dd></div></dl>
        <ErrorMessage message={typeof state.review?.error === 'string' ? state.review.error : state.review?.error?.message} /><ErrorMessage message={pollError} />
        {pollError && <div className="rv-running-retry"><button className="rv-button secondary" onClick={retryPoll}>진행 상태 다시 확인</button></div>}
        {failed && <div className="rv-running-retry"><button className="rv-button secondary" onClick={() => navigate('criteria')}>기준 다시 확인</button><button className="rv-button primary" onClick={run}>같은 기준으로 재시도</button></div>}
      </section></div>
  </section>;
}
