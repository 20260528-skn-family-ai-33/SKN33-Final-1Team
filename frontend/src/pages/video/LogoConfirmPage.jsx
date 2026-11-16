import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { fetchJson } from '../../api/client.js';
import { SiteHeader } from '../../components/SiteHeader.jsx';
import { WorkspaceProjectBar } from '../../layouts/ReviewLayout.jsx';
import { useWorkspaceReset } from '../../workspace/WorkspaceSession.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { autoDrafts, autoSelection, bundleOutcome, groupDifferences, handoff, intervalText, LINK_RATIO, linkCandidates, OUTCOME, toIntervals, topGroups,
  videoLogoSteps } from './logoFlow.js';
import { startAutoCompare } from './LogoFlowParts.jsx';
import './logoConfirm.css';

const RUNNING = new Set(['queued', 'running']);
const SKIP = { frame_missing: '프레임 없음', crop_too_small: '영역이 너무 작음' };

function timeText(sec) {
  const value = Math.max(0, Number(sec) || 0);
  return String(Math.floor(value / 60)).padStart(2, '0') + ':' + String(value % 60).padStart(2, '0');
}

function spanText(secs) {
  const ranges = toIntervals(secs || []);
  if (ranges.length > 3) return ranges.slice(0, 3).map(intervalText).join(', ') + ' 외 ' + (ranges.length - 3) + '구간';
  return ranges.map(intervalText).join(', ') || '시간 정보 없음';
}

function frameUrl(video, sec) {
  return video.base + '/' + video.key + '/f/' + sec + '.jpg';
}

function boxStyle(box, size) {
  const W = size[0] || 1;
  const H = size[1] || 1;
  return {
    left: (box[0] / W * 100) + '%',
    top: (box[1] / H * 100) + '%',
    width: ((box[2] - box[0]) / W * 100) + '%',
    height: ((box[3] - box[1]) / H * 100) + '%',
  };
}

// fit=[너비, 높이](선택): 이 크기 안에 로고 조각 전체가 들어가게 줄인다(작은 칸에서 잘려 보이지 않게).
function CropPreview({ video, sec, box, compact = false, fit }) {
  if (!video || !box) return <div className="vl-crop vl-crop-empty">미리보기 없음</div>;
  const W = video.size[0], H = video.size[1];
  const x1 = Math.max(0, Math.min(W - 1, box[0]));
  const y1 = Math.max(0, Math.min(H - 1, box[1]));
  const x2 = Math.max(x1 + 1, Math.min(W, box[2]));
  const y2 = Math.max(y1 + 1, Math.min(H, box[3]));
  const width = x2 - x1, height = y2 - y1;
  const [maxW, maxH] = fit || (compact ? [248, 112] : [350, 166]);
  const scale = Math.min(maxW / width, maxH / height);
  return <div className={'vl-crop' + (compact ? ' compact' : '') + (fit ? ' is-fit' : '')} aria-label={sec + '초 로고 영역'}>
    <span style={{
      width: width * scale,
      height: height * scale,
      backgroundImage: 'url("' + frameUrl(video, sec) + '")',
      backgroundSize: (W * scale) + 'px ' + (H * scale) + 'px',
      backgroundPosition: (-x1 * scale) + 'px ' + (-y1 * scale) + 'px',
    }} />
  </div>;
}

function FrameViewer({ video, sec, overlays = [], onSelect, className = '' }) {
  const missing = (video.missing || []).includes(sec);
  return <div className={'vl-frame ' + className} style={{ aspectRatio: video.size[0] + ' / ' + video.size[1] }}>
    <img src={frameUrl(video, sec)} alt={sec + '초 전체 프레임'} />
    {/* 이름표는 상자 바깥 위(화면 맨 위에 붙은 상자는 바깥 아래)에 두어 로고를 가리지 않는다.
        선택 여부는 글자 대신 상자 모양(점선 → 굵은 흰 테 + 잉크 외곽선, 이름표 앞 체크)으로 보인다. */}
    {overlays.map(item => <button
      type="button"
      key={item.id}
      className={'vl-box' + (item.selected ? ' selected' : '') + (item.box[1] / (video.size[1] || 1) < 0.08 ? ' label-below' : '')}
      style={boxStyle(item.box, video.size)}
      aria-label={item.label + (item.selected ? ' (선택됨)' : '')}
      aria-pressed={!!item.selected}
      title={item.label}
      onClick={() => onSelect && onSelect(item.id)}
    ><span>{item.selected && <i aria-hidden="true">✓</i>}{item.label}</span></button>)}
    {missing && <div className="vl-frame-missing">이 초의 프레임을 읽지 못했습니다.</div>}
    <span className="vl-frame-time">{timeText(sec)}</span>
  </div>;
}

function CandidateCard({ video, refIndex, groupIndex, rank, selected, onToggle }) {
  const group = video.groups[groupIndex];
  const representative = group.rep[refIndex];
  const [sec, box] = representative;
  const ocr = video.refs[refIndex].ocr[String(groupIndex)];
  return <article className={'vl-candidate' + (selected ? ' selected' : '')}>
    {/* 순위와 선택 표시는 로고 조각 위가 아니라 그 위쪽 줄에 작게 둬서 로고를 가리지 않는다. */}
    <button type="button" className="vl-candidate-image" onClick={onToggle} aria-pressed={selected} aria-label={'#' + rank + ' 후보 ' + (selected ? '선택 해제' : '선택')}>
      <span className="vl-candidate-top"><span className="vl-rank">#{rank}</span>
        <span className="vl-candidate-check" aria-hidden="true">{selected ? '✓' : ''}</span></span>
      <CropPreview video={video} sec={sec} box={box} compact />
    </button>
    <div className="vl-candidate-info">
      <div className="vl-candidate-score"><span>순위 점수</span><strong>{Number(group.rank[refIndex]).toFixed(3)}</strong></div>
      <p>{spanText(group.secs)}</p>
      <p className="vl-candidate-frames">{group.secs.length}개 프레임</p>
      {ocr && ocr[0] && <div className="vl-ocr-chip">
        <span>{ocr[1] ? '문구 유사' : 'OCR'}</span>{ocr[0]}
      </div>}
    </div>
  </article>;
}

