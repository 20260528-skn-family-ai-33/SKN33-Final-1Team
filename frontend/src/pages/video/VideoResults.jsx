import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '../../components/Icon.jsx';
import ImageStage from '../../components/ImageStage.jsx';
import { NoMatchArt } from '../../components/ReviewArt.jsx';
import { LogoFlowInline } from './LogoFlowParts.jsx';
import {
  coverageNote, diffChars, filterItems, formatTime, judgmentItems, mismatchLabels, reviewerLabels, roleLabels, ruleLabels, severityLabels, statusLabels,
} from './videoFormat.js';

const EMPTY = [];
const NO_VIEW = { segmentId: null, frameId: null, focus: null, key: '', time: null };
const typeLabels = { information: '정보 검수', visual: '시각 검수' };
const samplingLabels = { base: '1초 표본', head: '시작 0.2초 표본', tail: '끝 0.2초 표본' };
const groupTitles = { criteria: '기준별 결과', whole: '영상 전체에서 확인할 항목', disclosure: '광고·협찬 표시' };
const evidenceKey = item => (item ? `${item.frame_id}:${(item.bbox_xyxy || []).join(',')}:${item.time_ms}` : '');

function StatusLabel({ status }) {
  return <span className={`rv-result-status ${status || 'review'}`}><span aria-hidden="true" />{statusLabels[status] || '확인 필요'}</span>;
}

function joinLabels(...values) { return values.filter(Boolean).join(' · '); }

// 광고 표시 규정 중 화면 위치가 있는 근거(R1·R3)만 모은다.
function disclosureEvidence(disclosure) {
  return Object.entries(disclosure?.rules || {}).flatMap(([key, rule]) => (rule.evidence || [])
    .filter(item => item?.frame_id && Number.isFinite(item.time_ms)).map(item => ({ ...item, label: key })));
}

// 기준 문구와 화면 문구를 나란히 두고 다른 글자만 강조한다.
function MismatchRow({ item }) {
  const variants = (item.variants || []).map(value => (typeof value === 'string' ? value : value?.text)).filter(Boolean);
  return <>
    <span className="rv-video-mismatch-kind">{mismatchLabels[item.kind] || item.kind}</span>
    {item.expected ? <dl className="rv-video-mismatch-pair"><div><dt>기준</dt><dd>{item.expected}</dd></div>
      <div><dt>화면</dt><dd>{diffChars(item.expected, item.observed).map(([ch, different], i) => different ? <mark key={i}>{ch}</mark> : <React.Fragment key={i}>{ch}</React.Fragment>)}</dd></div></dl>
      : <dl className="rv-video-mismatch-pair"><div><dt>대표</dt><dd>{item.observed}</dd></div><div><dt>다르게 읽힘</dt><dd>{variants.join(' / ') || '–'}</dd></div></dl>}
  </>;
}

function EvidenceButton({ item, active, onShow, children }) {
  return <button type="button" className={`rv-video-evidence-button${active ? ' is-active' : ''}`} aria-pressed={active} onClick={() => onShow(item)}>
    {children}<span className="rv-video-evidence-go">{active ? '보는 중' : '위치 보기'}</span>
  </button>;
}

