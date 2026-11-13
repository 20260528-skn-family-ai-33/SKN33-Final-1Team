import React, { useMemo, useRef, useState } from 'react';
import VideoEvidenceDialog from './VideoEvidenceDialog.jsx';
import VideoEvidenceGraph from './VideoEvidenceGraph.jsx';
import VideoWorkflowActions from './VideoWorkflowActions.jsx';
import { Icon } from '../../components/Icon.jsx';
import ImageStage from '../../components/ImageStage.jsx';
import { LogoFlowInline } from './LogoFlowParts.jsx';
import { coverageNote, filterItems, formatTime, judgmentItems, roleLabels, statusLabels } from './videoFormat.js';

const EMPTY = [];
const NO_VIEW = { segmentId: null, frameId: null, focus: null, key: '', time: null };
const samplingLabels = { base: '1초 표본', head: '시작 0.2초 표본', tail: '끝 0.2초 표본' };
const evidenceKey = item => (item ? `${item.frame_id}:${(item.bbox_xyxy || []).join(',')}:${item.time_ms}` : '');
const itemEvidence = item => item?.result?.evidence_segments || (item?.disclosure ? disclosureEvidence(item.disclosure) : EMPTY);
const itemTitle = item => item.result?.criterion || '광고·협찬 표시';

function StatusLabel({ status }) {
  return <span className={`rv-result-status ${status || 'review'}`}><span aria-hidden="true" />{statusLabels[status] || '확인 필요'}</span>;
}

function joinLabels(...values) { return values.filter(Boolean).join(' · '); }

// 광고 표시 규정 중 화면 위치가 있는 근거(R1·R3)만 모은다.
function disclosureEvidence(disclosure) {
  return Object.entries(disclosure?.rules || {}).flatMap(([key, rule]) => (rule.evidence || [])
    .filter(item => item?.frame_id && Number.isFinite(item.time_ms)).map(item => ({ ...item, label: key })));
}

function EvidenceButton({ item, active, onShow, children }) {
  return <button type="button" className={`rv-video-evidence-button${active ? ' is-active' : ''}`} aria-pressed={active} onClick={() => onShow(item)}>
    {children}<span className="rv-video-evidence-go">{active ? '보는 중' : '위치 보기'}</span>
  </button>;
}

function TrackDetail({ track, frames, activeKey, onShow }) {
  const variants = track.variants || EMPTY;
  const rep = frames.find(frame => frame.frame_id === track.rep_reading_frame);
  return <>
    <div className="rv-result-verdict"><span className="rv-video-chip">{roleLabels[track.role] || track.role || '자막'}</span>
      <span>{joinLabels(track.id, track.vote && `투표 ${track.vote.count}/${track.vote.total}`)}</span></div>
    {track.requires_review && <p className="rv-video-note"><Icon name="warning" />프레임마다 읽은 결과가 갈려 과반을 얻지 못했습니다. 원본을 확인해 주세요.</p>}
    <section className="rv-result-detail-section"><h3>자막 문구</h3><p className="rv-result-observation">{track.text}</p></section>
    <section className="rv-result-detail-section"><h3>보인 시간 (표본 기준)</h3>
      <p>{formatTime(track.start_ms)}–{formatTime(track.end_ms)}{rep ? ` · 대표 프레임 ${formatTime(rep.time_ms)}` : ''}</p></section>
    <section className="rv-result-detail-section"><h3>다르게 읽힌 문구 <span>{variants.length}</span></h3>
      {variants.length ? <ol className="rv-video-evidence">{variants.map((variant, i) => {
        const target = { frame_id: variant.frame_id, time_ms: variant.time_ms, text: variant.text, label: track.id };
        return <li key={i}><EvidenceButton item={target} active={activeKey === evidenceKey(target)} onShow={onShow}>
          <span className="rv-video-time">{formatTime(variant.time_ms)}</span><span className="rv-video-evidence-text">{variant.text}</span></EvidenceButton></li>;
      })}</ol> : <p className="rv-result-muted">다르게 읽힌 표본이 없습니다.</p>}
    </section>
  </>;
}