function CandidatePlayer({ video, refIndex, sec, setSec, checked, onToggle }) {
  const [limit, setLimit] = useState(10);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  const last = Math.max(0, video.seconds - 1);
  const visible = useMemo(() => topGroups(video.groups, refIndex, limit), [video.groups, refIndex, limit]);
  const overlays = visible.flatMap((groupIndex, order) => {
    const group = video.groups[groupIndex];
    const box = group.box[refIndex][String(sec)];
    if (!box) return [];
    return [{
      id: groupIndex,
      box,
      selected: checked.has(groupIndex),
      label: '#' + (order + 1) + ' 후보',
    }];
  });
  // 지금 보이는 후보 묶음에 상자가 있는 시점(초). '이전·다음 후보' 단추와 Shift+←/→가 이 시점으로 건너뛴다.
  const candidateSecs = useMemo(() => [...new Set(visible.flatMap(groupIndex =>
    Object.keys(video.groups[groupIndex].box[refIndex] || {}).map(Number)))].sort((a, b) => a - b), [visible, video.groups, refIndex]);
  const prevSec = [...candidateSecs].reverse().find(value => value < sec);
  const nextSec = candidateSecs.find(value => value > sec);
  const step = delta => { setPlaying(false); setSec(value => Math.max(0, Math.min(last, value + delta))); };
  function keyDown(event) {
    if (event.target.closest('select, input, button')) return;
    if (event.key === ' ') { event.preventDefault(); setPlaying(value => !value); }
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      const forward = event.key === 'ArrowRight';
      if (event.shiftKey) {
        const target = forward ? nextSec : prevSec;
        if (target != null) { setPlaying(false); setSec(target); }
      } else step(forward ? 1 : -1);
    }
  }
  useEffect(() => {
    if (!playing) return undefined;
    const timer = setInterval(() => {
      setSec(value => {
        if (value >= last) {
          setPlaying(false);
          return 0;
        }
        return value + 1;
      });
    }, 1000 / speed);
    return () => clearInterval(timer);
  }, [playing, speed, last, setSec]);

  // 프레임을 누르거나 Tab으로 들어오면 ←/→(1초)·Shift+←/→(후보 시점)·Space(재생)로 움직인다.
  return <section className="vl-viewer-card vl-player" tabIndex={0} onKeyDown={keyDown} aria-label="후보 위치 영상. 왼쪽·오른쪽 화살표로 1초, Shift와 함께 누르면 후보 시점으로 이동합니다.">
    <div className="vl-section-heading vl-player-head">
      <h2>영상 프레임</h2>
      <div className="vl-player-jump">
        <button type="button" disabled={prevSec == null} onClick={() => { setPlaying(false); setSec(prevSec); }}>← 이전 후보</button>
        <button type="button" disabled={nextSec == null} onClick={() => { setPlaying(false); setSec(nextSec); }}>다음 후보 →</button>
      </div>
    </div>
    <FrameViewer video={video} sec={sec} overlays={overlays} onSelect={onToggle} />
    <div className="vl-player-controls">
      <button type="button" className="vl-play" onClick={() => setPlaying(value => !value)}>{playing ? '일시정지' : '재생'}</button>
      <button type="button" className="vl-step" aria-label="1초 앞으로" onClick={() => step(-1)} disabled={sec <= 0}>−1초</button>
      <input aria-label="영상 시점" type="range" min="0" max={last} value={Math.min(sec, last)} style={{ '--at': (last ? Math.min(sec, last) / last * 100 : 0) + '%' }}
        onChange={event => setSec(Number(event.target.value))} />
      <button type="button" className="vl-step" aria-label="1초 뒤로" onClick={() => step(1)} disabled={sec >= last}>+1초</button>
      <strong>{timeText(sec)} <span>/ {timeText(last)}</span></strong>
    </div>
    <div className="vl-player-options">
      <label>표시 후보
        <select value={limit} onChange={event => setLimit(Number(event.target.value))}>
          {[3, 10, 20, 50].map(value => <option key={value} value={value}>상위 {value}개</option>)}
        </select>
      </label>
      <label>속도
        <select value={speed} onChange={event => setSpeed(Number(event.target.value))}>
          {[1, 2, 4, 8].map(value => <option key={value} value={value}>{value}배</option>)}
        </select>
      </label>
    </div>
  </section>;
}

function GroupLinks({ video, refIndex, confirmed, checked, onToggle }) {
  const [expanded, setExpanded] = useState(false);
  const candidates = useMemo(
    () => linkCandidates(video.groups, refIndex, confirmed),
    [video.groups, refIndex, confirmed],
  );
  if (!confirmed.length) return null;
  const shown = expanded ? candidates : candidates.slice(0, 8);
  return <section className="vl-panel">
    <div className="vl-section-heading">
      <h2>같은 로고의 다른 구간</h2>
      <span className="vl-soft-tag">점수 {Math.round(LINK_RATIO * 100)}%+</span>
    </div>
    {!candidates.length ? <p className="vl-empty-inline">다른 구간이 없어요.</p> :
      <div className="vl-linked-list">{shown.map(groupIndex => {
        const group = video.groups[groupIndex];
        const [sec, box] = group.rep[refIndex];
        return <label key={groupIndex} className={'vl-linked-item' + (checked.has(groupIndex) ? ' selected' : '')}>
          <input type="checkbox" checked={checked.has(groupIndex)} onChange={() => onToggle(groupIndex)} />
          <CropPreview video={video} sec={sec} box={box} compact fit={[120, 64]} />
          <span className="vl-linked-copy"><strong>{spanText(group.secs)}</strong><small>점수 {Number(group.rank[refIndex]).toFixed(3)} · {group.secs.length}프레임</small></span>
        </label>;
      })}</div>}
    {candidates.length > 8 && <button type="button" className="vl-text-button" onClick={() => setExpanded(value => !value)}>
      {expanded ? '간단히 보기' : '후보 ' + candidates.length + '개 모두 보기'}
    </button>}
  </section>;
}

