import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { fetchJson } from '../../api/client.js';
import { WorkspaceFooter, WorkspaceTopbar } from '../../layouts/ReviewLayout.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { handoff, intervalText, LINK_RATIO, linkCandidates, toIntervals, topGroups } from './logoFlow.js';
import './logoConfirm.css';

const RUNNING = new Set(['queued', 'running']);
const OUTCOME = {
  match: { label: '차이 근거 없음', tone: 'match' },
  diff: { label: '차이 근거 있음', tone: 'diff' },
  unknown: { label: '비교 정보 부족', tone: 'unknown' },
};
const ELEMENT_STATUS = {
  present: '확인됨',
  missing: '빠짐',
  moved: '위치 다름',
  changed: '형태 다름',
  not_comparable_small: '크기 부족',
  not_comparable_unread: '읽기 불가',
};
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

function CropPreview({ video, sec, box, compact = false }) {
  if (!video || !box) return <div className="vl-crop vl-crop-empty">미리보기 없음</div>;
  const W = video.size[0], H = video.size[1];
  const x1 = Math.max(0, Math.min(W - 1, box[0]));
  const y1 = Math.max(0, Math.min(H - 1, box[1]));
  const x2 = Math.max(x1 + 1, Math.min(W, box[2]));
  const y2 = Math.max(y1 + 1, Math.min(H, box[3]));
  const width = x2 - x1, height = y2 - y1;
  const scale = Math.min((compact ? 248 : 350) / width, (compact ? 112 : 166) / height);
  return <div className={'vl-crop' + (compact ? ' compact' : '')} aria-label={sec + '초 로고 영역'}>
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
    {overlays.map(item => <button
      type="button"
      key={item.id}
      className={'vl-box' + (item.selected ? ' selected' : '')}
      style={boxStyle(item.box, video.size)}
      aria-label={item.label}
      title={item.label}
      onClick={() => onSelect && onSelect(item.id)}
    ><span>{item.label}</span></button>)}
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
    <button type="button" className="vl-candidate-image" onClick={onToggle} aria-pressed={selected}>
      <CropPreview video={video} sec={sec} box={box} compact />
      <span className="vl-rank">#{rank}</span>
      <span className="vl-candidate-check">{selected ? '선택됨' : '선택'}</span>
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
      label: '#' + (order + 1) + ' 후보' + (checked.has(groupIndex) ? ' · 선택됨' : ''),
    }];
  });
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

  return <section className="vl-viewer-card">
    <div className="vl-section-heading">
      <div><span className="vl-kicker">후보 위치</span><h2>영상에서 후보를 확인하세요</h2></div>
      <span className="vl-soft-tag">1초 간격 프레임</span>
    </div>
    <FrameViewer video={video} sec={sec} overlays={overlays} onSelect={onToggle} />
    <div className="vl-player-controls">
      <button type="button" className="vl-play" onClick={() => setPlaying(value => !value)}>{playing ? '일시정지' : '재생'}</button>
      <input aria-label="영상 시점" type="range" min="0" max={last} value={Math.min(sec, last)}
        onChange={event => setSec(Number(event.target.value))} />
      <strong>{timeText(sec)} <span>/ {timeText(last)}</span></strong>
    </div>
    <div className="vl-player-options">
      <label>오버레이 후보
        <select value={limit} onChange={event => setLimit(Number(event.target.value))}>
          {[3, 10, 20, 50].map(value => <option key={value} value={value}>상위 {value}개</option>)}
        </select>
      </label>
      <label>재생 속도
        <select value={speed} onChange={event => setSpeed(Number(event.target.value))}>
          {[1, 2, 4, 8].map(value => <option key={value} value={value}>{value}배</option>)}
        </select>
      </label>
      <span>{overlays.length ? '박스를 눌러 후보를 선택하거나 해제하세요.' : '이 시점에는 표시할 후보가 없습니다.'}</span>
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
      <div><span className="vl-kicker">추가 구간</span><h2>같은 로고의 다른 묶음</h2></div>
      <span className="vl-soft-tag">확정 점수 {Math.round(LINK_RATIO * 100)}% 이상</span>
    </div>
    {!candidates.length ? <p className="vl-empty-inline">연결해 볼 다른 후보가 없습니다.</p> :
      <div className="vl-linked-list">{shown.map(groupIndex => {
        const group = video.groups[groupIndex];
        const [sec, box] = group.rep[refIndex];
        return <label key={groupIndex} className={'vl-linked-item' + (checked.has(groupIndex) ? ' selected' : '')}>
          <input type="checkbox" checked={checked.has(groupIndex)} onChange={() => onToggle(groupIndex)} />
          <CropPreview video={video} sec={sec} box={box} compact />
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
      <div><span className="vl-kicker">직접 지정</span><h2>영상에서 로고 영역을 표시하세요</h2></div>
      <button type="button" className="vl-text-button" onClick={onCancel}>후보 화면으로</button>
    </div>
    <p className="vl-help">프레임 위에서 드래그해 로고를 감싸고, 같은 영역에 로고가 보이는 시간 구간을 지정하세요. 고정된 박스가 구간 전체에 적용됩니다.</p>
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
  if (!groups.length && !manuals.length) return <div className="vl-selection-empty">선택한 후보가 없습니다. 후보 카드를 선택하거나 영상에서 직접 지정하세요.</div>;
  return <div className="vl-selection-list">
    {groups.map(groupIndex => {
      const group = video.groups[groupIndex];
      const [sec, box] = group.rep[refIndex];
      const kind = confirmed.includes(groupIndex) ? '선택 후보' : '연결 후보';
      return <article className="vl-selected-item" key={'group-' + groupIndex}>
        <CropPreview video={video} sec={sec} box={box} compact />
        <div><span className="vl-mini-label">{kind}</span><strong>{spanText(group.secs)}</strong><small>{group.secs.length}개 프레임 · 점수 {Number(group.rank[refIndex]).toFixed(3)}</small></div>
        <button type="button" aria-label="후보에서 제거" onClick={() => onRemoveGroup(groupIndex)}>×</button>
      </article>;
    })}
    {manuals.map((manual, index) => <article className="vl-selected-item" key={'manual-' + index}>
      <CropPreview video={video} sec={manual.sec} box={manual.box} compact />
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
    <div className="vl-stage-heading">
      <div><span className="vl-kicker">01 / 위치 확인</span><h1>이 원본 로고가 나오는 구간을 고르세요</h1>
        <p>순위는 후보를 찾는 데 쓰고, 같은 로고인지 최종 확인은 상현님이 진행합니다.</p></div>
      <div className="vl-stage-actions">
        <button type="button" className="vl-button subtle" onClick={onManual}>후보에 없음 · 직접 지정</button>
        <button type="button" className="vl-button secondary" onClick={onNone}>영상에 없음</button>
      </div>
    </div>

    <div className="vl-review-grid">
      <div className="vl-review-left">
        <CandidatePlayer video={video} refIndex={refIndex} sec={sec} setSec={setSec} checked={checked} onToggle={onToggle} />
        <section className="vl-panel">
          <div className="vl-section-heading"><div><span className="vl-kicker">상위 후보</span><h2>비교할 묶음을 선택하세요</h2></div>
            <span className="vl-soft-tag">여러 묶음 선택 가능</span></div>
          <div className="vl-candidate-grid">
            {top.map((groupIndex, index) => <CandidateCard key={groupIndex} video={video} refIndex={refIndex} groupIndex={groupIndex}
              rank={index + 1} selected={checked.has(groupIndex)} onToggle={() => {
                setSec(video.groups[groupIndex].rep[refIndex][0]); onToggle(groupIndex);
              }} />)}
          </div>
          {!top.length && <p className="vl-empty-inline">순위에 표시할 후보가 없습니다. 영상에서 직접 지정할 수 있습니다.</p>}
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
          <div className="vl-section-heading"><div><span className="vl-kicker">확정할 후보</span><h2>선택 내용</h2></div>
            <span className="vl-count">{checked.size + manuals.length}</span></div>
          <SelectionSummary video={video} refIndex={refIndex} checked={checked} confirmed={confirmed} manuals={manuals}
            onRemoveGroup={onRemoveGroup} onRemoveManual={onRemoveManual} />
          {decision === 'none' && <div className="vl-none-state">이 원본 로고는 영상에 없는 것으로 표시했습니다.</div>}
          {checked.size > 0 && decision !== 'done' && <button type="button" className="vl-button primary vl-wide"
            onClick={onConfirm}>선택한 {checked.size}개 묶음 확정</button>}
          {decision === 'done' && <p className="vl-save-hint">선택 내용을 저장한 뒤 정밀 대조를 실행할 수 있습니다.</p>}
          {decision === 'done' && <button type="button" className="vl-button subtle vl-wide" onClick={onManual}>수동 영역 추가</button>}
          {decision === 'done' && <button type="button" className="vl-button secondary vl-wide" onClick={() => onNone()}>해당 없음으로 변경</button>}
        </section>
        {decision === 'done' && <GroupLinks video={video} refIndex={refIndex} confirmed={confirmed} checked={checked} onToggle={groupIndex => {
          setSec(video.groups[groupIndex].rep[refIndex][0]); onLinkToggle(groupIndex);
        }} />}
      </aside>
    </div>
  </div>;
}

function CompareResults({ base, refs, onBack, onStatus }) {
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer;
    setResult(null); setError('');
    async function poll() {
      try {
        const value = await fetchJson(base + '/compare', { signal: controller.signal });
        setResult(value); setError(''); onStatus(value.compare_status);
        if (RUNNING.has(value.compare_status)) timer = setTimeout(poll, 2000);
      } catch (failure) {
        if (failure.name !== 'AbortError') setError(failure.message);
      }
    }
    poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [base, onStatus, retry]);

  if (error) return <section className="vl-panel"><ErrorMessage message={error} />
    <button type="button" className="vl-button secondary" onClick={() => setRetry(value => value + 1)}>다시 불러오기</button></section>;
  if (!result) return <section className="vl-panel" role="status">정밀 대조 근거를 불러오고 있습니다.</section>;
  const bundles = result.bundles || [];
  const running = RUNNING.has(result.compare_status);
  return <div className="vl-main-stack">
    <div className="vl-stage-heading">
      <div><span className="vl-kicker">02 / 근거 확인</span><h1>초별 비교 근거를 확인하세요</h1>
        <p>이 화면은 자동 최종 판정을 내리지 않습니다. 요소·OCR·색상 근거를 보고 검토하세요.</p></div>
      <button type="button" className="vl-button secondary" onClick={onBack}>위치 확정으로 돌아가기</button>
    </div>
    {running && <section className="vl-panel vl-progress-card">
      <div className="vl-progress-title"><strong>정밀 대조 중</strong><span>{result.compare_progress || 0}%</span></div>
      <div className="vl-progress-track"><span style={{ width: Math.min(100, result.compare_progress || 0) + '%' }} /></div>
      <p>확정한 묶음의 초별 crop을 원본 로고와 비교하고 있습니다.</p>
    </section>}
    {result.compare_status === 'failed' && <section className="vl-panel"><ErrorMessage message={result.compare_error || '정밀 대조가 완료되지 않았습니다.'} /></section>}
    {!result.compare_status && <section className="vl-panel vl-empty">
      <strong>아직 정밀 대조를 실행하지 않았습니다.</strong>
      <p>후보 위치 확인 단계에서 원본 로고별 구간을 저장한 뒤 정밀 대조를 실행하세요.</p>
      <button type="button" className="vl-button primary" onClick={onBack}>후보 위치 확인으로 이동</button>
    </section>}
    {(result.warnings || []).map(warning => <div className="vl-warning" key={warning}>⚠ {warning}</div>)}
    {result.compare_status === 'completed' && !bundles.length && <section className="vl-panel vl-empty">
      <strong>대조 결과가 없습니다.</strong><p>모든 원본 로고를 ‘영상에 없음’으로 저장했거나 대조 대상 묶음이 없습니다.</p>
    </section>}
    {bundles.map(bundle => <EvidenceBundle key={bundle.logo + '-' + bundle.crop} base={base} bundle={bundle}
      refInfo={refs[bundle.logo]} />)}
  </div>;
}

function colorLabel(color) {
  if (!color) return '색상 정보 없음';
  if (color.mono) return '단색 후보 · 사람 확인';
  const items = [];
  if (color.hue != null) items.push('색상 각도 차이 ' + color.hue + '°');
  if (color.newc != null) items.push('새 유채색 ' + Math.round(color.newc * 100) + '%');
  if (color.lostc != null) items.push('빠진 유채색 ' + Math.round(color.lostc * 100) + '%');
  return items.join(' · ') || '색상 비교 근거 부족';
}

function EvidenceBundle({ base, bundle, refInfo }) {
  const evidence = bundle.evidence || {};
  const representative = evidence.representative;
  const refName = refInfo?.name || bundle.reference || ('원본 로고 ' + (bundle.logo + 1));
  const [open, setOpen] = useState(false);
  const rows = [...(bundle.per_sec || []).map(item => ({ ...item, skipped: false })),
    ...(bundle.skipped || []).map(item => ({ ...item, skipped: true }))].sort((a, b) => a.sec - b.sec);
  return <article className="vl-evidence-card">
    <header className="vl-evidence-head">
      <div><span className="vl-kicker">{bundle.source === 'candidate' ? '후보 묶음' : '직접 지정'}</span>
        <h2>{refName} · 대조 묶음 {bundle.crop + 1}</h2>
        <p>{evidence.n_secs || 0}초 비교 · {bundle.skipped?.length || 0}초 건너뜀</p></div>
      <button type="button" className="vl-text-button" onClick={() => setOpen(value => !value)}>{open ? '상세 접기' : '초별 근거 펼치기'}</button>
    </header>
    <div className="vl-evidence-images">
      <figure><img src={base + '/' + (refInfo?.img || ('refs/' + bundle.logo + '.png'))} alt={refName + ' 원본'} /><figcaption>원본 로고</figcaption></figure>
      {representative ? <figure><img src={base + '/compare/rep/' + bundle.logo + '/' + bundle.crop + '.jpg'} alt={representative.sec + '초 비교 crop'} />
        <figcaption>{timeText(representative.sec)} 대표 crop</figcaption></figure> :
        <div className="vl-evidence-no-image">비교 가능한 프레임이 없습니다.</div>}
      <div className="vl-evidence-summary">
        <div className="vl-result-counts">
          {Object.entries(evidence.outcomes || {}).map(([key, count]) => <span className={'vl-result-pill ' + (OUTCOME[key]?.tone || '')} key={key}>
            {OUTCOME[key]?.label || key} {count}초</span>)}
        </div>
        <p className="vl-color-line">{colorLabel(representative?.color)}</p>
        {evidence.findings?.length > 0 ? <ul className="vl-finding-list">
          {evidence.findings.map(([label, count]) => <li key={label}><span>{label}</span><strong>{count}초</strong></li>)}
        </ul> : <p className="vl-muted-copy">계산된 차이 근거가 없습니다. ‘정답’ 확정이 아니라 비교한 항목 기준의 결과입니다.</p>}
        {evidence.changes?.length > 0 && <p className="vl-muted-copy">결과 변화 시점: {evidence.changes.map(timeText).join(', ')}</p>}
      </div>
    </div>
    <div className="vl-sec-timeline" aria-label="초별 대조 상태">
      {rows.map(row => <span key={row.sec} title={row.skipped ? timeText(row.sec) + ' · ' + (SKIP[row.reason] || '건너뜀') : timeText(row.sec) + ' · ' + (OUTCOME[row.outcome]?.label || row.outcome)}
        className={row.skipped ? 'skipped' : (OUTCOME[row.outcome]?.tone || 'unknown')} />)}
    </div>
    <details className="vl-details" open={open}>
      <summary onClick={event => { event.preventDefault(); setOpen(value => !value); }}>
        초별 검사 자료 <span>{rows.length}개 항목</span>
      </summary>
      <div className="vl-table-wrap"><table className="vl-evidence-table">
        <thead><tr><th>시점</th><th>계산 결과</th><th>요소 상태</th><th>OCR 원문</th><th>색상 근거</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.sec}>
          <td>{timeText(row.sec)}</td>
          <td>{row.skipped ? <span className="vl-result-pill unknown">{SKIP[row.reason] || '건너뜀'}</span> :
            <span className={'vl-result-pill ' + (OUTCOME[row.outcome]?.tone || '')}>{OUTCOME[row.outcome]?.label || row.outcome}</span>}</td>
          <td>{row.skipped ? '비교하지 않음' : (row.elements || []).map(element => element.name + ': ' + (ELEMENT_STATUS[element.status] || element.status)).join(' · ') || '항목 없음'}</td>
          <td>{row.skipped ? '—' : (row.ocr || []).join(' / ') || '읽힌 글자 없음'}</td>
          <td>{row.skipped ? '—' : colorLabel(row.color)}</td>
        </tr>)}</tbody>
      </table></div>
    </details>
  </article>;
}

export default function LogoConfirmPage() {
  const [params, setParams] = useSearchParams();
  const job = params.get('job');
  const view = params.get('view') === 'compare' ? 'compare' : 'confirm';
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
  function changeView(nextView) {
    const query = new URLSearchParams(params);
    if (nextView === 'compare') query.set('view', 'compare');
    else query.delete('view');
    setParams(query);
  }
  async function runCompare() {
    setBusy(true); setError('');
    try {
      const body = refNames.map((_, i) => answerMatchesSelection(i) ? answers[i] : null).filter(Boolean)
        .map(({ frame_size, selection, ...answer }) => answer);
      await fetchJson(base + '/compare', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      setMeta(previous => previous && previous.id === job ? { ...previous, compare_status: 'queued' } : previous);
      changeView('compare');
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  const back = meta?.review_id ? '/video?id=' + meta.review_id : '/video';
  const missing = video?.missing || [];

  return <div className="rv-workspace rv-workspace--tool">
    <a href="#review-content" className="rv-skip">작업 영역으로 이동</a>
    <WorkspaceTopbar current="/video" />
    <main id="review-content" className="vl-page" tabIndex={-1}>
    <div className="vl-topbar">
      <div className="vl-topbar-title"><span>VIDEO LOGO REVIEW</span><strong>동영상 로고 검수</strong></div>
      <div className="vl-topbar-actions">
        <Link className="vl-button secondary" to={back}>동영상 검수로</Link>
        {ready && <button type="button" className="vl-button primary" onClick={runCompare}
          disabled={!compareCount || busy || comparing}>
          {comparing ? '정밀 대조 중' : busy ? '요청 중…' : compareCount
            ? '저장한 ' + compareCount + '개 로고 정밀 대조' : '정밀 대조할 위치 없음'}
        </button>}
      </div>
    </div>

    {!job ? <section className="vl-empty-state">
      <div className="vl-empty-mark">01</div>
      <span className="vl-kicker">백엔드 작업 연결</span>
      <h1>동영상 검수 화면에서 작업을 시작하세요</h1>
      <p>영상과 원본 로고를 함께 올리면 후보 묶음이 만들어집니다. 여기서 위치를 확인한 뒤 초별 비교 근거를 살펴볼 수 있습니다.</p>
      <Link className="vl-button primary" to="/video">동영상 검수 화면 열기</Link>
    </section> : <div className="vl-content">
      <div className="vl-jobbar">
        <div><span className="vl-kicker">작업</span><strong>{meta?.id === job ? meta.title : '작업 정보를 불러오는 중'}</strong></div>
        {meta?.id === job && <span className={'vl-status ' + (meta.status === 'failed' ? 'failed' : meta.status === 'candidates_ready' ? 'ready' : 'working')}>
          {meta.status === 'failed' ? '실패' : meta.status === 'candidates_ready' ? '후보 준비 완료' : (meta.stage_detail || '후보 찾는 중')}
        </span>}
      </div>

      {meta?.id === job && RUNNING.has(meta.status) && <section className="vl-panel vl-progress-card">
        <div className="vl-progress-title"><strong>영상 후보를 찾고 있습니다</strong><span>{meta.progress || 0}%</span></div>
        <div className="vl-progress-track"><span style={{ width: Math.min(100, meta.progress || 0) + '%' }} /></div>
        <p>{meta.stage_detail || 'RunPod에서 프레임별 후보를 찾고 있습니다.'}</p>
      </section>}
      {meta?.id === job && meta.status === 'failed' && <section className="vl-panel">
        <ErrorMessage message={meta.error || '후보 찾기에 실패했습니다.'} />
        <p className="vl-help">작업이 실패했습니다. 영상 검수 화면에서 다시 접수할 수 있습니다.</p>
      </section>}
      {error && <section className="vl-panel"><ErrorMessage message={error} /></section>}

      {ready && <>
        <nav className="vl-stepbar" aria-label="검수 단계">
          <button type="button" className={view === 'confirm' ? 'active' : 'done'} onClick={() => changeView('confirm')}>
            <span>1</span><strong>후보 위치 확인</strong><small>{savedCount} / {refNames.length}개 저장</small>
          </button>
          <i />
          <button type="button" className={view === 'compare' ? 'active' : ''} onClick={() => changeView('compare')}>
            <span>2</span><strong>정밀 대조 근거</strong><small>{meta.compare_status === 'completed' ? '대조 완료' : '저장 후 실행'}</small>
          </button>
        </nav>
        {missing.length > 0 && <div className="vl-warning">
          <strong>프레임 누락 {missing.length}초</strong>
          <span>누락된 시점은 후보 찾기와 정밀 대조에서 제외됩니다: {spanText(missing)}</span>
        </div>}

        {view === 'confirm' ? <div className="vl-layout">
          <aside className="vl-logo-sidebar">
            <div className="vl-sidebar-title"><span className="vl-kicker">기준 로고</span><strong>{refNames.length}개</strong></div>
            <div className="vl-logo-list">
              {refNames.map((name, i) => <button type="button" key={i} className={'vl-logo-row' + (i === refIndex ? ' active' : '')}
                onClick={() => changeReference(i)} aria-current={i === refIndex ? 'step' : undefined}>
                <img src={base + '/refs/' + i + '.png'} alt="" />
                <span><strong>{name}</strong><small>{answerMatchesSelection(i) ? answers[i].decision === 'none' ? '영상에 없음 · 저장됨' : '위치 확정 · 저장됨' : drafts[i]?.decision ? '저장 필요' : '확인 필요'}</small></span>
                <i>{answerMatchesSelection(i) ? '✓' : (i + 1)}</i>
              </button>)}
            </div>
            <p className="vl-sidebar-note">각 기준 로고에 해당하는 구간을 확인하고 저장하세요.</p>
          </aside>

          <section className="vl-workspace">
            {refInfo && <div className="vl-current-ref">
              <img src={base + '/' + refInfo.img} alt={refInfo.name + ' 원본'} />
              <div><span className="vl-kicker">현재 확인 중 · {refIndex + 1} / {refNames.length}</span><h1>{refInfo.name}</h1>
                <p>순위는 탐색 보조 자료입니다. 화면에서 로고 영역과 시간 구간을 직접 확인하세요.</p></div>
            </div>}

            {mode === 'manual' ? <ManualEditor video={video} initialSec={sec} onCancel={() => setMode('candidates')} onDone={markManual} /> :
              <CandidateWorkspace video={video} refIndex={refIndex} checked={checked} onToggle={toggleGroup} sec={sec} setSec={setSec}
                onConfirm={confirmGroups} onNone={markNone} onManual={() => setMode('manual')}
                onLinkToggle={toggleGroup} confirmed={confirmed} manuals={manuals}
                onRemoveGroup={removeGroup} onRemoveManual={removeManual} decision={decision} />}

            {payload && <ExposureSummary video={video} payload={payload} />}
            {decision && <section className="vl-savebar">
              <div><strong>{decision === 'none' ? '영상에 해당 로고가 없는 것으로 표시했습니다.' : '선택한 위치를 확인해 주세요.'}</strong>
                <span>{decision === 'none' ? '이 기록은 브라우저에 저장됩니다.' : '정밀 대조에는 이 브라우저에 저장한 묶음과 영역이 전달됩니다.'}</span></div>
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
        </div> : <CompareResults base={base} refs={video.refs} onBack={() => changeView('confirm')} onStatus={onCompareStatus} />}
      </>}
    </div>}
  </main>
    <WorkspaceFooter />
  </div>;
}