function CriterionDetail({ result, activeKey, onShow }) {
  const evidence = result.evidence_segments || EMPTY;
  return <>
    <div className="rv-result-verdict"><StatusLabel status={result.status} />
      <span>{joinLabels(typeLabels[result.type], severityLabels[result.severity], reviewerLabels[result.reviewer] && `${reviewerLabels[result.reviewer]} 판정`)}</span></div>
    {result.whole_video && <p className="rv-video-note"><Icon name="info" />영상 전체에서 확인할 항목입니다.</p>}
    <section className="rv-result-detail-section"><h3>검수 기준</h3><p className="rv-result-selected-criterion">{result.criterion}</p></section>
    <section className="rv-result-detail-section"><h3>화면에서 읽은 문구</h3>
      <p className={`rv-result-observation${result.observed_text ? '' : ' is-empty'}`}>{result.observed_text || '기록된 관측 문구가 없습니다. 판정 이유와 근거 프레임을 확인해 주세요.'}</p></section>
    <section className="rv-result-detail-section"><h3>판정 이유</h3><p>{result.reason || '제공된 판정 이유가 없습니다. 원본 영상과 기준을 직접 확인해 주세요.'}</p></section>
    {result.mismatch?.length > 0 && <section className="rv-result-detail-section"><h3>불일치 <span>{result.mismatch.length}</span></h3>
      <ul className="rv-video-mismatch">{result.mismatch.map((item, i) => <li key={i}><MismatchRow item={item} /></li>)}</ul>
      <p className="rv-video-note"><Icon name="warning" />OCR이 잘못 읽었을 수도 있으니 원본을 확인해 주세요.</p></section>}
    <section className="rv-result-detail-section"><h3>근거 구간 <span>{evidence.length}</span></h3>
      {evidence.length ? <ol className="rv-video-evidence">{evidence.map((item, i) => <li key={i}>
        <EvidenceButton item={item} active={activeKey === evidenceKey(item)} onShow={onShow}>
          <span className="rv-video-time">{formatTime(item.start_ms)}~{formatTime(item.end_ms)}</span>
          <span className="rv-video-evidence-text">{item.text || '문구 없이 위치만 제공된 근거입니다.'}</span>
        </EvidenceButton></li>)}</ol>
        : <p className="rv-result-muted">연결된 근거 구간이 없습니다.</p>}
    </section>
  </>;
}

function DisclosureDetail({ disclosure, activeKey, onShow }) {
  const rules = Object.entries(disclosure.rules || {});
  return <>
    <div className="rv-result-verdict"><StatusLabel status={disclosure.status} />
      <span>{joinLabels('광고·협찬 표시', severityLabels[disclosure.severity], reviewerLabels[disclosure.reviewer] && `${reviewerLabels[disclosure.reviewer]} 판정`)}</span></div>
    <section className="rv-result-detail-section"><h3>판정 이유</h3><p>{disclosure.reason || '제공된 판정 이유가 없습니다.'}</p></section>
    {disclosure.applicable && <section className="rv-result-detail-section"><h3>표시 문구·시간 (표본 기준)</h3>
      <p><StatusLabel status={disclosure.text_time_status} /></p>
      <p className="rv-video-note"><Icon name="info" />표본 문구 확인과 표시 규정 충족은 다른 상태입니다.</p></section>}
    {rules.length > 0 && <section className="rv-result-detail-section"><h3>규정별 확인 <span>{rules.length}</span></h3>
      <ul className="rv-video-rules">{rules.map(([key, rule]) => {
        const located = (rule.evidence || []).filter(item => item?.frame_id && Number.isFinite(item.time_ms));
        return <li key={key}>
          <div className="rv-video-rule-head"><span className="rv-video-rule-key">{key}</span><strong>{ruleLabels[key] || key}</strong><StatusLabel status={rule.status} /></div>
          {rule.note && <p>{rule.note}</p>}
          {located.length > 0 && <div className="rv-video-rule-evidence">{located.slice(0, 6).map((item, i) => {
            const target = { ...item, label: key };
            return <button type="button" key={i} className={activeKey === evidenceKey(target) ? 'is-active' : ''} aria-pressed={activeKey === evidenceKey(target)}
              onClick={() => onShow(target)}>{formatTime(item.time_ms)} 위치 보기</button>;
          })}{located.length > 6 && <span>외 {located.length - 6}곳</span>}</div>}
        </li>;
      })}</ul></section>}
    {disclosure.notes?.length > 0 && <section className="rv-result-detail-section"><h3>참고</h3>{disclosure.notes.map(note => <p key={note}>{note}</p>)}</section>}
  </>;
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
  return <section className="image-stage rv-video-player" aria-label="원본 영상">
    <div className="image-stage-heading"><div className="image-stage-file"><span className="image-stage-icon"><Icon name="film" /></span>
      <div><strong>원본 영상</strong><span>{media ? `${formatTime(media.duration_ms)} · ${media.width}×${media.height}` : '영상 정보 없음'}</span></div></div>
      <span className="image-stage-original">ORIGINAL</span></div>
    <div className="image-stage-viewport rv-video-player-viewport">
      {unavailable || failed ? <div className="image-stage-empty"><Icon name="film" /><strong className="rv-video-empty-title">영상을 재생할 수 없어요.</strong>
        <p>{unavailable ? job.playback?.reason || '브라우저에서 재생할 수 없는 형식입니다.' : '브라우저가 이 영상을 재생하지 못했습니다.'}<br />근거 프레임으로 확인하거나 원본을 내려받아 확인해 주세요.</p>
        <a className="rv-button secondary" href={source} download={`video-review-${job.id}.${extension}`}>원본 영상 내려받기</a></div>
        : <video ref={player} controls preload="metadata" playsInline src={source}
          onTimeUpdate={event => onTime(event.currentTarget.currentTime * 1000)} onSeeked={event => onTime(event.currentTarget.currentTime * 1000)}
          onError={() => setFailed(true)} />}
    </div>
    <div className="image-stage-toolbar"><span className="rv-video-timecode">재생 위치 <strong>{formatTime(currentTime)}</strong></span>
      <button type="button" className="image-stage-focus" title={seekLabel} disabled={target == null || unavailable || failed} onClick={onSeekTarget}>
        <Icon name="clock" />{seekLabel}</button></div>
  </section>;
}