function ManualEditor({ video, initialSec, onCancel, onDone }) {
  const [sec, setSec] = useState(Math.min(video.seconds - 1, initialSec || 0));
  const [box, setBox] = useState(null);
  const [draft, setDraft] = useState(null);
  const [from, setFrom] = useState(Math.min(video.seconds - 1, initialSec || 0));
  const [to, setTo] = useState(Math.min(video.seconds - 1, initialSec || 0));
  const canvas = useRef(null);
  const dragStart = useRef(null);
  const W = video.size[0], H = video.size[1], last = Math.max(0, video.seconds - 1);

  function point(event) {
    const rect = canvas.current.getBoundingClientRect();
    return [
      Math.max(0, Math.min(W, (event.clientX - rect.left) * W / rect.width)),
      Math.max(0, Math.min(H, (event.clientY - rect.top) * H / rect.height)),
    ];
  }
  function pointerDown(event) {
    if (event.button !== 0) return;
    event.preventDefault();
    dragStart.current = point(event);
    setDraft(null);
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function pointerMove(event) {
    if (!dragStart.current) return;
    const end = point(event), start = dragStart.current;
    setDraft([Math.min(start[0], end[0]), Math.min(start[1], end[1]), Math.max(start[0], end[0]), Math.max(start[1], end[1])]);
  }
  function pointerUp(event) {
    if (!dragStart.current) return;
    const end = point(event), start = dragStart.current;
    const next = [Math.min(start[0], end[0]), Math.min(start[1], end[1]), Math.max(start[0], end[0]), Math.max(start[1], end[1])];
    dragStart.current = null;
    setDraft(null);
    if (next[2] - next[0] >= 8 && next[3] - next[1] >= 8) setBox(next);
  }
  function complete() {
    if (!box || from > to) return;
    onDone({ sec, box, secs: Array.from({ length: to - from + 1 }, (_, i) => from + i) });
  }

  return <section className="vl-panel vl-manual">
    <div className="vl-section-heading">
      <h2>로고 영역 직접 지정</h2>
      <button type="button" className="vl-text-button" onClick={onCancel}>후보 화면으로</button>
    </div>
    <p className="vl-help">프레임에서 로고를 드래그해 감싸고, 로고가 보이는 구간을 정하세요.</p>
    <div className="vl-manual-workspace">
      <div>
        <div ref={canvas} className="vl-manual-canvas" style={{ aspectRatio: W + ' / ' + H }}
          onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp}>
          <img src={frameUrl(video, sec)} alt={sec + '초 프레임'} draggable="false" />
          {box && <span className="vl-draw-box" style={boxStyle(box, video.size)} />}
          {draft && <span className="vl-draw-box drawing" style={boxStyle(draft, video.size)} />}
          <span className="vl-frame-time">{timeText(sec)}</span>
        </div>
        <div className="vl-manual-slider">
          <span>{timeText(sec)}</span>
          <input type="range" min="0" max={last} value={sec} aria-label="수동 지정 프레임" onChange={event => setSec(Number(event.target.value))} />
          <span>{timeText(last)}</span>
        </div>
      </div>
      <aside className="vl-manual-side">
        <div className="vl-manual-box">
          <strong>선택 영역</strong>
          {box ? <CropPreview video={video} sec={sec} box={box} /> : <p>영상 프레임 위를 드래그하세요.</p>}
          {box && <button type="button" className="vl-text-button" onClick={() => setBox(null)}>영역 다시 그리기</button>}
        </div>
        <div className="vl-range-fields">
          <label>구간 시작(초)<input type="number" min="0" max={last} value={from} onChange={event => setFrom(Math.max(0, Math.min(last, Number(event.target.value) || 0)))} /></label>
          <label>구간 끝(초)<input type="number" min="0" max={last} value={to} onChange={event => setTo(Math.max(0, Math.min(last, Number(event.target.value) || 0)))} /></label>
        </div>
        {from > to && <p className="vl-inline-error">끝 시점은 시작 시점보다 뒤여야 합니다.</p>}
        <div className="vl-manual-actions">
          <button type="button" className="vl-button secondary" onClick={onCancel}>취소</button>
          <button type="button" className="vl-button primary" disabled={!box || from > to} onClick={complete}>영역 확정</button>
        </div>
      </aside>
    </div>
  </section>;
}

function SelectionSummary({ video, refIndex, checked, confirmed, manuals, onRemoveGroup, onRemoveManual }) {
  const groups = [...checked];
  if (!groups.length && !manuals.length) return <div className="vl-selection-empty">선택한 후보가 없어요.</div>;
  return <div className="vl-selection-list">
    {groups.map(groupIndex => {
      const group = video.groups[groupIndex];
      const [sec, box] = group.rep[refIndex];
      const kind = confirmed.includes(groupIndex) ? '선택 후보' : '연결 후보';
      return <article className="vl-selected-item" key={'group-' + groupIndex}>
        <CropPreview video={video} sec={sec} box={box} compact fit={[120, 64]} />
        <div><span className="vl-mini-label">{kind}</span><strong>{spanText(group.secs)}</strong></div>
        <button type="button" aria-label="후보에서 제거" onClick={() => onRemoveGroup(groupIndex)}>×</button>
      </article>;
    })}
    {manuals.map((manual, index) => <article className="vl-selected-item" key={'manual-' + index}>
      <CropPreview video={video} sec={manual.sec} box={manual.box} compact fit={[120, 64]} />
      <div><span className="vl-mini-label manual">직접 지정</span><strong>{spanText(manual.secs)}</strong><small>{manual.secs.length}초 · 고정 영역</small></div>
      <button type="button" aria-label="직접 지정 영역 제거" onClick={() => onRemoveManual(index)}>×</button>
    </article>)}
  </div>;
}

function ExposureSummary({ video, payload }) {
  const missing = new Set(video.missing || []);
  const selected = new Set(payload.intervals.flatMap(([a, b]) => Array.from({ length: b - a + 1 }, (_, i) => a + i)));
  const width = Math.min(1000, Math.max(1, video.seconds));
  const slots = Array.from({ length: width }, (_, slot) => {
    const start = Math.floor(slot * video.seconds / width);
    const end = Math.max(start, Math.floor((slot + 1) * video.seconds / width) - 1);
    let isMissing = false, isSelected = false;
    for (let sec = start; sec <= end; sec += 1) {
      isMissing ||= missing.has(sec);
      isSelected ||= selected.has(sec);
    }
    return { start, end, isMissing, isSelected };
  });
  return <section className="vl-panel vl-exposure">
    <div className="vl-section-heading"><div><span className="vl-kicker">검수 범위</span><h2>선택한 구간</h2></div>
      <span className="vl-soft-tag">{payload.total_seconds}초</span></div>
    <div className="vl-timeline" role="img" aria-label="선택한 로고 구간 타임라인">
      {slots.map(({ start, end, isMissing, isSelected }) => <span key={start}
        className={(isMissing ? 'missing ' : '') + (isSelected ? 'selected' : '')}
        title={start === end ? start + '초' : start + '~' + end + '초'} />)}
    </div>
    <div className="vl-timeline-axis"><span>00:00</span><span>{timeText(video.seconds - 1)}</span></div>
    <p>{payload.intervals.length ? payload.intervals.map(intervalText).join(' · ') : '노출 구간을 지정하지 않았습니다.'}</p>
  </section>;
}

