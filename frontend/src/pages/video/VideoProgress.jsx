import React, { useState } from 'react';
import { Link } from 'react-router';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { useTilt } from '../../components/LayerFigure.jsx';
import { LogoFlowCard } from './LogoFlowParts.jsx';
import { filmCells, formatTime, progressSteps, scanState, stageLabels, stageIndex, stageOrder, stepLabels } from './videoFormat.js';

const stepStatusText = { completed: '완료', active: '진행 중', failed: '멈춤', pending: '대기' };
const clamp01 = value => Math.min(1, Math.max(0, value));

// 필름 칸 하나. 프레임이 아직 없거나 불러오지 못하면 빈 칸 모양만 남긴다.
function FilmCell({ src, className, style }) {
  const [failed, setFailed] = useState(false);
  return <span className={className} style={style}>
    {src && !failed && <img src={src} alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} />}
    <i />
  </span>;
}

// 돋보기가 표본 프레임 필름을 따라 읽는 연출. 읽은 칸과 돋보기 위치는 서버가 알려 준 처리량을 표본 계획의 시각에 대응시켜 정한다.
function FilmScanner({ job, scan }) {
  // 필름은 3D로 눕혀 놓고(스타일), 마우스를 따라 조금 기운다. 움직임을 줄인 환경과 터치 화면에서는 고정한다.
  const tilt = useTilt();
  const { height, gap, cells } = filmCells(job?.media, 502, 61, 5);
  const count = cells.length;
  const lastTime = cells[count - 1]?.time_ms || 0;
  const allRead = ['organizing', 'judging', 'done'].includes(scan.phase) || scan.readUpTo === Infinity;
  // 돋보기 위치(0~1): 읽는 중이면 지금 읽는 프레임의 시각, 읽기 전이면 처음, 다 읽었으면 끝.
  const lens = scan.lensTime != null ? (lastTime > 0 ? clamp01(scan.lensTime / lastTime) : 0) : allRead ? 1 : 0;
  const current = scan.lensTime != null ? Math.round(lens * (count - 1)) : -1;
  const lensX = count <= 1 ? 50 : 7 + lens * 86;
  const frameSrc = cell => (job?.id && scan.framesReady ? `/api/video-reviews/${job.id}/frames/${cell.frame_id}` : null);
  const lensCell = cells[Math.max(0, Math.min(count - 1, Math.round(lens * (count - 1))))];
  const duration = job?.media?.duration_ms;
  const frames = scan.frames;
  const caption = scan.phase === 'reading' && frames?.total ? `프레임 ${frames.done} / ${frames.total} 읽음`
    : scan.phase === 'preparing' && frames?.total ? `표본 프레임 ${frames.total}장 준비`
      : allRead ? '표본 프레임 읽기 완료' : scan.phase === 'failed' ? '처리 멈춤' : '표본 프레임 준비 전';
  return <div className={`rv-video-scanner is-${scan.phase}`} aria-hidden="true" ref={tilt}
    style={{ '--cell-h': `${height}px`, '--cell-gap': `${gap}px`, '--cells': count }}>
    <div className="rv-video-film">
      <div className="rv-video-film-track">
        <div className="rv-video-film-cells">{cells.map((cell, index) => <FilmCell key={cell.frame_id} src={frameSrc(cell)}
          style={{ '--i': index }}
          className={`rv-video-film-cell${allRead || cell.time_ms <= scan.readUpTo ? ' is-read' : ''}${index === current ? ' is-current' : ''}${scan.phase === 'failed' && current >= 0 && index > current ? ' is-stopped' : ''}`} />)}</div>
        <div className="rv-video-lens" style={{ left: `${lensX}%` }}>
          <span className="rv-video-lens-glass">
            <span className="rv-video-lens-lines"><i /><i /><i /></span>
            <FilmCell key={lensCell?.frame_id} src={lensCell ? frameSrc(lensCell) : null} className="rv-video-lens-frame" />
            <span className="rv-video-lens-scan" />
            {scan.phase === 'judging' || scan.phase === 'done' ? <Icon name="check" /> : scan.phase === 'failed' ? <Icon name="warning" /> : null}
          </span>
          <span className="rv-video-lens-handle" />
        </div>
      </div>
    </div>
    <div className="rv-video-scanner-scale"><span>0:00</span><span>{caption}</span><span>{duration ? formatTime(duration) : '–:––'}</span></div>
  </div>;
}

// 대기 중에 적힌 "대기 N건"은 다음 단계에서도 남아 있을 수 있어, 대기 단계가 아니면 쓰지 않는다.
function stageDetail(job) {
  const stale = job.stage !== 'queued' && /^대기 \d+건$/.test(job.stage_detail || '');
  if (job.stage_detail && !stale) return job.stage_detail;
  return job.media ? `영상 ${formatTime(job.media.duration_ms)} · ${job.media.width}×${job.media.height}` : '';
}

// 실패하면 서버가 기록한 멈춘 단계를 쓰고, 프레임 수는 글자 읽기에서 멈췄을 때만 덧붙인다.
function failureDetail(job) {
  const label = stepLabels[job.failed_stage];
  if (job.failed_stage === 'ocr' && /프레임\s*\d+\s*\/\s*\d+/.test(job.stage_detail || '')) return `멈춘 단계: ${label} · ${job.stage_detail}`;
  return label ? `멈춘 단계: ${label}` : '';
}