function SegmentDetail({ segment, tracks, frames, items, onSelectItem }) {
  const texts = segment.track_ids.map(id => tracks.find(track => track.id === id)).filter(Boolean);
  const rep = frames.find(frame => frame.frame_id === segment.rep_frame_id);
  const users = items.filter(item => item.kind === 'criterion' && (item.result.evidence_segments || []).some(e => e.segment_id === segment.id));
  return <>
    <div className="rv-result-verdict"><span className="rv-video-chip">{segment.id}</span><span>{((segment.end_ms - segment.start_ms) / 1000).toFixed(1)}초 구간</span></div>
    <section className="rv-result-detail-section"><h3>구간</h3><p>{formatTime(segment.start_ms)}–{formatTime(segment.end_ms)}{rep ? ` · 대표 프레임 ${formatTime(rep.time_ms)}` : ''}</p></section>
    <section className="rv-result-detail-section"><h3>자막 <span>{texts.length}</span></h3>
      {texts.length ? <ul className="rv-video-plain-list">{texts.map(track => <li key={track.id}>{track.text}</li>)}</ul> : <p className="rv-result-muted">이 구간에서 남긴 자막 줄이 없습니다.</p>}</section>
    <section className="rv-result-detail-section"><h3>1초 표본 프레임</h3><p>{segment.frame_ids?.length || 0}장</p></section>
    <section className="rv-result-detail-section"><h3>판정 근거로 쓴 항목 <span>{users.length}</span></h3>
      {users.length ? <ul className="rv-video-plain-list">{users.map(item => <li key={item.key}><button type="button" className="rv-video-text-button" onClick={() => onSelectItem(item)}>{item.result.criterion}</button></li>)}</ul>
        : <p className="rv-result-muted">이 구간을 근거로 쓴 기준이 없습니다.</p>}</section>
  </>;
}

// 표본 범위·자막 분류 방식·OCR 처리 정보. 판정의 한계를 함께 보여 준다.
function RuntimeDetails({ job }) {
  const runtime = job.runtime;
  return <details className="rv-result-runtime"><summary>검사 범위와 처리 내역</summary>
    <p>{coverageNote(job.coverage)}. &lsquo;충족&rsquo;은 검사한 시점에서 충족이라는 뜻입니다.</p>
    <p>{job.classifier === 'logreg' ? '학습한 자막 줄 분류 모델' : '자막 줄 분류 모델이 아직 없어 OCR 신뢰도 0.5 하한 규칙'}으로 자막 후보를 골랐습니다. 제외한 줄도 사유와 함께 남겨 두었습니다.</p>
    {runtime && <p>{joinLabels(runtime.engine && `OCR ${runtime.engine}`, runtime.device, runtime.seconds_per_frame && `장당 ${runtime.seconds_per_frame}초`,
      Number.isFinite(runtime.ocr_success_ratio) && `읽기 성공 ${Math.round(runtime.ocr_success_ratio * 100)}%`)}</p>}
    <p>기준 판정은 영상당 1회 · 기준 v{job.criteria_version}</p>
  </details>;
}