function CandidateWorkspace({ video, refIndex, checked, onToggle, sec, setSec, onConfirm, onNone, onManual, onLinkToggle, confirmed, manuals, onRemoveGroup, onRemoveManual, decision }) {
  const ranked = useMemo(() => topGroups(video.groups, refIndex, video.groups.length), [video.groups, refIndex]);
  const [visibleExtra, setVisibleExtra] = useState(0);
  useEffect(() => setVisibleExtra(0), [refIndex]);
  const top = ranked.slice(0, 3);
  const extraCount = Math.max(0, ranked.length - 3);
  const rest = ranked.slice(3, 3 + visibleExtra);
  return <div className="vl-main-stack">
    <div className="vl-stage-heading vl-confirm-heading">
      <h2>로고가 나오는 구간 고르기</h2>
      <div className="vl-stage-actions">
        <button type="button" className="vl-button subtle" onClick={onManual}>직접 지정</button>
        <button type="button" className="vl-button secondary" onClick={onNone}>영상에 없음</button>
      </div>
    </div>

    <div className="vl-review-grid">
      <div className="vl-review-left">
        <CandidatePlayer video={video} refIndex={refIndex} sec={sec} setSec={setSec} checked={checked} onToggle={onToggle} />
        <section className="vl-panel">
          <div className="vl-section-heading"><h2>후보 구간</h2>
            <span className="vl-soft-tag">여러 개 선택 가능</span></div>
          <div className="vl-candidate-grid">
            {top.map((groupIndex, index) => <CandidateCard key={groupIndex} video={video} refIndex={refIndex} groupIndex={groupIndex}
              rank={index + 1} selected={checked.has(groupIndex)} onToggle={() => {
                setSec(video.groups[groupIndex].rep[refIndex][0]); onToggle(groupIndex);
              }} />)}
          </div>
          {!top.length && <p className="vl-empty-inline">후보가 없어요. ‘직접 지정’으로 영역을 표시하세요.</p>}
          {extraCount > 0 && <button type="button" className="vl-text-button" onClick={() => setVisibleExtra(value =>
            value >= extraCount ? 0 : Math.min(extraCount, value + 15))}>
            {visibleExtra >= extraCount ? '상위 3개만 보기' : visibleExtra === 0
              ? '다른 후보도 보기 (' + extraCount + '개)' : '후보 더 보기 · ' + visibleExtra + ' / ' + extraCount}
          </button>}
          {rest.length > 0 && <div className="vl-candidate-grid vl-candidate-grid-extra">
            {rest.map(groupIndex => {
              const rank = ranked.indexOf(groupIndex) + 1;
              return <CandidateCard key={groupIndex} video={video} refIndex={refIndex} groupIndex={groupIndex} rank={rank}
                selected={checked.has(groupIndex)} onToggle={() => {
                  setSec(video.groups[groupIndex].rep[refIndex][0]); onToggle(groupIndex);
                }} />;
            })}
          </div>}
        </section>
      </div>

      <aside className="vl-review-right">
        <section className="vl-panel vl-selection-panel">
          <div className="vl-section-heading"><h2>선택한 구간</h2>
            <span className="vl-count">{checked.size + manuals.length}</span></div>
          <SelectionSummary video={video} refIndex={refIndex} checked={checked} confirmed={confirmed} manuals={manuals}
            onRemoveGroup={onRemoveGroup} onRemoveManual={onRemoveManual} />
          {decision === 'none' && <div className="vl-none-state">이 원본 로고는 영상에 없는 것으로 표시했습니다.</div>}
          {checked.size > 0 && decision !== 'done' && <button type="button" className="vl-button primary vl-wide"
            onClick={onConfirm}>선택한 {checked.size}개 구간 확정</button>}
          {decision === 'done' && <button type="button" className="vl-button subtle vl-wide" onClick={onManual}>영역 직접 추가</button>}
          {decision === 'done' && <button type="button" className="vl-button secondary vl-wide" onClick={() => onNone()}>해당 없음으로 변경</button>}
        </section>
        {decision === 'done' && <GroupLinks video={video} refIndex={refIndex} confirmed={confirmed} checked={checked} onToggle={groupIndex => {
          setSec(video.groups[groupIndex].rep[refIndex][0]); onLinkToggle(groupIndex);
        }} />}
      </aside>
    </div>
  </div>;
}