// 동영상 검수 진행 화면: 포스터 진행 카드와 같은 구성에 필름 돋보기를 얹는다.
export default function VideoProgress({ job, error, missing, onRestart, logo }) {
  if (!job && missing) {
    // 없는 기록은 진행 중으로 보이지 않게 따로 알린다(조회는 이미 멈춘 상태).
    return <section className="rv-progress-page rv-video-progress" aria-labelledby="page-title">
      <div className="rv-progress-card rv-video-progress-card is-missing">
        <span className="rv-progress-symbol" aria-hidden="true"><Icon name="search" /></span>
        <span className="rv-eyebrow">RECORD NOT FOUND</span>
        <h1 id="page-title" tabIndex={-1}>검수 기록을 찾을 수 없어요.</h1>
        <p className="rv-progress-message">주소가 바뀌었거나 이 서버의 실행 폴더에 기록이 없습니다.</p>
        <ErrorMessage message={error} />
        <div className="rv-progress-actions"><Link className="rv-button secondary" to="/review/tasks">내 작업으로</Link>
          <button type="button" className="rv-button primary" onClick={onRestart}>자료 입력으로 돌아가기<Icon name="arrow" /></button></div>
      </div>
    </section>;
  }
  const scan = scanState(job);
  const steps = progressSteps(job);
  const failed = job?.status === 'failed';
  const value = job ? Math.min(100, Math.max(0, Math.round(job.progress || 0))) : null;
  const title = !job ? '검수 기록을 불러오고 있어요.' : failed ? stageLabels.failed : stageLabels[job.stage] || '처리하고 있어요.';
  const media = job?.media;
  const detail = !job ? '' : failed ? failureDetail(job) : stageDetail(job);
  const runtime = job?.runtime;
  const criteriaCount = job?.criteria?.length;
  const activeKey = job && !failed ? stageOrder[stageIndex(job.stage)] : null;
  // 화면 낭독기에는 단계가 바뀔 때만 알린다(진행률·프레임 수는 너무 자주 바뀐다).
  const announcement = !job ? '' : failed ? `${stepLabels[job.failed_stage] || '처리'} 단계에서 멈췄습니다.` : `${stepLabels[activeKey] || '처리'} 단계 진행 중`;
  const facts = [
    ['영상 길이', media ? formatTime(media.duration_ms) : '확인 중'],
    ['해상도', media ? `${media.width}×${media.height}` : '확인 중'],
    ['검수 기준', criteriaCount == null ? '확인 중' : criteriaCount ? `${criteriaCount}개` : '없음 · 자막·광고만'],
    ['처리 속도', runtime?.seconds_per_frame ? `장당 ${runtime.seconds_per_frame}초${runtime.device ? ` · ${runtime.device}` : ''}` : '글자 읽기 후 표시'],
  ];
  return <>
    <section className="rv-progress-page rv-video-progress" aria-labelledby="page-title">
      <div className={`rv-progress-card rv-video-progress-card is-${scan.phase}`}>
        <FilmScanner job={job} scan={scan} />
        <span className="rv-eyebrow">{failed ? 'REVIEW STOPPED' : job ? 'VIDEO REVIEW IN PROGRESS' : 'VIDEO REVIEW'}</span>
        <h1 id="page-title" tabIndex={-1}>{title}</h1>
        <span className="visually-hidden" role="status">{announcement}</span>
        {detail && <p className="rv-progress-message">{detail}</p>}
        {job && <>
          <div className="rv-progress-track" role="progressbar" aria-label="동영상 검수 진행률" aria-valuemin={0} aria-valuemax={100}
            aria-valuenow={value} aria-valuetext={`${value}% · ${title}`}>
            <div className="rv-progress-fill" style={{ width: `${value}%` }} />
          </div>
          <div className="rv-progress-caption"><span>{failed ? '멈춘 시점의 처리 단계 기준 진행률' : '처리 단계 기준 진행률'}</span><strong>{value}%</strong></div>
          <ol className="rv-progress-steps rv-video-steps" aria-label="처리 단계">{steps.map((step, i) => <li key={step.key} className={`is-${step.status}`}
            aria-current={step.status === 'active' ? 'step' : undefined}>
            <span aria-hidden="true">{step.status === 'completed' ? <Icon name="check" /> : step.status === 'failed' ? <Icon name="warning" /> : String(i + 1).padStart(2, '0')}</span>
            {step.label}<span className="visually-hidden"> {stepStatusText[step.status]}</span>
          </li>)}</ol>
          <dl className="rv-video-facts">{facts.map(([label, text]) => <div key={label}><dt>{label}</dt><dd>{text}</dd></div>)}</dl>
        </>}
        {failed ? <ErrorMessage message={job.error || '동영상 검수를 완료하지 못했습니다.'} />
          : job && <>
            <p className="rv-progress-help">완료되면 결과 화면으로 이동합니다. 페이지를 닫아도 서버에서 검수를 계속합니다.</p>
            <div className="rv-progress-actions"><Link className="rv-button secondary" to="/review/tasks">내 작업에서 다시 열기</Link></div>
          </>}
        <ErrorMessage message={error} />
        {job?.warnings?.length > 0 && <ul className="rv-video-warning-list">{job.warnings.map(warning => <li key={warning}><Icon name="warning" />{warning}</li>)}</ul>}
        {(failed || (!job && error)) && <div className="rv-progress-actions">
          <button type="button" className="rv-button primary" onClick={onRestart}>새 동영상으로 다시 검수<Icon name="arrow" /></button>
        </div>}
      </div>
    </section>
    <LogoFlowCard jobs={logo?.jobs} error={logo?.error} />
  </>;
}