// 구간 타임라인: 검사 시점은 점, 미검사 구간은 빗금, 선택 항목의 근거는 강조, 재생 위치는 세로선으로 표시한다.
// 구간 버튼은 마우스 보조용이다. 키보드로는 왼쪽 목록의 '구간' 탭에서 같은 구간을 고른다.
function Timeline({ job, view, evidence, currentTime, onSelect }) {
  const duration = job.coverage?.duration_ms || job.media?.duration_ms || 1;
  const pct = ms => `${Math.min(100, Math.max(0, ms / duration * 100))}%`;
  const evidenceSegments = new Set(evidence.map(item => item.segment_id).filter(Boolean));
  const tracks = Object.fromEntries((job.tracks || []).map(track => [track.id, track]));
  const note = coverageNote(job.coverage);
  return <section className="rv-panel rv-video-timeline" aria-labelledby="video-timeline-title">
    <div className="rv-video-timeline-heading"><div><h2 id="video-timeline-title">구간 타임라인</h2>{note && <p>{note}</p>}</div>
      <ul className="rv-video-legend" aria-label="타임라인 범례"><li className="is-text">자막 구간</li><li className="is-evidence">선택 항목 근거</li><li className="is-current">보는 구간</li><li className="is-playhead">재생 위치</li></ul></div>
    <div className="rv-video-timeline-body">
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
      <div className="rv-video-timeline-scale"><span>0:00</span><span>점 = 검사한 시점 · 빗금 = 검사하지 않은 구간</span><span>{formatTime(duration)}</span></div>
    </div>
  </section>;
}