function CompareResults({ base, video, refs, onConfirm, onStatus }) {
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  // 정밀 대조 기록이 없는 작업(자동 대조를 하지 않는 서버가 만든 작업)은 서버와 같은 규칙으로 고른 후보로 한 번 대조한다.
  const requested = useRef(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer;
    setResult(null); setError('');
    async function poll() {
      try {
        const value = await fetchJson(base + '/compare', { signal: controller.signal });
        setResult(value); setError(''); onStatus(value.compare_status);
        if (!value.compare_status && !requested.current) {
          requested.current = true;
          await startAutoCompare(base, { video, signal: controller.signal });
          if (!controller.signal.aborted) timer = setTimeout(poll, 500);
        } else if (RUNNING.has(value.compare_status)) timer = setTimeout(poll, 2000);
      } catch (failure) {
        if (failure.name !== 'AbortError') setError(failure.message);
      }
    }
    poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [base, video, onStatus, retry]);

  if (error) return <section className="vl-panel"><ErrorMessage message={error} />
    <button type="button" className="vl-button secondary" onClick={() => setRetry(value => value + 1)}>다시 불러오기</button></section>;
  if (!result) return <section className="vl-panel" role="status">정밀 대조 근거를 불러오고 있습니다.</section>;
  const bundles = result.bundles || [];
  const running = RUNNING.has(result.compare_status);
  const auto = result.origin !== 'manual';
  const tally = bundles.reduce((counts, bundle) => {
    const tone = OUTCOME[bundleOutcome(bundle.evidence?.outcomes)].tone;
    return { ...counts, [tone]: (counts[tone] || 0) + 1 };
  }, {});
  // 테마(Optimus) 정밀 대조 화면: 짧은 제목·결과 개수 → 로고마다 카드(원본 ↔ 영상, 초별 결과 띠, 접는 초별 표).
  return <div className="vl-main-stack vl-compare">
    <div className="vl-stage-heading vl-compare-heading">
      <div><h1>로고 정밀 대조</h1>
        <p>{auto ? '' : '직접 확인한 위치로 다시 대조했어요. '}차이 근거가 있으면 보류, 없으면 통과예요. 최종 판정은 담당자가 합니다.</p>
        {bundles.length > 0 && <div className="vl-compare-tally" aria-label="정밀 대조 결과 요약">
          <span className="vl-compare-total">결과 {bundles.length}건</span>
          {['match', 'diff', 'unknown'].filter(tone => tally[tone])
            .map(tone => <span key={tone} className={'vl-result-pill ' + tone}>{OUTCOME[tone].label} {tally[tone]}</span>)}</div>}</div>
      <button type="button" className="vl-button secondary" onClick={onConfirm}>위치 직접 확인</button>
    </div>
    {running && <section className="vl-panel vl-progress-card">
      <div className="vl-progress-title"><strong>정밀 대조 중</strong><span>{result.compare_progress || 0}%</span></div>
      <div className="vl-progress-track"><span style={{ width: Math.min(100, result.compare_progress || 0) + '%' }} /></div>
      <p>고른 구간의 초별 영역을 원본 로고와 비교하고 있습니다.</p>
    </section>}
    {result.compare_status === 'failed' && <section className="vl-panel"><ErrorMessage message={result.compare_error || '정밀 대조가 완료되지 않았습니다.'} /></section>}
    {!result.compare_status && <section className="vl-panel vl-progress-card" role="status">
      <div className="vl-progress-title"><strong>정밀 대조를 시작하고 있습니다</strong></div>
      <p>원본 로고마다 순위가 높은 후보를 자동으로 골라 정밀 대조합니다.</p>
    </section>}
    {(result.warnings || []).map(warning => <div className="vl-warning" key={warning}>⚠ {warning}</div>)}
    {result.compare_status === 'completed' && !bundles.length && <section className="vl-panel vl-empty">
      <strong>대조 결과가 없습니다.</strong><p>영상에서 원본 로고 후보를 찾지 못했거나 모든 원본 로고를 ‘영상에 없음’으로 정했습니다. 위치를 직접 지정해 다시 대조할 수 있어요.</p>
    </section>}
    {bundles.map(bundle => <EvidenceBundle key={bundle.logo + '-' + bundle.crop} base={base} bundle={bundle}
      refInfo={refs[bundle.logo]} several={bundles.filter(item => item.logo === bundle.logo).length > 1} />)}
  </div>;
}

function EvidenceBundle({ base, bundle, refInfo, several }) {
  const evidence = bundle.evidence || {};
  const representative = evidence.representative;
  const refName = refInfo?.name || bundle.reference || ('원본 로고 ' + (bundle.logo + 1));
  const rows = [...(bundle.per_sec || []).map(item => ({ ...item, skipped: false })),
    ...(bundle.skipped || []).map(item => ({ ...item, skipped: true }))].sort((a, b) => a.sec - b.sec);
  const [expanded, setExpanded] = useState(null);
  const detailsOpen = expanded ?? (rows.length > 0 && rows.length <= 4);
  // 묶음 결과는 보류(차이 있음)·통과(차이 없음)로 보이고, 보류면 차이만 종류별로 모아 보여 준다.
  const outcome = OUTCOME[bundleOutcome(evidence.outcomes)];
  const differences = groupDifferences(bundle.per_sec);
  return <article className={'vl-evidence-card vl-cmp-card is-' + outcome.tone}>
    <header className="vl-cmp-head">
      <div className="vl-cmp-title"><h2>{refName}{several ? ' · 구간 ' + (bundle.crop + 1) : ''}</h2>
        <span className={'vl-result-pill vl-bundle-outcome ' + outcome.tone}>{outcome.label}</span>
        {bundle.source !== 'candidate' && <span className="vl-cmp-tag">직접 지정</span>}</div>
    </header>
    <div className="vl-cmp-pair">
      <figure><div className="vl-cmp-frame"><img src={base + '/' + (refInfo?.img || ('refs/' + bundle.logo + '.png'))} alt={refName + ' 원본'} /></div>
        <figcaption>원본 로고</figcaption></figure>
      <span className="vl-cmp-arrow" aria-hidden="true">→</span>
      <figure>{representative ? <div className="vl-cmp-frame"><img src={base + '/compare/rep/' + bundle.logo + '/' + bundle.crop + '.jpg'}
        alt={timeText(representative.sec) + ' 영상 속 ' + refName} /></div>
        : <div className="vl-cmp-frame is-empty">비교할 프레임 없음</div>}
        <figcaption>{representative ? '영상 ' + timeText(representative.sec) : '영상'}</figcaption></figure>
    </div>
    {differences.length > 0 && <div className="vl-diff-groups vl-cmp-diffs" aria-label="종류별 차이">
      {differences.map(group => <section className="vl-diff-group" key={group.kind}>
        <h3>{group.kind} <span>{group.secs}초</span></h3>
        <ul className="vl-finding-list">{group.items.map(([label, count]) => <li key={label}><span>{label}</span><strong>{count}초</strong></li>)}</ul>
      </section>)}
    </div>}
    {outcome.tone === 'unknown' && !differences.length && <p className="vl-cmp-note">로고가 작거나 가려져 비교하지 못했어요. 원본 영상을 확인해 주세요.</p>}
    {rows.length > 0 && <div className="vl-cmp-strip">
      <span>초별 결과</span>
      <div className="vl-sec-timeline" aria-label="초별 대조 상태">
        {rows.map(row => <span key={row.sec} title={row.skipped ? timeText(row.sec) + ' · ' + (SKIP[row.reason] || '건너뜀') : timeText(row.sec) + ' · ' + (OUTCOME[row.outcome]?.label || row.outcome)}
          className={row.skipped ? 'skipped' : (OUTCOME[row.outcome]?.tone || 'unknown')} />)}
      </div>
      <div className="vl-cmp-strip-axis" aria-hidden="true"><span>{timeText(rows[0].sec)}</span>{rows.length > 1 && <span>{timeText(rows[rows.length - 1].sec)}</span>}</div>
    </div>}
    {rows.length > 0 && <details className="vl-details" open={detailsOpen} aria-label={refName + ' 초별 대조 근거'}>
      <summary aria-expanded={detailsOpen} onClick={event => { event.preventDefault(); setExpanded(!detailsOpen); }}>
        <span>초별 근거 <em>{rows.length}</em></span><i aria-hidden="true" />
      </summary>
      <div className="vl-table-wrap"><table className="vl-evidence-table">
        <thead><tr><th>시점</th><th>결과</th><th>차이 종류</th><th>차이 내용</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.sec}>
          <td>{timeText(row.sec)}</td>
          <td>{row.skipped ? <span className="vl-result-pill unknown">{SKIP[row.reason] || '건너뜀'}</span> :
            <span className={'vl-result-pill ' + (OUTCOME[row.outcome]?.tone || '')}>{OUTCOME[row.outcome]?.label || row.outcome}</span>}</td>
          <td>{row.skipped || row.outcome !== 'diff' ? '—' : (row.kinds || []).join(' · ') || '기타'}</td>
          <td>{row.skipped ? '비교하지 않음' : row.outcome === 'diff' ? (row.findings || []).join(' · ') || '차이 근거 있음'
            : row.outcome === 'match' ? '차이 없음' : '비교할 수 없음'}</td>
        </tr>)}</tbody>
      </table></div>
    </details>}
  </article>;
}