// 영상 재생 칸. 재생 위치는 타임라인 재생선과 맞춘다.
// 서버의 playback.available은 '재생용 MP4를 따로 만들었는가'라서, MP4·MOV·WEBM 원본은 false여도 그대로 재생된다.
// 브라우저가 못 읽는 AVI만 변환이 없을 때 재생 불가로 보고 원본 내려받기를 안내한다.
function PlayerPane({ job, player, currentTime, onTime, target, onSeekTarget }) {
  const [failed, setFailed] = useState(false);
  const unavailable = job.media?.container === 'avi' && job.playback?.available !== true;
  const media = job.media;
  const source = `/api/video-reviews/${job.id}/video`;
  const extension = { avi: 'avi', matroska: 'webm', mov: 'mp4' }[media?.container] || 'mp4';
  const seekLabel = target == null ? '근거 시각으로' : `${formatTime(target)}로 이동`;
  return <section className="image-stage image-stage--bare rv-video-player" aria-label="원본 영상">
    <div className="image-stage-viewport rv-video-player-viewport">
      {unavailable || failed ? <div className="image-stage-empty"><Icon name="film" /><strong className="rv-video-empty-title">영상을 재생할 수 없어요.</strong>
        <p>{unavailable ? job.playback?.reason || '브라우저에서 재생할 수 없는 형식입니다.' : '브라우저가 이 영상을 재생하지 못했습니다.'}<br />근거 프레임으로 확인하거나 원본을 내려받아 확인해 주세요.</p>
        <a className="rv-button secondary" href={source} download={`video-review-${job.id}.${extension}`}>원본 영상 내려받기</a></div>
        : <video ref={player} controls preload="metadata" playsInline src={source}
          onLoadedMetadata={event => { if (currentTime) event.currentTarget.currentTime = currentTime / 1000; }}
          onTimeUpdate={event => onTime(event.currentTarget.currentTime * 1000)} onSeeked={event => onTime(event.currentTarget.currentTime * 1000)}
          onError={() => setFailed(true)} />}
    </div>
    <div className="image-stage-toolbar"><span className="rv-video-timecode">재생 위치 <strong>{formatTime(currentTime)}</strong>
      {media && <span className="rv-video-media-meta"> · {formatTime(media.duration_ms)} · {media.width}×{media.height}</span>}</span>
      <button type="button" className="image-stage-focus" title={seekLabel} disabled={target == null || unavailable || failed} onClick={onSeekTarget}>
        <Icon name="clock" />{seekLabel}</button></div>
  </section>;
}

// 구간 타임라인: 검사 시점은 점, 미검사 구간은 빗금, 선택 항목의 근거는 강조, 재생 위치는 세로선으로 표시한다.
// 키보드로는 검수 목록의 '구간' 탭에서 같은 구간을 고른다.
function Timeline({ job, view, evidence, currentTime, shortcuts, onSelect, onScrub, onShortcut }) {
  const duration = job.coverage?.duration_ms || job.media?.duration_ms || 0;
  const time = Math.min(currentTime, duration);
  const pct = ms => `${Math.min(100, Math.max(0, ms / (duration || 1) * 100))}%`;
  const evidenceSegments = new Set(evidence.map(item => item.segment_id).filter(Boolean));
  const tracks = Object.fromEntries((job.tracks || []).map(track => [track.id, track]));
  const note = coverageNote(job.coverage);
  return <section className="rv-panel rv-video-timeline" aria-labelledby="video-timeline-title">
    <div className="rv-video-timeline-heading"><h2 id="video-timeline-title">구간 타임라인</h2>
      <span className="rv-video-time">{duration ? `${formatTime(time)} / ${formatTime(duration)}` : '시각 정보 없음'}</span></div>
    <div className="rv-video-timeline-body">
      <input type="range" min={0} max={duration} step="any" value={time} disabled={!duration}
        aria-label="영상 타임라인" aria-valuetext={formatTime(time)} onChange={event => onScrub(event.target.valueAsNumber)} />
      <div className="rv-video-timeline-scale" aria-hidden="true">{[0, .25, .5, .75, 1].map(tick => <span key={tick}>{formatTime(duration * tick)}</span>)}</div>
      {shortcuts.length > 0 && <div className="rv-video-timeline-shortcuts"><span>근거 시점</span>
        {shortcuts.map(({ item, target }) => <button type="button" key={item.key} className="rv-button secondary rv-compact"
          aria-pressed={view.key === evidenceKey(target)} onClick={() => onShortcut(item, target)}>{formatTime(target.time_ms)} {itemTitle(item)}</button>)}</div>}
      <details className="rv-video-coverage">
        <summary>검사 시점과 구간 보기</summary>
        {note && <p>{note}</p>}
        <ul className="rv-video-legend" aria-label="타임라인 범례"><li className="is-text">자막 구간</li><li className="is-evidence">선택 항목 근거</li><li className="is-current">보는 구간</li><li className="is-playhead">재생 위치</li></ul>
      <div className="rv-video-timeline-track">
        {(job.segments || EMPTY).map(segment => {
          const text = segment.track_ids.map(id => tracks[id]?.text).filter(Boolean).join(' · ');
          return <button key={segment.id} type="button" title={`${formatTime(segment.start_ms)}–${formatTime(segment.end_ms)}${text ? ` · ${text}` : ''}`}
            className={`rv-video-segment${segment.track_ids.length ? ' has-text' : ''}${evidenceSegments.has(segment.id) ? ' is-evidence' : ''}${view.segmentId === segment.id ? ' is-current' : ''}`}
            style={{ left: pct(segment.start_ms), width: pct(segment.end_ms - segment.start_ms) }} onClick={() => onSelect(segment)} aria-pressed={view.segmentId === segment.id}
            tabIndex={-1} aria-label={`${formatTime(segment.start_ms)}부터 ${formatTime(segment.end_ms)}까지 구간${text ? `: ${text}` : ''}${evidenceSegments.has(segment.id) ? ' · 선택 항목 근거' : ''}`} />;
        })}
        {(job.coverage?.uninspected || EMPTY).map(([start, end]) => <span key={`${start}-${end}`} className="rv-video-timeline-gap" style={{ left: pct(start), width: pct(end - start) }} />)}
        {evidence.filter(item => Number.isFinite(item.time_ms)).map((item, i) => <span key={i} className="rv-video-timeline-marker" style={{ left: pct(item.time_ms) }} />)}
        <span className="rv-video-timeline-playhead" style={{ left: pct(currentTime) }} />
      </div>
      <div className="rv-video-timeline-points" aria-hidden="true">{(job.coverage?.inspected_ms || EMPTY).map(time => <span key={time} style={{ left: pct(time) }} />)}</div>
      <p>점은 검사한 시점, 빗금은 검사하지 않은 구간입니다.</p>
      </details>
    </div>
  </section>;
}