// 결과 화면: 포스터 결과와 같은 3단(목록·근거·상세)에 영상 재생과 구간 타임라인을 더한다.
export default function VideoResults({ job, logo, logoWarning, onNewReview, onReuse }) {
  const player = useRef(null);
  const inspector = useRef(null);
  const railScroll = useRef(null);
  const items = useMemo(() => judgmentItems(job), [job]);
  const tracks = job.tracks || EMPTY;
  const segments = job.segments || EMPTY;
  const frames = job.frames || EMPTY;
  const results = job.review?.results || EMPTY;
  const counts = job.counts || {};
  const [tab, setTab] = useState('checks');
  const [filter, setFilter] = useState('all');
  const [selection, setSelection] = useState(() => (items[0] ? { kind: 'item', key: items[0].key } : tracks[0] ? { kind: 'track', id: tracks[0].id } : null));
  const [currentTime, setCurrentTime] = useState(0);
  const segmentAt = ms => segments.find(segment => ms >= segment.start_ms && ms < segment.end_ms) || segments.at(-1);
  // 구간의 대표 프레임이 없으면(1초 표본이 들어 있지 않은 짧은 구간) 구간 안의 0.2초 표본이라도 보여 준다.
  const segmentView = segment => (segment ? { segmentId: segment.id, focus: null, key: '', time: segment.start_ms,
    frameId: segment.rep_frame_id || segment.frame_ids?.[0] || frames.find(item => item.time_ms >= segment.start_ms && item.time_ms < segment.end_ms)?.frame_id || null } : NO_VIEW);
  const evidenceView = item => ({ segmentId: item.segment_id || segmentAt(item.time_ms)?.id || null, frameId: item.frame_id, key: evidenceKey(item), time: item.time_ms,
    focus: item.bbox_xyxy ? { bbox: item.bbox_xyxy, text: item.text, label: item.label || '선택 근거' } : null });
  const [view, setView] = useState(() => {
    const first = items[0];
    const evidence = first?.kind === 'criterion' ? first.result.evidence_segments?.[0] : first ? disclosureEvidence(first.disclosure)[0] : null;
    return evidence ? evidenceView(evidence) : segmentView(segments.find(segment => segment.track_ids.length) || segments[0]);
  });

  const visibleItems = useMemo(() => filterItems(items, filter), [items, filter]);
  const statusCount = status => items.filter(item => item.status === status).length;
  const selectedItem = selection?.kind === 'item' ? items.find(item => item.key === selection.key) : null;
  const selectedTrack = selection?.kind === 'track' ? tracks.find(track => track.id === selection.id) : null;
  const selectedSegment = selection?.kind === 'segment' ? segments.find(segment => segment.id === selection.id) : null;
  const evidence = selectedItem?.kind === 'criterion' ? selectedItem.result.evidence_segments || EMPTY
    : selectedItem?.kind === 'disclosure' ? disclosureEvidence(selectedItem.disclosure) : EMPTY;
  const frame = frames.find(item => item.frame_id === view.frameId);
  const frameLines = frame?.lines || EMPTY;
  const kept = frameLines.filter(line => line.kept).length;
  const overlays = useMemo(() => {
    if (!frame) return EMPTY;
    const lines = (frame.lines || EMPTY).map(line => ({ id: line.line_id, bbox_xyxy: line.bbox_xyxy, text: line.text, label: line.kept ? line.text : `제외 · ${line.text}`,
      tone: line.kept ? null : 'excluded', ariaLabel: `${line.kept ? '남긴 글자 줄' : '제외한 글자 줄'}: ${line.text}` }));
    return view.focus ? [...lines, { id: 'focus', bbox_xyxy: view.focus.bbox, text: view.focus.text, label: view.focus.label,
      ariaLabel: `${view.focus.label}${view.focus.text ? `: ${view.focus.text}` : ''}` }] : lines;
  }, [frame, view.focus]);
  // 고른 항목에 위치 근거가 없으면 지금 보이는 프레임이 그 항목의 근거처럼 읽히지 않게 제목을 바꾼다.
  const noEvidence = Boolean(selectedItem) && !evidence.length;
  const list = tab === 'checks' ? visibleItems : tab === 'tracks' ? tracks : segments;
  const listIndex = tab === 'checks' ? visibleItems.findIndex(item => item.key === selectedItem?.key)
    : tab === 'tracks' ? tracks.findIndex(track => track.id === selectedTrack?.id) : segments.findIndex(segment => segment.id === selectedSegment?.id);
  // 목록 배지는 입력한 기준 순서이고 ←/→는 묶어 보이는 목록 순서라, 상세 창에는 항목 이름과 목록 위치를 따로 적는다.
  const criterionNumber = result => String(results.indexOf(result) + 1).padStart(2, '0');
  const selectionLabel = selectedItem ? (selectedItem.kind === 'criterion' ? `기준 ${criterionNumber(selectedItem.result)}` : '광고 표시')
    : selectedTrack?.id || selectedSegment?.id || '';
  const selectionKey = selection ? `${selection.kind}:${selection.key ?? selection.id}` : '';

  useEffect(() => { if (inspector.current) inspector.current.scrollTop = 0; }, [selection?.kind, selection?.key, selection?.id]);
  // 탭을 바꾸거나 상세 창의 ←/→로 고르면 목록에서도 고른 줄이 보이게 한다. 페이지가 아니라 목록 상자만 움직인다.
  useLayoutEffect(() => {
    const box = railScroll.current;
    const row = box?.querySelector('.rv-result-item-button.is-selected');
    if (!row) return;
    if (row === box.querySelector('.rv-result-item-button')) { box.scrollTop = 0; return; }
    const outer = box.getBoundingClientRect(), inner = row.getBoundingClientRect();
    if (inner.top < outer.top) box.scrollTop -= outer.top - inner.top + 8;
    else if (inner.bottom > outer.bottom) box.scrollTop += inner.bottom - outer.bottom + 8;
  }, [tab, selectionKey]);

  function seek(ms) {
    if (!Number.isFinite(ms)) return;
    setCurrentTime(ms);
    const video = player.current;
    if (video) { video.currentTime = ms / 1000; video.pause(); }
  }
  function show(item) { setView(evidenceView(item)); seek(item.time_ms); }
  function showSegment(segment) {
    setView(segmentView(segment)); seek(segment.start_ms);
    if (tab === 'segments') setSelection({ kind: 'segment', id: segment.id });
  }
  // 항목을 고르고 첫 근거를 보여 준다. 근거가 없으면 이전 항목의 근거 상자·이동 시각을 지운다.
  function focusItem(item) {
    setTab('checks'); setSelection({ kind: 'item', key: item.key });
    const first = item.kind === 'criterion' ? item.result.evidence_segments?.[0] : disclosureEvidence(item.disclosure)[0];
    if (first) show(first);
    else setView(previous => ({ ...previous, focus: null, key: '', time: null }));
  }
  function selectItem(item) {
    if (!visibleItems.some(entry => entry.key === item.key)) setFilter('all');
    focusItem(item);
  }
  function selectTrack(track) {
    setSelection({ kind: 'track', id: track.id });
    show({ segment_id: segments.find(segment => segment.track_ids.includes(track.id))?.id, frame_id: track.rep_reading_frame, bbox_xyxy: track.bbox_xyxy,
      time_ms: track.start_ms, text: track.text, label: track.id });
  }
  function selectSegment(segment) { setSelection({ kind: 'segment', id: segment.id }); setView(segmentView(segment)); seek(segment.start_ms); }
  function chooseIndex(index) {
    const entry = list[index]; if (!entry) return;
    if (tab === 'checks') selectItem(entry); else if (tab === 'tracks') selectTrack(entry); else selectSegment(entry);
  }
  function changeTab(next) {
    setTab(next);
    if (next === 'tracks' && tracks[0] && selection?.kind !== 'track') selectTrack(tracks[0]);
    if (next === 'segments' && segments[0] && selection?.kind !== 'segment') selectSegment(segments.find(segment => segment.id === view.segmentId) || segments[0]);
    if (next === 'checks' && visibleItems[0] && selection?.kind !== 'item') selectItem(visibleItems[0]);
  }
  // 필터를 바꾸면 보이는 첫 항목으로 옮기고, 보이는 항목이 없으면 숨은 항목의 근거를 화면에 남기지 않는다.
  function changeFilter(next) {
    setFilter(next);
    const visible = filterItems(items, next);
    if (!visible.length) { setSelection(null); setView(previous => ({ ...previous, focus: null, key: '', time: null })); }
    else if (!visible.some(item => item.key === selectedItem?.key)) focusItem(visible[0]);
  }
  function selectOverlay(id) {
    if (id === 'focus' || !frame) return;
    const line = frameLines.find(item => item.line_id === id);
    if (line) setView(previous => ({ ...previous, key: '', focus: { bbox: line.bbox_xyxy, text: line.text, label: line.kept ? '고른 글자 줄' : '제외한 글자 줄' } }));
  }

  const groups = ['criteria', 'whole', 'disclosure'].map(group => [group, visibleItems.filter(item => item.group === group)]).filter(([, entries]) => entries.length);
  const orientation = job.media && job.media.width > job.media.height ? 'is-landscape' : 'is-portrait';
  const disclosure = job.disclosure;

  return <section className="rv-video-results" aria-labelledby="page-title">
    <header className="rv-result-header rv-video-result-header">
      <div><p className="rv-eyebrow">VIDEO REVIEW RESULTS · 기준 v{job.criteria_version}</p>
        <h1 id="page-title" tabIndex={-1}>{job.project_name || '숏폼 검수'}</h1>
        <p className="rv-video-result-meta">{joinLabels(job.media && `${formatTime(job.media.duration_ms)} · ${job.media.width}×${job.media.height}`,
          counts.frames != null && `표본 프레임 ${counts.frames}장`, `자막 트랙 ${counts.tracks ?? tracks.length}`, `구간 ${counts.segments ?? segments.length}`,
          job.created_at && new Date(job.created_at).toLocaleString('ko-KR'))}</p></div>
      <LogoFlowInline jobs={logo?.jobs} error={logo?.error} notice={logoWarning} />
    </header>

    <div className="rv-result-summary rv-video-summary" aria-label="자동 검수 결과 요약">
      <span className="rv-result-completed"><Icon name="check" />자동 검수 완료</span>
      <span className="rv-result-total">기준 <strong>{results.length}</strong></span>
      {['pass', 'fail', 'review'].map(status => <span className={`rv-result-summary-count ${status}`} key={status}>
        <span className="rv-result-dot" aria-hidden="true" />{statusLabels[status]}<strong>{counts.results?.[status] ?? results.filter(row => row.status === status).length}</strong></span>)}
      <span className="rv-result-summary-count rv-video-mismatch-count"><span className="rv-result-dot" aria-hidden="true" />불일치<strong>{counts.mismatch ?? 0}</strong></span>
      {disclosure && <span className="rv-video-summary-disclosure">광고 표시 <StatusLabel status={disclosure.status} /></span>}
      <span className="rv-result-summary-notice">&lsquo;충족&rsquo;은 검사한 시점에서 충족이라는 뜻입니다.</span>
    </div>
    {job.warnings?.length > 0 && <ul className="rv-video-warning-list is-inline">{job.warnings.map(warning => <li key={warning}><Icon name="warning" />{warning}</li>)}</ul>}

    <div className="rv-video-workbench">
      <aside className="rv-panel rv-video-rail" aria-labelledby="rv-video-rail-title">
        <h2 id="rv-video-rail-title" className="visually-hidden">{{ checks: '검수 항목', tracks: '자막 트랙', segments: '구간' }[tab]} 목록</h2>
        <div className="rv-video-tabs" role="group" aria-label="목록 종류">
          {[['checks', '검수 항목', items.length], ['tracks', '자막 트랙', tracks.length], ['segments', '구간', segments.length]].map(([key, label, count]) =>
            <button type="button" key={key} className={tab === key ? 'is-active' : ''} aria-pressed={tab === key} onClick={() => changeTab(key)}>{label}<span>{count}</span></button>)}
        </div>
        {tab === 'checks' && <>
          <div className="rv-result-filters" role="group" aria-label="검수 결과 상태 필터">
            {[['all', '전체', items.length], ['fail', '위반', statusCount('fail')], ['review', '확인 필요', statusCount('review')], ['pass', '충족', statusCount('pass')]].map(([status, label, count]) =>
              <button type="button" key={status} className={filter === status ? 'is-active' : ''} aria-pressed={filter === status} onClick={() => changeFilter(status)}>{label}<span>{count}</span></button>)}
          </div>
          <p className="rv-video-rail-count" role="status">{visibleItems.length}개 표시</p>
        </>}
        <div ref={railScroll} className="rv-video-rail-scroll">
          {tab === 'checks' && <>
            {!results.length && filter === 'all' && <p className="rv-video-rail-note">입력한 기준이 없어 기준 판정을 하지 않았습니다.</p>}
            {groups.map(([group, entries]) => <div key={group} className="rv-video-rail-group"><h3>{groupTitles[group]}</h3>
              <ul className="rv-result-items">{entries.map(item => {
                const selected = selectedItem?.key === item.key;
                const result = item.result;
                return <li key={item.key}><button type="button" className={`rv-result-item-button${selected ? ' is-selected' : ''}`} aria-pressed={selected}
                  aria-controls="rv-video-inspector" onClick={() => selectItem(item)}>
                  <span className="rv-result-item-meta"><StatusLabel status={item.status} />
                    <span>{item.kind === 'criterion' ? criterionNumber(result) : ''}</span></span>
                  <span className="rv-result-item-title">{item.kind === 'criterion' ? result.criterion : '광고·협찬 표시'}</span>
                  {item.kind === 'criterion' ? <span className="rv-result-item-subtitle">{joinLabels(severityLabels[result.severity], reviewerLabels[result.reviewer])}
                    <span>{result.mismatch?.length ? `불일치 ${result.mismatch.length}` : `근거 ${result.evidence_segments?.length || 0}구간`}</span></span>
                    : <span className="rv-result-item-subtitle">표시 규정 R0–R6<span>{item.disclosure.applicable ? `문구·시간 ${statusLabels[item.disclosure.text_time_status] || '확인 필요'}` : '검수 제외'}</span></span>}
                </button></li>;
              })}</ul></div>)}
            {!visibleItems.length && items.length > 0 && <div className="rv-result-empty"><NoMatchArt /><strong>일치하는 항목이 없습니다.</strong>
              <button type="button" className="rv-button secondary" onClick={() => changeFilter('all')}>전체 보기</button></div>}
          </>}
          {tab === 'tracks' && (tracks.length ? <ul className="rv-result-items">{tracks.map(track => {
            const selected = selectedTrack?.id === track.id;
            return <li key={track.id}><button type="button" className={`rv-result-item-button${selected ? ' is-selected' : ''}`} aria-pressed={selected}
              aria-controls="rv-video-inspector" onClick={() => selectTrack(track)}>
              <span className="rv-result-item-meta"><span className="rv-video-time">{formatTime(track.start_ms)}–{formatTime(track.end_ms)}</span><span>{track.id}</span></span>
              <span className="rv-result-item-title">{track.text}</span>
              <span className="rv-result-item-subtitle">{joinLabels(roleLabels[track.role] || track.role, track.vote && `투표 ${track.vote.count}/${track.vote.total}`)}
                <span>{joinLabels(track.requires_review && '과반 없음', track.variants?.length && `다르게 읽힘 ${track.variants.length}`)}</span></span>
            </button></li>;
          })}</ul> : <p className="rv-video-rail-note">읽은 자막 트랙이 없습니다.</p>)}
          {tab === 'segments' && <ul className="rv-result-items">{segments.map(segment => {
            const selected = selectedSegment?.id === segment.id;
            const text = segment.track_ids.map(id => tracks.find(track => track.id === id)?.text).filter(Boolean).join(' · ');
            const used = results.some(result => (result.evidence_segments || []).some(item => item.segment_id === segment.id));
            return <li key={segment.id}><button type="button" className={`rv-result-item-button${selected ? ' is-selected' : ''}`} aria-pressed={selected}
              aria-controls="rv-video-inspector" onClick={() => selectSegment(segment)}>
              <span className="rv-result-item-meta"><span className="rv-video-time">{formatTime(segment.start_ms)}–{formatTime(segment.end_ms)}</span><span>{segment.id}</span></span>
              <span className={`rv-result-item-title${text ? '' : ' is-muted'}`}>{text || '자막 없음'}</span>
              <span className="rv-result-item-subtitle">1초 표본 {segment.frame_ids?.length || 0}장<span>{used ? '판정 근거 구간' : ''}</span></span>
            </button></li>;
          })}</ul>}
        </div>
      </aside>

      <div className="rv-video-center">
        <div className={`rv-video-monitor ${orientation}`}>
          <PlayerPane job={job} player={player} currentTime={currentTime} onTime={setCurrentTime} target={view.time} onSeekTarget={() => seek(view.time)} />
          <ImageStage src={frame ? `/api/video-reviews/${job.id}/frames/${frame.frame_id}` : null} alt={frame ? `${formatTime(frame.time_ms)} 프레임과 읽은 글자 영역` : '근거 프레임'}
            title={frame ? `${noEvidence ? '구간 대표 프레임' : '근거 프레임'} ${formatTime(frame.time_ms)}` : '근거 프레임'}
            subtitle={frame ? joinLabels(noEvidence && '이 항목에는 위치 근거가 없어요', samplingLabels[frame.sampling] || '표본', `글자 줄 남김 ${kept} · 제외 ${frameLines.length - kept}`, frame.ocr_status === 'failed' && 'OCR 실패')
              : '구간이나 근거를 고르면 대표 프레임을 보여 줍니다.'}
            width={frame?.width} height={frame?.height} overlays={overlays} selectedOverlayId={view.focus ? 'focus' : null} onOverlaySelect={selectOverlay} viewKey={selectionKey}
            empty={view.segmentId ? <><Icon name="image" /><strong className="rv-video-empty-title">이 구간에는 표본 프레임이 없어요.</strong><p>표본 시각이 들어 있지 않은 짧은 구간입니다. 원본 영상으로 확인해 주세요.</p></>
              : <><Icon name="image" /><strong className="rv-video-empty-title">근거 프레임</strong><p>구간을 고르면 대표 프레임과 글자 위치를 보여 줍니다.</p></>} />
        </div>
        <Timeline job={job} view={view} evidence={evidence} currentTime={currentTime} onSelect={showSegment} />
      </div>

      <aside id="rv-video-inspector" className="rv-panel rv-video-inspector" aria-labelledby="rv-video-inspector-title">
        <div className="rv-result-inspector-heading"><div><p className="rv-eyebrow">INSPECTOR</p><h2 id="rv-video-inspector-title">선택 항목 상세</h2></div>
          <div className="rv-result-item-navigation"><span className="rv-video-nav-position" title="←/→는 왼쪽 목록에 보이는 순서대로 이동합니다.">
            {selectionLabel && <strong>{selectionLabel}</strong>}{listIndex < 0 ? `목록 ${list.length}개` : `목록 ${listIndex + 1}/${list.length}`}</span>
            <button type="button" aria-label="이전 항목" disabled={listIndex <= 0} onClick={() => chooseIndex(listIndex - 1)}>←</button>
            <button type="button" aria-label="다음 항목" disabled={listIndex < 0 || listIndex >= list.length - 1} onClick={() => chooseIndex(listIndex + 1)}>→</button></div>
        </div>
        <div ref={inspector} className="rv-result-inspector-body">
          {selectedItem?.kind === 'criterion' ? <CriterionDetail result={selectedItem.result} activeKey={view.key} onShow={show} />
            : selectedItem?.kind === 'disclosure' ? <DisclosureDetail disclosure={selectedItem.disclosure} activeKey={view.key} onShow={show} />
              : selectedTrack ? <TrackDetail track={selectedTrack} frames={frames} activeKey={view.key} onShow={show} />
                : selectedSegment ? <SegmentDetail segment={selectedSegment} tracks={tracks} frames={frames} items={items} onSelectItem={selectItem} />
                  : <div className="rv-result-empty"><NoMatchArt /><strong>표시할 항목이 없습니다.</strong><p>목록에서 항목을 고르면 기준·문구·근거를 확인할 수 있습니다.</p></div>}
          <RuntimeDetails job={job} />
        </div>
      </aside>
    </div>

    <footer className="rv-result-footer"><p><Icon name="shield" />검수 실행 완료는 최종 승인을 의미하지 않습니다. 확인 필요 항목은 원본 영상으로 확인해 주세요.</p>
      <div><button type="button" className="rv-button secondary" onClick={onReuse}>같은 기준으로 다시 검수</button>
        <button type="button" className="rv-button primary" onClick={onNewReview}>새 동영상 검수 시작하기<Icon name="arrow" /></button></div>
    </footer>
  </section>;
}