export default function LogoConfirmPage() {
  const go = useNavigate();
  const [params, setParams] = useSearchParams();
  const job = params.get('job');
  // 기본 화면은 정밀 대조 결과다. 위치 직접 확인(view=confirm)은 결과를 본 사람이 고를 때만 연다.
  const view = params.get('view') === 'confirm' ? 'confirm' : 'compare';
  const base = '/api/video-logos/' + encodeURIComponent(job || '');
  const storageKey = 'video-logo-review:v4:' + (job || 'new');
  const [meta, setMeta] = useState(null);
  const [index, setIndex] = useState([]);
  const [video, setVideo] = useState(null);
  const [error, setError] = useState('');
  const [refIndex, setRefIndex] = useState(0);
  const [sec, setSec] = useState(0);
  const [answers, setAnswers] = useState({});
  const [drafts, setDrafts] = useState({});
  const [decision, setDecision] = useState(null);
  const [confirmed, setConfirmed] = useState([]);
  const [checked, setChecked] = useState(new Set());
  const [manuals, setManuals] = useState([]);
  const [mode, setMode] = useState('candidates');
  const [busy, setBusy] = useState(false);
  const active = useRef(null);
  useWorkspaceReset(() => active.current?.abort());
  useEffect(() => () => active.current?.abort(), []);

  useEffect(() => {
    if (!job) {
      setMeta(null); setIndex([]); setVideo(null); setAnswers({}); setDrafts({});
      return undefined;
    }
    const controller = new AbortController();
    let timer;
    setMeta(null); setIndex([]); setVideo(null); setError(''); setRefIndex(0);
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
      if (saved && typeof saved === 'object' && saved.answers) {
        setAnswers(saved.answers || {});
        setDrafts(saved.drafts || {});
      } else {
        const legacy = saved || JSON.parse(localStorage.getItem('video-logo-review:v3:' + job) || '{}');
        const nextAnswers = {}, nextDrafts = {};
        for (const [key, value] of Object.entries(legacy || {})) {
          if (value && value.decision && Array.isArray(value.crops)) nextAnswers[key] = value;
          if (value?.selection) nextDrafts[key] = value.selection;
        }
        setAnswers(nextAnswers); setDrafts(nextDrafts);
      }
    } catch { setAnswers({}); setDrafts({}); }
    async function poll() {
      try {
        const value = await fetchJson(base, { signal: controller.signal });
        setMeta(value); setError('');
        if (RUNNING.has(value.status)) timer = setTimeout(poll, 2000);
      } catch (failure) {
        if (failure.name !== 'AbortError') setError(failure.message);
      }
    }
    poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [job, base, storageKey]);

  useEffect(() => {
    if (!job || meta?.id !== job || meta.status !== 'candidates_ready') return undefined;
    const controller = new AbortController();
    setVideo(null); setIndex([]);
    Promise.all([
      fetchJson(base + '/index.json', { signal: controller.signal }),
      fetchJson(base + '/video/data.json', { signal: controller.signal }),
    ]).then(([list, data]) => {
      setIndex(list);
      setVideo({ ...data, base });
    }).catch(failure => {
      if (failure.name !== 'AbortError') setError(failure.message);
    });
    return () => controller.abort();
  }, [job, base, meta?.id, meta?.status]);

  // 이 브라우저에 저장한 선택이 없으면 서버가 자동 정밀 대조에 쓴 묶음(auto_selection)을 확정 화면의 선택으로 채운다.
  // 자동 대조를 하지 않는 서버가 만든 작업은 auto_selection이 없으므로, 화면이 같은 규칙으로 고른 묶음을 쓴다.
  useEffect(() => {
    if (!video || meta?.id !== job || Object.keys(answers).length || Object.keys(drafts).length) return;
    const seeded = autoDrafts(meta.auto_selection || autoSelection(video.groups, video.refs.length), video.groups);
    const nextAnswers = {}, nextDrafts = {};
    for (const [key, draft] of Object.entries(seeded)) {
      const indexNo = Number(key);
      if (!video.refs[indexNo]) continue;
      const selection = { ...draft, auto: true };
      nextDrafts[indexNo] = selection;
      nextAnswers[indexNo] = { ...handoff({ video: video.key, ref: indexNo, refName: video.refs[indexNo].name, decision: selection.decision,
        groups: video.groups, checked: new Set(selection.checked), manuals: [] }), frame_size: video.size, selection };
    }
    setAnswers(nextAnswers); setDrafts(nextDrafts);
  }, [video, meta, job, answers, drafts]);

  const refNames = meta?.id === job ? (index[0]?.refs || []) : [];
  const refInfo = video?.refs?.[refIndex];
  const ready = meta?.id === job && meta.status === 'candidates_ready' && !!video;
  function answerMatchesSelection(indexNo) {
    const answer = answers[indexNo];
    if (!answer || !video?.refs?.[indexNo]) return false;
    const selection = indexNo === refIndex
      ? { decision, checked: [...checked], manuals }
      : (drafts[indexNo] || answer.selection || {});
    if (!selection.decision) return false;
    const expected = handoff({ video: video.key, ref: indexNo, refName: video.refs[indexNo].name,
      decision: selection.decision, groups: video.groups, checked: new Set(selection.checked || selection.confirmed || []),
      manuals: selection.manuals || [] });
    return answer.decision === expected.decision && JSON.stringify(answer.crops) === JSON.stringify(expected.crops);
  }
  const savedCount = refNames.filter((_, i) => answerMatchesSelection(i)).length;
  const compareCount = refNames.filter((_, i) => answerMatchesSelection(i) && answers[i]?.decision === 'done').length;
  const comparing = RUNNING.has(meta?.compare_status);
  const onCompareStatus = useCallback(value => {
    setMeta(previous => previous && previous.id === job ? { ...previous, compare_status: value } : previous);
  }, [job]);

  useEffect(() => {
    if (!ready || !video) return;
    const saved = drafts[refIndex] || answers[refIndex]?.selection;
    if (saved) {
      setDecision(saved.decision || null);
      setConfirmed(saved.confirmed || []);
      setChecked(new Set(saved.checked || saved.confirmed || []));
      setManuals(saved.manuals || []);
    } else {
      setDecision(null); setConfirmed([]); setChecked(new Set()); setManuals([]);
    }
    const preferred = topGroups(video.groups, refIndex, 1)[0];
    setSec(saved?.sec ?? (preferred == null ? 0 : video.groups[preferred].rep[refIndex][0]));
    setMode('candidates');
  }, [job, refIndex, video, ready, answers, drafts]);

  const payload = useMemo(() => {
    if (!video || !refInfo || !decision) return null;
    return handoff({ video: video.key, ref: refIndex, refName: refInfo.name, decision,
      groups: video.groups, checked, manuals });
  }, [video, refInfo, refIndex, decision, checked, manuals]);
  const savedAnswer = answers[refIndex];
  const isSaved = !!(payload && answerMatchesSelection(refIndex) && savedAnswer && savedAnswer.decision === payload.decision &&
    JSON.stringify(savedAnswer.crops) === JSON.stringify(payload.crops));

  function keepLocalState(nextAnswers, nextDrafts) {
    setAnswers(nextAnswers); setDrafts(nextDrafts);
    try { localStorage.setItem(storageKey, JSON.stringify({ answers: nextAnswers, drafts: nextDrafts })); }
    catch { /* 브라우저 저장소를 사용할 수 없는 경우 화면 안에서 유지 */ }
  }
  function saveAnswer() {
    if (!payload) return;
    const selection = { decision, confirmed, checked: [...checked], manuals, sec };
    keepLocalState({ ...answers, [refIndex]: { ...payload, frame_size: video.size, selection } },
      { ...drafts, [refIndex]: selection });
  }
  function changeReference(nextIndex) {
    if (nextIndex === refIndex) return;
    const selection = { decision, confirmed, checked: [...checked], manuals, sec };
    keepLocalState(answers, { ...drafts, [refIndex]: selection });
    setRefIndex(nextIndex);
  }
  function toggleGroup(groupIndex) {
    setChecked(previous => {
      const next = new Set(previous);
      if (next.has(groupIndex)) next.delete(groupIndex); else next.add(groupIndex);
      return next;
    });
  }
  function confirmGroups() {
    const selected = [...checked];
    if (!selected.length) return;
    setConfirmed(selected); setDecision('done'); setMode('candidates');
  }
  function markManual(value) {
    setManuals(previous => [...previous, value]);
    setDecision('done'); setMode('candidates');
  }
  function markNone() {
    setDecision('none'); setConfirmed([]); setChecked(new Set()); setManuals([]); setMode('candidates');
  }
  function resetSelection() {
    setDecision(null); setConfirmed([]); setChecked(new Set()); setManuals([]); setMode('candidates');
    const emptyDraft = { decision: null, confirmed: [], checked: [], manuals: [], sec };
    keepLocalState(answers, { ...drafts, [refIndex]: emptyDraft });
  }
  function removeGroup(groupIndex) {
    setChecked(previous => { const next = new Set(previous); next.delete(groupIndex); return next; });
    setConfirmed(previous => previous.filter(value => value !== groupIndex));
  }
  function removeManual(indexToRemove) {
    setManuals(previous => previous.filter((_, index) => index !== indexToRemove));
  }
  // 기본 화면(view 없음)은 정밀 대조 결과다. 위치 직접 확인만 view=confirm으로 연다.
  function changeView(nextView) {
    const query = new URLSearchParams(params);
    if (nextView === 'confirm') query.set('view', 'confirm');
    else query.delete('view');
    setParams(query);
  }
  async function runCompare() {
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError('');
    try {
      const body = refNames.map((_, i) => answerMatchesSelection(i) ? answers[i] : null).filter(Boolean)
        .map(({ frame_size, selection, ...answer }) => answer);
      await fetchJson(base + '/compare', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: controller.signal });
      if (controller.signal.aborted) return;
      setMeta(previous => previous && previous.id === job ? { ...previous, compare_status: 'queued' } : previous);
      // 동영상 검수에 연결된 작업은 검수 결과 화면으로 돌아가 다시 대조한 결과를 거기서 바로 본다.
      if (meta?.review_id) go('/video?id=' + encodeURIComponent(meta.review_id));
      else changeView('compare');
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message);
    } finally {
      if (!controller.signal.aborted) { active.current = null; setBusy(false); }
    }
  }

  // 포스터 로고 화면과 같은 로고 단계 표시. 종합 검수(동영상 검수에 연결된 작업)는 첫 단계가 그 검수로,
  // 로고만 검수는 자료 입력으로 돌아간다. 일반 자료(detector=openai)는 위치 후보를 OpenAI로 찾았다고 알린다.
  const linkedReview = meta?.review_id;
  const back = linkedReview ? '/video?id=' + linkedReview : '/video';
  const missing = video?.missing || [];
  const openaiDetector = meta?.id === job && meta?.detector === 'openai';
  const baseSteps = linkedReview ? { review: '동영상 검수', candidates: videoLogoSteps.candidates, compare: videoLogoSteps.compare } : videoLogoSteps;
  const steps = ready && view === 'confirm' ? { ...baseSteps, confirm: '위치 직접 확인' } : baseSteps;
  const step = !job ? 'input' : ready ? view : 'candidates';
  const stepVersion = !ready ? '로고 검수' : view === 'compare'
    ? (meta.compare_status === 'completed' ? '대조 완료' : comparing ? '대조 중' : '저장 후 실행')
    : savedCount + ' / ' + refNames.length + '개 저장';

  return <div className="rv-workspace rv-workspace--tool">
    <a href="#review-content" className="rv-skip">작업 영역으로 이동</a>
    <SiteHeader current="/video" />
    <WorkspaceProjectBar name={meta?.id === job ? meta.title : '동영상 로고 검수'} version={stepVersion} step={step} steps={steps} locked={busy}
      canNavigate={key => key === 'input' || key === 'review' || (ready && (key === 'confirm' || key === 'compare'))}
      navigate={key => { if (key === 'input' || key === 'review') go(back); else if (key === 'confirm' || key === 'compare') changeView(key); }}>
      {ready && view === 'confirm' && <button type="button" className="rv-button primary" onClick={runCompare}
        disabled={!compareCount || busy || comparing}>
        {comparing ? '정밀 대조 중' : busy ? '요청 중…' : compareCount
          ? '확인한 ' + compareCount + '개 로고로 다시 정밀 대조' : '정밀 대조할 위치 없음'}
      </button>}
    </WorkspaceProjectBar>
    <main id="review-content" className="vl-page" tabIndex={-1}>
    {!job ? <section className="vl-empty-state">
      <div className="vl-empty-mark">01</div>
      <span className="vl-kicker">자료 입력에서 시작</span>
      <h1>자료 입력에서 로고 검수를 시작하세요</h1>
      <p>검수 유형에서 ‘로고 검수’ 또는 ‘종합 검수’를 고르고 영상과 원본 로고를 첨부하면 후보를 찾고 정밀 대조까지 자동으로 진행합니다. 결과를 보고 검수하려던 로고가 아니면 위치를 직접 확인해 다시 대조할 수 있습니다.</p>
      <Link className="vl-button primary" to="/video">자료 입력 열기</Link>
    </section> : <div className="vl-content">
      {/* 작업 이름은 위쪽 프로젝트 줄에 이미 있으므로, 후보 찾기가 끝나기 전(진행·실패)에만 상태 줄을 보인다. */}
      {meta?.id === job && meta.status !== 'candidates_ready' && <div className="vl-jobbar">
        <div><span className="vl-kicker">작업</span><strong>{meta.title}</strong></div>
        <span className={'vl-status ' + (meta.status === 'failed' ? 'failed' : 'working')}>
          {meta.status === 'failed' ? '실패' : (meta.stage_detail || '후보 찾는 중')}</span>
      </div>}

      {meta?.id === job && RUNNING.has(meta.status) && <section className="vl-panel vl-progress-card">
        <div className="vl-progress-title"><strong>영상 후보를 찾고 있습니다</strong><span>{meta.progress || 0}%</span></div>
        <div className="vl-progress-track"><span style={{ width: Math.min(100, meta.progress || 0) + '%' }} /></div>
        <p>{meta.stage_detail || (openaiDetector ? 'OpenAI로 프레임별 위치를 찾고 있습니다.' : 'RunPod에서 프레임별 후보를 찾고 있습니다.')}</p>
      </section>}
      {meta?.id === job && meta.status === 'failed' && <section className="vl-panel">
        <ErrorMessage message={meta.error || '후보 찾기에 실패했습니다.'} />
        <p className="vl-help">작업이 실패했습니다. {linkedReview ? '영상 검수 화면에서' : '자료 입력에서'} 다시 접수할 수 있습니다.</p>
      </section>}
      {error && <section className="vl-panel"><ErrorMessage message={error} /></section>}

      {ready && <>
        {missing.length > 0 && <div className="vl-warning">
          <strong>프레임 누락 {missing.length}초</strong>
          <span>누락된 시점은 후보 찾기와 정밀 대조에서 제외됩니다: {spanText(missing)}</span>
        </div>}

        {view === 'confirm' ? <div className={'vl-layout' + (refNames.length > 1 ? '' : ' is-single')}>
          {/* 원본 로고가 하나면 고를 것이 없으므로 왼쪽 목록을 그리지 않는다. */}
          {refNames.length > 1 && <aside className="vl-logo-sidebar">
            <div className="vl-sidebar-title"><span className="vl-kicker">기준 로고</span><strong>{refNames.length}개</strong></div>
            <div className="vl-logo-list">
              {refNames.map((name, i) => <button type="button" key={i} className={'vl-logo-row' + (i === refIndex ? ' active' : '')}
                onClick={() => changeReference(i)} aria-current={i === refIndex ? 'step' : undefined}>
                <img src={base + '/refs/' + i + '.png'} alt="" />
                <span><strong>{name}</strong><small>{answerMatchesSelection(i)
                  ? (answers[i].decision === 'none' ? '영상에 없음' : '위치 확정') + (answers[i].selection?.auto ? ' · 자동 선택' : ' · 저장됨')
                  : drafts[i]?.decision ? '저장 필요' : '확인 필요'}</small></span>
                <i>{answerMatchesSelection(i) ? '✓' : (i + 1)}</i>
              </button>)}
            </div>
          </aside>}

          <section className="vl-workspace">
            {refInfo && <div className="vl-current-ref">
              <img src={base + '/' + refInfo.img} alt={refInfo.name + ' 원본'} />
              <div>{refNames.length > 1 && <span className="vl-kicker">{refIndex + 1} / {refNames.length}</span>}
                <span className="vl-current-label">원본 로고</span><h1>{refInfo.name}</h1></div>
            </div>}

            {mode === 'manual' ? <ManualEditor video={video} initialSec={sec} onCancel={() => setMode('candidates')} onDone={markManual} /> :
              <CandidateWorkspace video={video} refIndex={refIndex} checked={checked} onToggle={toggleGroup} sec={sec} setSec={setSec}
                onConfirm={confirmGroups} onNone={markNone} onManual={() => setMode('manual')}
                onLinkToggle={toggleGroup} confirmed={confirmed} manuals={manuals}
                onRemoveGroup={removeGroup} onRemoveManual={removeManual} decision={decision} />}

            {payload && <ExposureSummary video={video} payload={payload} />}
            {decision && <section className="vl-savebar">
              <div><strong>{decision === 'none' ? '영상에 없는 로고로 표시했어요.' : isSaved ? '저장했어요. 위쪽 단추로 다시 정밀 대조하세요.' : '선택한 구간을 저장하세요.'}</strong></div>
              <div className="vl-save-actions">
                <button type="button" className="vl-button subtle" onClick={resetSelection}>다시 선택</button>
                <button type="button" className="vl-button primary" onClick={saveAnswer} disabled={isSaved}>
                  {isSaved ? '저장됨' : '이 원본 로고 확정 저장'}
                </button>
                {refIndex + 1 < refNames.length && <button type="button" className="vl-button secondary" onClick={() => { saveAnswer(); setRefIndex(value => value + 1); }}>
                  저장하고 다음 로고
                </button>}
              </div>
            </section>}
          </section>
        </div> : <CompareResults base={base} video={video} refs={video.refs} onConfirm={() => changeView('confirm')} onStatus={onCompareStatus} />}
      </>}
    </div>}
  </main>
  </div>;
}