// 왼쪽 영상과 타임라인을 오른쪽의 판정 목록에 연결한다. 검수 항목을 누르면 판정 근거가 창(VideoEvidenceDialog)으로 열리고,
// 자막 트랙·구간은 목록 아래에 상세를 보여 준다.
export default function VideoResults({ job, logo, logoWarning, onNewReview, onReuse, onRevise, onOpenRun, onDecision, onReport }) {
  const player = useRef(null);
  const items = useMemo(() => judgmentItems(job), [job]);
  const tracks = job.tracks || EMPTY;
  const segments = job.segments || EMPTY;
  const frames = job.frames || EMPTY;
  const [tab, setTab] = useState('checks');
  const [filter, setFilter] = useState('all');
  const [selection, setSelection] = useState(items[0]?.key ?? null);
  const [evidenceKeyOpen, setEvidenceKeyOpen] = useState(null);   // 판정 근거 창을 연 항목
  const rail = useRef(null);
  const segmentAt = time => segments.find(segment => time >= segment.start_ms && time < segment.end_ms) || segments.at(-1);
  const segmentView = segment => segment ? { segmentId: segment.id, focus: null, key: '', time: segment.start_ms,
    frameId: segment.rep_frame_id || segment.frame_ids?.[0] || frames.find(frame => frame.time_ms >= segment.start_ms && frame.time_ms < segment.end_ms)?.frame_id || null,
  } : NO_VIEW;
  const evidenceView = target => ({ segmentId: target.segment_id || segmentAt(target.time_ms)?.id || null, frameId: target.frame_id,
    key: evidenceKey(target), time: target.time_ms,
    focus: target.bbox_xyxy ? { bbox: target.bbox_xyxy, text: target.text, label: target.label || '선택 근거' } : null });
  const [view, setView] = useState(() => {
    const first = itemEvidence(items[0])[0];
    return first ? evidenceView(first) : segmentView(segments.find(segment => segment.track_ids.length) || segments[0]);
  });
  const [currentTime, setCurrentTime] = useState(view.time ?? 0);

  const visibleItems = filterItems(items, filter);
  const selectedItem = tab === 'checks' ? items.find(item => item.key === selection) : null;
  const selectedTrack = tab === 'tracks' ? tracks.find(track => track.id === selection) : null;
  const selectedSegment = tab === 'segments' ? segments.find(segment => segment.id === selection) : null;
  const evidence = itemEvidence(selectedItem);
  const shortcuts = useMemo(() => items.flatMap(item => {
    const target = itemEvidence(item).find(entry => Number.isFinite(entry.time_ms) && entry.frame_id);
    return target ? [{ item, target }] : [];
  }).sort((a, b) => a.target.time_ms - b.target.time_ms), [items]);
  const frame = frames.find(item => item.frame_id === view.frameId);
  const frameLines = frame?.lines || EMPTY;
  const kept = frameLines.filter(line => line.kept).length;
  const overlays = useMemo(() => {
    if (!frame) return EMPTY;
    const lines = (frame.lines || EMPTY).map(line => ({ id: line.line_id, bbox_xyxy: line.bbox_xyxy, text: line.text, label: line.kept ? line.text : `제외 · ${line.text}`,
      tone: line.kept ? null : 'excluded', ariaLabel: `${line.kept ? '남긴 글자 줄' : '제외한 글자 줄'}: ${line.text}` }));
    return view.focus ? [...lines, { id: 'focus', bbox_xyxy: view.focus.bbox, text: view.focus.text, label: view.focus.label, tone: view.key ? selectedItem?.status : null,
      ariaLabel: `${view.focus.label}${view.focus.text ? `: ${view.focus.text}` : ''}` }] : lines;
  }, [frame, view.focus, view.key, selectedItem?.status]);

  function seek(ms) {
    if (!Number.isFinite(ms)) return;
    setCurrentTime(ms);
    const video = player.current;
    if (video) {
      video.pause();
      if (video.readyState) video.currentTime = ms / 1000;
    }
  }
  function show(target) { setView(evidenceView(target)); seek(target.time_ms); }
  function showSegment(segment) {
    setView(segmentView(segment)); seek(segment.start_ms);
    if (tab === 'segments') setSelection(segment.id);
  }
  function scrub(time) {
    const nearest = frames.reduce((closest, candidate) => !closest || Math.abs(candidate.time_ms - time) < Math.abs(closest.time_ms - time) ? candidate : closest, null);
    setView({ ...NO_VIEW, segmentId: segmentAt(time)?.id ?? null, frameId: nearest?.frame_id ?? null, time });
    seek(time);
  }
  function selectItem(item, target = itemEvidence(item)[0]) {
    setTab('checks'); setSelection(item.key);
    if (target) show(target);
    else setView(previous => ({ ...previous, focus: null, key: '', time: null }));
  }
  function openItem(item, target) {
    if (!visibleItems.some(entry => entry.key === item.key)) setFilter('all');
    selectItem(item, target);
  }
  function selectTrack(track) {
    setSelection(track.id);
    show({ segment_id: segments.find(segment => segment.track_ids.includes(track.id))?.id, frame_id: track.rep_reading_frame, bbox_xyxy: track.bbox_xyxy,
      time_ms: frames.find(frame => frame.frame_id === track.rep_reading_frame)?.time_ms ?? track.start_ms, text: track.text, label: track.id });
  }
  function selectSegment(segment) {
    setSelection(segment.id);
    setView(segmentView(segment)); seek(segment.start_ms);
  }
  function changeTab(next) {
    setTab(next);
    if (next === 'tracks' && tracks[0]) selectTrack(tracks[0]);
    if (next === 'segments' && segments[0]) selectSegment(segments.find(segment => segment.id === view.segmentId) || segments[0]);
    if (next === 'checks' && visibleItems[0]) selectItem(visibleItems[0]);
  }
  function changeFilter(next) {
    setFilter(next);
    const visible = filterItems(items, next);
    if (!visible.length) { setSelection(null); setView(previous => ({ ...previous, focus: null, key: '', time: null })); }
    else if (!visible.some(item => item.key === selectedItem?.key)) selectItem(visible[0]);
  }
  function selectOverlay(id) {
    if (id === 'focus') return;
    const line = frameLines.find(item => item.line_id === id);
    if (line) setView(previous => ({ ...previous, key: '', focus: { bbox: line.bbox_xyxy, text: line.text, label: line.kept ? '고른 글자 줄' : '제외한 글자 줄' } }));
  }

  // 항목을 누르면 왼쪽 프레임을 그 항목의 근거로 옮기고 판정 근거 창을 연다.
  const evidenceItem = evidenceKeyOpen ? items.find(item => item.key === evidenceKeyOpen) : null;
  const evidenceIndex = evidenceItem ? visibleItems.indexOf(evidenceItem) : -1;
  function openEvidence(item) { selectItem(item); setEvidenceKeyOpen(item.key); }
  function closeEvidence() {
    setEvidenceKeyOpen(null);
    rail.current?.querySelector('.rv-video-verdict-trigger.is-selected')?.focus();
  }
  const subtitle = frame ? joinLabels(formatTime(frame.time_ms), samplingLabels[frame.sampling] || '표본', `글자 줄 남김 ${kept} · 제외 ${frameLines.length - kept}`,
    frame.ocr_status === 'failed' && 'OCR 실패', selectedItem && !evidence.length && '이 항목에는 위치 근거가 없어요')
    : '타임라인에서 시점을 고르거나 항목의 근거를 선택하세요.';

  return <section className="rv-results rv-video-results" aria-labelledby="page-title">
    <h1 id="page-title" className="sr-only" tabIndex={-1}>{job.project_name || '숏폼 검수'} 검수 결과</h1>
    <div className="rv-result-notice rv-video-result-notice">
      <p>자동 검수를 완료했어요. 판정은 검사한 표본 시각을 기준으로 합니다.</p>
      <LogoFlowInline jobs={logo?.jobs} error={logo?.error} notice={logoWarning} />
    </div>
    {job.warnings?.length > 0 && <ul className="rv-video-warning-list is-inline">{job.warnings.map(warning => <li key={warning}><Icon name="warning" />{warning}</li>)}</ul>}

    <div className="rv-video-workbench">
      <div className="rv-video-center">
        <div className="rv-video-evidence-stage">
          <ImageStage src={frame ? `/api/video-reviews/${job.id}/frames/${frame.frame_id}` : null}
            alt={frame ? `${formatTime(frame.time_ms)} 프레임과 읽은 글자 영역` : '근거 프레임'}
            heading={false} bare toolbarNote={subtitle} width={frame?.width} height={frame?.height}
            overlays={overlays} selectedOverlayId={view.focus ? 'focus' : null} onOverlaySelect={selectOverlay} viewKey={view.key}
            empty={<><Icon name="image" /><strong className="rv-video-empty-title">표시할 표본 프레임이 없어요.</strong><p>다른 시점을 고르거나 원본 영상을 확인해 주세요.</p></>} />
        </div>
        <Timeline job={job} view={view} evidence={evidence} currentTime={currentTime} shortcuts={shortcuts}
          onSelect={showSegment} onScrub={scrub} onShortcut={openItem} />
        <details className="rv-panel rv-video-original"><summary>원본 영상 재생</summary>
          <PlayerPane job={job} player={player} currentTime={currentTime} onTime={setCurrentTime} target={view.time} onSeekTarget={() => seek(view.time)} />
        </details>
      </div>

      <aside className="rv-panel rv-video-rail" aria-labelledby="rv-video-rail-title">
        <header className="rv-video-rail-heading"><div><h2 id="rv-video-rail-title">검수 요구사항</h2>
          <p>항목을 누르면 판정 근거가 창으로 열립니다.</p></div><span>{items.length}개 항목</span></header>
        <div className="rv-video-rail-body" ref={rail}>
          {tab === 'checks' && <>
            <div className="rv-result-filters" role="group" aria-label="검수 결과 상태 필터">
              {['all', 'pass', 'review', 'fail'].map(status => <button type="button" key={status} className={filter === status ? 'is-active' : ''}
                aria-pressed={filter === status} onClick={() => changeFilter(status)}>
                {status === 'all' ? '전체' : statusLabels[status]}<span>{filterItems(items, status).length}</span></button>)}
            </div>
            {visibleItems.map(item => {
              const active = selection === item.key;
              return <div className="rv-video-verdict" key={item.key}>
                <button type="button" className={`rv-video-verdict-trigger${active ? ' is-selected' : ''}`} aria-pressed={active} aria-haspopup="dialog"
                  onClick={() => openEvidence(item)}>
                  <span className="rv-video-verdict-index">{String(items.indexOf(item) + 1).padStart(2, '0')}</span>
                  <strong>{itemTitle(item)}</strong><StatusLabel status={item.status} />
                </button>
              </div>;
            })}
            {!visibleItems.length && <p className="rv-video-rail-note" role="status">{items.length ? '이 분류에 해당하는 요구사항이 없어요.' : '기록된 항목별 판정이 없어요.'}</p>}
          </>}
          {tab === 'tracks' && <>
            {tracks.length ? <ul className="rv-result-items">{tracks.map(track => <li key={track.id}>
              <button type="button" className={`rv-result-item-button${track.id === selection ? ' is-selected' : ''}`}
                aria-pressed={track.id === selection} aria-controls="rv-video-aux-detail" onClick={() => selectTrack(track)}>
                <span className="rv-video-time">{formatTime(track.start_ms)}–{formatTime(track.end_ms)}</span>
                <span className="rv-result-item-title">{track.text}</span>
              </button></li>)}</ul> : <p className="rv-video-rail-note">읽은 자막 트랙이 없습니다.</p>}
          </>}
          {tab === 'segments' && <>
            {segments.length ? <ul className="rv-result-items">{segments.map(segment => <li key={segment.id}>
              <button type="button" className={`rv-result-item-button${segment.id === selection ? ' is-selected' : ''}`}
                aria-pressed={segment.id === selection} aria-controls="rv-video-aux-detail" onClick={() => selectSegment(segment)}>
                <span className="rv-video-time">{formatTime(segment.start_ms)}–{formatTime(segment.end_ms)}</span>
                <span className="rv-result-item-title">{segment.track_ids.map(id => tracks.find(track => track.id === id)?.text).filter(Boolean).join(' · ') || '자막 없음'}</span>
              </button></li>)}</ul> : <p className="rv-video-rail-note">기록된 구간이 없습니다.</p>}
          </>}
          {tab !== 'checks' && <div className="rv-video-verdict-detail" id="rv-video-aux-detail">
            {selectedTrack && <TrackDetail track={selectedTrack} frames={frames} activeKey={view.key} onShow={show} />}
            {selectedSegment && <SegmentDetail segment={selectedSegment} tracks={tracks} frames={frames} items={items} onSelectItem={openItem} />}
          </div>}
          <div className="rv-video-tabs" role="group" aria-label="목록 종류">
            {[['checks', '검수 항목', items.length], ['tracks', '자막 트랙', tracks.length], ['segments', '구간', segments.length]].map(([key, label, count]) =>
              <button type="button" key={key} className={tab === key ? 'is-active' : ''} aria-pressed={tab === key} onClick={() => changeTab(key)}>{label}<span>{count}</span></button>)}
          </div>
          {job.disclosure && <VideoEvidenceGraph jobId={job.id} onFrame={show} />}
          {job.workflow && <VideoWorkflowActions {...{ job, onDecision, onRevise, onOpenRun }} />}
          <RuntimeDetails job={job} />
        </div>
      </aside>
    </div>

    <footer className="rv-result-footer rv-video-result-actions"><div>
      <button type="button" className="rv-button secondary" onClick={onReuse}>기준을 복사해 새 검수</button>
      <button type="button" className="rv-button secondary" onClick={onNewReview}>새 검수 시작하기</button>
      <button type="button" className="rv-button primary" onClick={onReport}>보고서 생성하기<Icon name="arrow" /></button></div>
    </footer>
    {evidenceItem && <VideoEvidenceDialog job={job} item={evidenceItem} number={String(items.indexOf(evidenceItem) + 1).padStart(2, '0')}
      position={evidenceIndex >= 0 ? { index: evidenceIndex + 1, total: visibleItems.length } : null} onFrame={show}
      onPrev={evidenceIndex > 0 ? () => openEvidence(visibleItems[evidenceIndex - 1]) : undefined}
      onNext={evidenceIndex >= 0 && evidenceIndex < visibleItems.length - 1 ? () => openEvidence(visibleItems[evidenceIndex + 1]) : undefined}
      onClose={closeEvidence} />}
  </section>;
}
