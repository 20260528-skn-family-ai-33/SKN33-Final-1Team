import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { fetchJson } from '../../api/client.js';
import { SiteHeader } from '../../components/SiteHeader.jsx';
import { logoSteps, WorkspaceProjectBar } from '../../layouts/ReviewLayout.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { useWorkspaceReset } from '../../workspace/WorkspaceSession.jsx';
import { POSTER_LOGO_API, posterLogoAnswer, rankedCandidates, TOP_N } from './posterLogoFlow.js';
import '../video/logoConfirm.css';
import './posterLogo.css';

// 포스터 로고 위치 확정(1) → 정밀 대조 근거(2). 영상 로고 확정 화면과 같은 흐름이며, 포스터는 한 장이라 시간 구간이 없다.
// 순위는 후보를 찾는 데만 쓰고, 같은 로고인지와 최종 일치 여부는 사람이 판단한다.
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
const EMPTY_SELECTION = { decision: null, checked: [], manuals: [] };

function boxStyle(box, size) {
  const W = size[0] || 1, H = size[1] || 1;
  return { left: (box[0] / W * 100) + '%', top: (box[1] / H * 100) + '%',
    width: ((box[2] - box[0]) / W * 100) + '%', height: ((box[3] - box[1]) / H * 100) + '%' };
}

// 세로 포스터가 화면 높이를 넘지 않게 너비를 화면 높이 기준으로 제한한다.
function posterFit(size, vh = 74) {
  const ratio = (size[0] || 1) / (size[1] || 1);
  return { aspectRatio: size[0] + ' / ' + size[1], width: 'min(100%, calc(' + vh + 'vh * ' + ratio.toFixed(4) + '))' };
}

function boxText(box) {
  return Math.round(box[2] - box[0]) + '×' + Math.round(box[3] - box[1]) + 'px';
}

function CropPreview({ poster, box, compact = false }) {
  if (!poster || !box) return <div className="vl-crop vl-crop-empty">미리보기 없음</div>;
  const W = poster.size[0], H = poster.size[1];
  const x1 = Math.max(0, Math.min(W - 1, box[0])), y1 = Math.max(0, Math.min(H - 1, box[1]));
  const x2 = Math.max(x1 + 1, Math.min(W, box[2])), y2 = Math.max(y1 + 1, Math.min(H, box[3]));
  const width = x2 - x1, height = y2 - y1;
  const scale = Math.min((compact ? 248 : 350) / width, (compact ? 112 : 166) / height);
  return <div className={'vl-crop' + (compact ? ' compact' : '')} aria-label={'로고 영역 ' + boxText(box)}>
    <span style={{ width: width * scale, height: height * scale, backgroundImage: 'url("' + poster.url + '")',
      backgroundSize: (W * scale) + 'px ' + (H * scale) + 'px', backgroundPosition: (-x1 * scale) + 'px ' + (-y1 * scale) + 'px' }} />
  </div>;
}

function PosterViewer({ poster, overlays, onSelect }) {
  return <div className="vl-frame pl-poster" style={posterFit(poster.size)}>
    <img src={poster.url} alt="검수 포스터" />
    {overlays.map(item => <button type="button" key={item.id} className={'vl-box' + (item.selected ? ' selected' : '')}
      style={boxStyle(item.box, poster.size)} aria-label={item.label} title={item.label} aria-pressed={item.selected}
      onClick={() => onSelect && onSelect(item.id)}><span>{item.label}</span></button>)}
  </div>;
}

function CandidateCard({ poster, refIndex, candidateIndex, rank, selected, onToggle }) {
  const candidate = poster.candidates[candidateIndex];
  const box = candidate.rep[refIndex];
  const ocr = poster.refs[refIndex].ocr[String(candidateIndex)];
  return <article className={'vl-candidate' + (selected ? ' selected' : '')}>
    <button type="button" className="vl-candidate-image" onClick={onToggle} aria-pressed={selected}>
      <CropPreview poster={poster} box={box} compact />
      <span className="vl-rank">#{rank}</span>
      <span className="vl-candidate-check">{selected ? '선택됨' : '선택'}</span>
    </button>
    <div className="vl-candidate-info">
      <div className="vl-candidate-score"><span>순위 점수</span><strong>{Number(candidate.rank[refIndex]).toFixed(3)}</strong></div>
      <p className="pl-candidate-meta"><span>유사도 {Number(candidate.score[refIndex]).toFixed(3)}</span><span>탐지 {Number(candidate.owl).toFixed(2)}</span><span>{boxText(box)}</span></p>
      {ocr && ocr[0] && <div className="vl-ocr-chip"><span>{ocr[1] ? '문구 유사' : 'OCR'}</span>{ocr[0]}</div>}
    </div>
  </article>;
}

function ManualEditor({ poster, onCancel, onDone }) {
  const [box, setBox] = useState(null);
  const [draft, setDraft] = useState(null);
  const canvas = useRef(null);
  const start = useRef(null);
  const W = poster.size[0], H = poster.size[1];
  function point(event) {
    const rect = canvas.current.getBoundingClientRect();
    return [Math.max(0, Math.min(W, (event.clientX - rect.left) * W / rect.width)),
      Math.max(0, Math.min(H, (event.clientY - rect.top) * H / rect.height))];
  }
  const span = (a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
  function pointerDown(event) {
    if (event.button !== 0) return;
    event.preventDefault(); start.current = point(event); setDraft(null);
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function pointerMove(event) { if (start.current) setDraft(span(start.current, point(event))); }
  function pointerUp(event) {
    if (!start.current) return;
    const next = span(start.current, point(event)); start.current = null; setDraft(null);
    if (next[2] - next[0] >= 8 && next[3] - next[1] >= 8) setBox(next);
  }
  return <section className="vl-panel vl-manual">
    <div className="vl-section-heading">
      <div><span className="vl-kicker">직접 지정</span><h2>포스터에서 로고 영역을 표시하세요</h2></div>
      <button type="button" className="vl-text-button" onClick={onCancel}>후보 화면으로</button>
    </div>
    <p className="vl-help">포스터 위에서 드래그해 로고를 감싸세요. 표시한 영역은 넓히지 않고 그대로 원본과 대조합니다.</p>
    <div className="vl-manual-workspace">
      <div ref={canvas} className="vl-manual-canvas pl-manual-canvas" style={posterFit(poster.size)}
        onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp}>
        <img src={poster.url} alt="검수 포스터" draggable="false" />
        {box && <span className="vl-draw-box" style={boxStyle(box, poster.size)} />}
        {draft && <span className="vl-draw-box drawing" style={boxStyle(draft, poster.size)} />}
      </div>
      <aside className="vl-manual-side">
        <div className="vl-manual-box">
          <strong>선택 영역</strong>
          {box ? <CropPreview poster={poster} box={box} /> : <p>포스터 위를 드래그하세요.</p>}
          {box && <button type="button" className="vl-text-button" onClick={() => setBox(null)}>영역 다시 그리기</button>}
        </div>
        <div className="vl-manual-actions">
          <button type="button" className="vl-button secondary" onClick={onCancel}>취소</button>
          <button type="button" className="vl-button primary" disabled={!box} onClick={() => onDone(box)}>영역 확정</button>
        </div>
      </aside>
    </div>
  </section>;
}

function SelectionSummary({ poster, refIndex, checked, manuals, onRemoveCandidate, onRemoveManual }) {
  if (!checked.size && !manuals.length) return <div className="vl-selection-empty">선택한 위치가 없습니다. 후보를 선택하거나 포스터에서 직접 지정하세요.</div>;
  const ranked = rankedCandidates(poster.candidates, refIndex);
  return <div className="vl-selection-list">
    {[...checked].map(index => <article className="vl-selected-item" key={'candidate-' + index}>
      <CropPreview poster={poster} box={poster.candidates[index].rep[refIndex]} compact />
      <div><span className="vl-mini-label">후보 #{ranked.indexOf(index) + 1}</span>
        <strong>점수 {Number(poster.candidates[index].rank[refIndex]).toFixed(3)}</strong>
        <small>{boxText(poster.candidates[index].rep[refIndex])}</small></div>
      <button type="button" aria-label="선택에서 제거" onClick={() => onRemoveCandidate(index)}>×</button>
    </article>)}
    {manuals.map((box, index) => <article className="vl-selected-item" key={'manual-' + index}>
      <CropPreview poster={poster} box={box} compact />
      <div><span className="vl-mini-label manual">직접 지정</span><strong>{boxText(box)}</strong><small>표시한 영역 그대로</small></div>
      <button type="button" aria-label="직접 지정 영역 제거" onClick={() => onRemoveManual(index)}>×</button>
    </article>)}
  </div>;
}

function CandidateWorkspace({ poster, refIndex, checked, manuals, decision, onToggle, onManual, onNone, onRemoveManual }) {
  const ranked = useMemo(() => rankedCandidates(poster.candidates, refIndex), [poster.candidates, refIndex]);
  const [limit, setLimit] = useState(10);
  const [extra, setExtra] = useState(0);
  useEffect(() => setExtra(0), [refIndex]);
  const top = ranked.slice(0, TOP_N);
  const rest = ranked.slice(TOP_N, TOP_N + extra);
  const extraCount = Math.max(0, ranked.length - TOP_N);
  const overlays = ranked.slice(0, limit).map((index, order) => ({ id: index, box: poster.candidates[index].rep[refIndex],
    selected: checked.has(index), label: '#' + (order + 1) + ' 후보' + (checked.has(index) ? ' · 선택됨' : '') }))
    .concat(manuals.map((box, i) => ({ id: 'manual-' + i, box, selected: true, label: '직접 지정 ' + (i + 1) })));
  return <div className="vl-main-stack">
    <div className="vl-stage-heading">
      <div><span className="vl-kicker">01 / 위치 확인</span><h1>이 원본 로고가 있는 위치를 고르세요</h1>
        <p>순위는 후보를 찾는 데만 씁니다. 같은 로고인지는 화면에서 직접 확인해 주세요. 같은 로고가 여러 곳에 있으면 모두 고를 수 있습니다.</p></div>
      <div className="vl-stage-actions">
        <button type="button" className="vl-button subtle" onClick={onManual}>후보에 없음 · 직접 지정</button>
        <button type="button" className="vl-button secondary" onClick={onNone}>포스터에 없음</button>
      </div>
    </div>
    <div className="vl-review-grid">
      <div className="vl-review-left">
        <section className="vl-viewer-card vl-panel">
          <div className="vl-section-heading">
            <div><span className="vl-kicker">후보 위치</span><h2>포스터에서 후보를 확인하세요</h2></div>
            <label className="vl-soft-tag pl-filter">표시 후보 <select value={limit} onChange={event => setLimit(Number(event.target.value))}>
              {[3, 10, 30, 1000].map(value => <option key={value} value={value}>{value >= 1000 ? '전체' : '상위 ' + value + '개'}</option>)}
            </select></label>
          </div>
          <PosterViewer poster={poster} overlays={overlays} onSelect={id => typeof id === 'number' && onToggle(id)} />
          <p className="vl-help">{ranked.length ? '박스를 눌러 후보를 선택하거나 해제하세요.' : '찾은 후보가 없습니다. 포스터에서 직접 지정할 수 있습니다.'}</p>
        </section>
        <section className="vl-panel">
          <div className="vl-section-heading"><div><span className="vl-kicker">상위 후보</span><h2>원본 로고와 같은 위치를 선택하세요</h2></div>
            <span className="vl-soft-tag">여러 위치 선택 가능</span></div>
          <div className="vl-candidate-grid">{top.map((index, order) => <CandidateCard key={index} poster={poster} refIndex={refIndex}
            candidateIndex={index} rank={order + 1} selected={checked.has(index)} onToggle={() => onToggle(index)} />)}</div>
          {!top.length && <p className="vl-empty-inline">순위에 표시할 후보가 없습니다.</p>}
          {extraCount > 0 && <button type="button" className="vl-text-button" onClick={() => setExtra(value => value >= extraCount ? 0 : Math.min(extraCount, value + 15))}>
            {extra >= extraCount ? '상위 ' + TOP_N + '개만 보기' : extra === 0 ? '다른 후보도 보기 (' + extraCount + '개)' : '후보 더 보기 · ' + extra + ' / ' + extraCount}
          </button>}
          {rest.length > 0 && <div className="vl-candidate-grid vl-candidate-grid-extra">{rest.map(index => <CandidateCard key={index} poster={poster}
            refIndex={refIndex} candidateIndex={index} rank={ranked.indexOf(index) + 1} selected={checked.has(index)} onToggle={() => onToggle(index)} />)}</div>}
        </section>
      </div>
      <aside className="vl-review-right">
        <section className="vl-panel vl-selection-panel">
          <div className="vl-section-heading"><div><span className="vl-kicker">확정할 위치</span><h2>선택 내용</h2></div>
            <span className="vl-count">{checked.size + manuals.length}</span></div>
          <SelectionSummary poster={poster} refIndex={refIndex} checked={checked} manuals={manuals}
            onRemoveCandidate={onToggle} onRemoveManual={onRemoveManual} />
          {decision === 'none' && <div className="vl-none-state">이 원본 로고는 포스터에 없는 것으로 표시했습니다.</div>}
          {decision !== 'none' && <button type="button" className="vl-button subtle vl-wide" onClick={onManual}>직접 지정 영역 추가</button>}
        </section>
      </aside>
    </div>
  </div>;
}

function colorLabel(color) {
  if (!color) return '색상 정보 없음';
  if (color.mono) return '단색(흰색·검은색) 로고 · 색 비교 생략';
  const items = [];
  if (color.hue != null) items.push('색상 차이 ' + color.hue + '°');
  if (color.lostc != null) items.push('빠진 유채색 ' + Math.round(color.lostc * 100) + '%');
  if (color.newc != null) items.push('새 유채색 ' + Math.round(color.newc * 100) + '%');
  return items.join(' · ') || '색상 비교 근거 부족';
}

function LocationEvidence({ base, item, refInfo }) {
  const refName = refInfo?.name || item.reference || ('원본 로고 ' + (item.logo + 1));
  const outcome = OUTCOME[item.outcome] || { label: item.outcome, tone: 'unknown' };
  return <article className="vl-evidence-card">
    <header className="vl-evidence-head">
      <div><span className="vl-kicker">{item.source === 'candidate' ? '후보 위치' : '직접 지정'}</span>
        <h2>{refName} · 위치 {item.crop + 1}</h2>
        <p>영역 {item.size[0]}×{item.size[1]}px · 선명도 {item.sharp}{item.rank != null ? ' · 순위 점수 ' + Number(item.rank).toFixed(3) : ''}</p></div>
      <span className={'vl-result-pill ' + outcome.tone}>{outcome.label}</span>
    </header>
    <div className="vl-evidence-images">
      <figure><img src={base + '/' + (refInfo?.img || ('refs/' + item.logo + '.png'))} alt={refName + ' 원본'} /><figcaption>원본 로고</figcaption></figure>
      <figure><img src={base + '/compare/crop/' + item.logo + '/' + item.crop + '.jpg'} alt={refName + ' 포스터 영역'} /><figcaption>포스터에서 대조한 영역</figcaption></figure>
      <div className="vl-evidence-summary">
        <p className="vl-color-line">{colorLabel(item.color)}</p>
        {item.findings?.length > 0 ? <ul className="vl-finding-list">{item.findings.map(text => <li key={text}><span>{text}</span></li>)}</ul>
          : <p className="vl-muted-copy">계산된 차이 근거가 없습니다. 일치 확정이 아니라 비교한 항목 기준의 결과입니다.</p>}
        <ul className="pl-element-list">{(item.elements || []).map((element, index) => <li key={index}>
          <span>{element.name}</span><span>{ELEMENT_STATUS[element.status] || element.status}</span></li>)}</ul>
        <p className="vl-muted-copy">OCR: {(item.ocr || []).join(' / ') || '읽힌 글자 없음'}</p>
        {item.small && <p className="pl-small-note">짧은 변이 60px 미만인 작은 영역입니다. 정상 로고도 차이 근거가 나오기 쉬우니 원본과 직접 비교해 주세요.</p>}
      </div>
    </div>
  </article>;
}

function CompareResults({ base, refs, onBack, onStatus }) {
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); let timer;
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
  const locations = result.locations || [];
  const absent = result.absent || [];
  return <div className="vl-main-stack">
    <div className="vl-stage-heading">
      <div><span className="vl-kicker">02 / 근거 확인</span><h1>위치별 비교 근거를 확인하세요</h1>
        <p>이 화면은 자동 최종 판정을 내리지 않습니다. 요소·문구·색 근거와 두 이미지를 보고 판단해 주세요.</p></div>
      <button type="button" className="vl-button secondary" onClick={onBack}>위치 확정으로 돌아가기</button>
    </div>
    {RUNNING.has(result.compare_status) && <section className="vl-panel vl-progress-card">
      <div className="vl-progress-title"><strong>정밀 대조 중</strong><span>{result.compare_progress || 0}%</span></div>
      <div className="vl-progress-track"><span style={{ width: Math.min(100, result.compare_progress || 0) + '%' }} /></div>
      <p>확정한 위치를 원본 로고와 비교하고 있습니다.</p>
    </section>}
    {result.compare_status === 'failed' && <section className="vl-panel"><ErrorMessage message={result.compare_error || '정밀 대조가 완료되지 않았습니다.'} /></section>}
    {!result.compare_status && <section className="vl-panel vl-empty">
      <strong>아직 정밀 대조를 실행하지 않았습니다.</strong><p>원본 로고마다 위치를 확정해 저장한 뒤 정밀 대조를 실행하세요.</p>
      <button type="button" className="vl-button primary" onClick={onBack}>위치 확인으로 이동</button>
    </section>}
    {(result.warnings || []).map(warning => <div className="vl-warning" key={warning}>⚠ {warning}</div>)}
    {absent.length > 0 && <section className="vl-panel">
      <div className="vl-section-heading"><div><span className="vl-kicker">사람 확인</span><h2>포스터에 없음으로 표시한 원본 로고</h2></div></div>
      <ul className="pl-absent-list">{absent.map(item => <li key={item.logo}>
        <img src={base + '/' + (refs[item.logo]?.img || ('refs/' + item.logo + '.png'))} alt="" />{refs[item.logo]?.name || item.reference}</li>)}</ul>
    </section>}
    {result.compare_status === 'completed' && !locations.length && !absent.length && <section className="vl-panel vl-empty">
      <strong>대조 결과가 없습니다.</strong><p>대조할 수 있는 위치가 없었습니다.</p></section>}
    {locations.map(item => <LocationEvidence key={item.logo + '-' + item.crop} base={base} item={item} refInfo={refs[item.logo]} />)}
  </div>;
}

export default function PosterLogoPage() {
  const go = useNavigate();
  const [params, setParams] = useSearchParams();
  const job = params.get('job');
  const view = params.get('view') === 'compare' ? 'compare' : 'confirm';
  const base = POSTER_LOGO_API + '/' + encodeURIComponent(job || '');
  const storageKey = 'poster-logo-review:v1:' + (job || 'new');
  const [meta, setMeta] = useState(null);
  const [poster, setPoster] = useState(null);
  const [error, setError] = useState('');
  const [refIndex, setRefIndex] = useState(0);
  const [answers, setAnswers] = useState({});
  const [drafts, setDrafts] = useState({});
  const [selection, setSelection] = useState(EMPTY_SELECTION);
  const [mode, setMode] = useState('candidates');
  const [busy, setBusy] = useState(false);
  const active = useRef(null);
  useWorkspaceReset(() => active.current?.abort());
  useEffect(() => () => active.current?.abort(), []);

  useEffect(() => {
    if (!job) return undefined;
    const controller = new AbortController(); let timer;
    setMeta(null); setPoster(null); setError(''); setRefIndex(0);
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
      setAnswers(saved?.answers || {}); setDrafts(saved?.drafts || {});
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
    fetchJson(base + '/data.json', { signal: controller.signal })
      .then(data => setPoster({ ...data, base, url: base + '/' + (data.image || 'poster.jpg') }))
      .catch(failure => { if (failure.name !== 'AbortError') setError(failure.message); });
    return () => controller.abort();
  }, [job, base, meta?.id, meta?.status]);

  const refs = poster?.refs || [];
  const refInfo = refs[refIndex];
  const ready = meta?.id === job && meta.status === 'candidates_ready' && !!poster;
  const comparing = RUNNING.has(meta?.compare_status);
  const checked = useMemo(() => new Set(selection.checked), [selection.checked]);
  const decision = selection.decision === 'none' ? 'none' : (selection.checked.length || selection.manuals.length) ? 'done' : null;

  // 저장한 값이 지금(또는 다른 로고는 마지막으로 본) 선택과 같을 때만 저장된 것으로 본다.
  const selectionOf = useCallback(index => (index === refIndex ? selection : drafts[index] || answers[index]?.selection || EMPTY_SELECTION),
    [refIndex, selection, drafts, answers]);
  const answerFor = useCallback((index, value) => {
    const chosen = value.decision === 'none' ? 'none' : (value.checked.length || value.manuals.length) ? 'done' : null;
    return chosen && refs[index] ? posterLogoAnswer({ ref: index, refName: refs[index].name, decision: chosen,
      checked: new Set(value.checked), manuals: value.manuals }) : null;
  }, [refs]);
  const isSaved = useCallback(index => {
    const expected = answerFor(index, selectionOf(index)); const saved = answers[index];
    return !!(expected && saved && saved.decision === expected.decision && JSON.stringify(saved.crops) === JSON.stringify(expected.crops));
  }, [answerFor, selectionOf, answers]);
  const savedIndexes = refs.map((_, i) => i).filter(isSaved);
  const onCompareStatus = useCallback(value => {
    setMeta(previous => previous && previous.id === job ? { ...previous, compare_status: value } : previous);
  }, [job]);

  useEffect(() => {
    if (!ready) return;
    setSelection(drafts[refIndex] || answers[refIndex]?.selection || EMPTY_SELECTION);
    setMode('candidates');
    // 로고를 바꿀 때만 선택을 다시 읽는다. 저장 시점의 answers·drafts 변경으로 편집 중인 선택을 덮지 않는다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, refIndex]);

  function keep(nextAnswers, nextDrafts) {
    setAnswers(nextAnswers); setDrafts(nextDrafts);
    try { localStorage.setItem(storageKey, JSON.stringify({ answers: nextAnswers, drafts: nextDrafts })); }
    catch { /* 브라우저 저장소를 쓸 수 없으면 화면 안에서만 유지한다 */ }
  }
  function saveAnswer() {
    const answer = answerFor(refIndex, selection);
    if (!answer) return;
    keep({ ...answers, [refIndex]: { ...answer, selection } }, { ...drafts, [refIndex]: selection });
  }
  function changeReference(next) {
    if (next === refIndex) return;
    keep(answers, { ...drafts, [refIndex]: selection });
    setRefIndex(next);
  }
  function toggle(index) {
    setSelection(previous => ({ decision: null, manuals: previous.manuals,
      checked: previous.checked.includes(index) ? previous.checked.filter(value => value !== index) : [...previous.checked, index] }));
  }
  function addManual(box) {
    setSelection(previous => ({ decision: null, checked: previous.checked, manuals: [...previous.manuals, box.map(v => Math.round(v * 10) / 10)] }));
    setMode('candidates');
  }
  function removeManual(position) {
    setSelection(previous => ({ ...previous, manuals: previous.manuals.filter((_, index) => index !== position) }));
  }
  function markNone() { setSelection({ decision: 'none', checked: [], manuals: [] }); setMode('candidates'); }
  function resetSelection() { setSelection(EMPTY_SELECTION); keep(answers, { ...drafts, [refIndex]: EMPTY_SELECTION }); }
  function changeView(next) {
    const query = new URLSearchParams(params);
    if (next === 'compare') query.set('view', 'compare'); else query.delete('view');
    setParams(query);
  }
  async function runCompare() {
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError('');
    try {
      const body = savedIndexes.map(index => { const { selection: _, ...answer } = answers[index]; return answer; });
      await fetchJson(base + '/compare', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body), signal: controller.signal });
      if (controller.signal.aborted) return;
      setMeta(previous => previous && previous.id === job ? { ...previous, compare_status: 'queued' } : previous);
      changeView('compare');
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message);
    } finally {
      if (!controller.signal.aborted) { active.current = null; setBusy(false); }
    }
  }

  // 둘 다 검수는 포스터 검수 실행에 연결된 작업이라 첫 단계가 그 실행으로, 로고만 검수는 자료 입력으로 돌아간다.
  const linkedRun = meta?.run_id;
  const back = linkedRun ? '/review/new?framework=' + encodeURIComponent(linkedRun) : '/review/new';
  const steps = linkedRun ? { review: '포스터 검수', candidates: logoSteps.candidates, confirm: logoSteps.confirm, compare: logoSteps.compare } : logoSteps;
  const step = !job ? 'input' : ready ? view : 'candidates';
  const stepVersion = !ready ? '로고 검수' : view === 'compare'
    ? (meta.compare_status === 'completed' ? '대조 완료' : comparing ? '대조 중' : '저장 후 실행')
    : savedIndexes.length + ' / ' + refs.length + '개 저장';
  const saved = isSaved(refIndex);

  return <div className="rv-workspace rv-workspace--tool">
    <a href="#review-content" className="rv-skip">작업 영역으로 이동</a>
    <SiteHeader current="/review/new" />
    <WorkspaceProjectBar name={meta?.id === job ? meta.title : '로고 검수'} version={stepVersion} step={step} steps={steps} locked={busy}
      canNavigate={key => key === 'input' || key === 'review' || (ready && (key === 'confirm' || key === 'compare'))}
      navigate={key => { if (key === 'input' || key === 'review') go(back); else if (key === 'confirm' || key === 'compare') changeView(key); }}>
      {ready && <button type="button" className="rv-button primary" onClick={runCompare} disabled={!savedIndexes.length || busy || comparing}>
        {comparing ? '정밀 대조 중' : busy ? '요청 중…' : savedIndexes.length ? '저장한 ' + savedIndexes.length + '개 로고 정밀 대조' : '저장한 로고 없음'}
      </button>}
    </WorkspaceProjectBar>
    <main id="review-content" className="vl-page" tabIndex={-1}>
      {!job ? <section className="vl-empty-state">
        <div className="vl-empty-mark">01</div>
        <span className="vl-kicker">자료 입력에서 시작</span>
        <h1>자료 입력에서 로고 검수를 시작하세요</h1>
        <p>검수 유형에서 ‘로고 검수’ 또는 ‘둘 다 검수’를 고르고 포스터와 원본 로고를 첨부하면 포스터에서 로고 후보를 찾습니다.</p>
        <Link className="vl-button primary" to="/review/new">자료 입력 열기</Link>
      </section> : <div className="vl-content">
        <div className="vl-jobbar">
          <div><span className="vl-kicker">작업</span><strong>{meta?.id === job ? meta.title : '작업 정보를 불러오는 중'}</strong>
            {meta?.poster && <small> · {meta.poster.name} ({meta.poster.size[0]}×{meta.poster.size[1]})</small>}</div>
          {meta?.id === job && <span className={'vl-status ' + (meta.status === 'failed' ? 'failed' : meta.status === 'candidates_ready' ? 'ready' : 'working')}>
            {meta.status === 'failed' ? '실패' : meta.status === 'candidates_ready' ? '후보 ' + (meta.candidate_count ?? 0) + '개 준비' : (meta.stage_detail || '후보 찾는 중')}
          </span>}
        </div>
        {meta?.id === job && RUNNING.has(meta.status) && <section className="vl-panel vl-progress-card">
          <div className="vl-progress-title"><strong>포스터에서 로고 후보를 찾고 있습니다</strong><span>{meta.progress || 0}%</span></div>
          <div className="vl-progress-track"><span style={{ width: Math.min(100, meta.progress || 0) + '%' }} /></div>
          <p>{meta.stage_detail || 'RunPod에서 후보를 찾고 있습니다.'}</p>
        </section>}
        {meta?.id === job && meta.status === 'failed' && <section className="vl-panel">
          <ErrorMessage message={meta.error || '후보 찾기에 실패했습니다.'} />
          <p className="vl-help">{linkedRun ? '포스터 검수는 그대로 진행됩니다. 로고 워커 연결을 확인한 뒤 포스터를 다시 등록하면 새로 접수합니다.'
            : '로고 워커 연결을 확인한 뒤 자료 입력에서 다시 접수해 주세요.'}</p>
        </section>}
        {error && <section className="vl-panel"><ErrorMessage message={error} /></section>}

        {ready && <>
          {view === 'confirm' ? <div className="vl-layout">
            <aside className="vl-logo-sidebar">
              <div className="vl-sidebar-title"><span className="vl-kicker">기준 로고</span><strong>{refs.length}개</strong></div>
              <div className="vl-logo-list">{refs.map((item, index) => <button type="button" key={index}
                className={'vl-logo-row' + (index === refIndex ? ' active' : '')} onClick={() => changeReference(index)}
                aria-current={index === refIndex ? 'step' : undefined}>
                <img src={base + '/' + item.img} alt="" />
                <span><strong>{item.name}</strong><small>{isSaved(index) ? (answers[index].decision === 'none' ? '포스터에 없음 · 저장됨' : '위치 확정 · 저장됨')
                  : selectionOf(index).decision || selectionOf(index).checked.length || selectionOf(index).manuals.length ? '저장 필요' : '확인 필요'}</small></span>
                <i>{isSaved(index) ? '✓' : index + 1}</i>
              </button>)}</div>
              <p className="vl-sidebar-note">원본 로고마다 위치를 확인하고 저장하세요. 저장한 선택은 이 브라우저에 보관됩니다.</p>
            </aside>

            <section className="vl-workspace">
              {refInfo && <div className="vl-current-ref">
                <img src={base + '/' + refInfo.img} alt={refInfo.name + ' 원본'} />
                <div><span className="vl-kicker">현재 확인 중 · {refIndex + 1} / {refs.length}</span><h1>{refInfo.name}</h1>
                  <p>{refInfo.bonus_basis === 'openai' ? 'OpenAI가 포스터에서 찾은 위치를 확신도 순으로 보여 줍니다(일반 자료). 정밀 대조는 RunPod에서 합니다.'
                    : refInfo.bonus_basis === 'input' ? '기준 문구: ' + refInfo.bonus_text
                    : refInfo.bonus_basis === 'ocr' ? '기준 문구가 없어 원본 로고 OCR(' + refInfo.bonus_text + ')로 후보 가산점을 계산했습니다.'
                      : '기준 문구가 없고 원본 로고에서 글자를 읽지 못해 이미지 유사도로만 순위를 매겼습니다.'}</p></div>
              </div>}
              {mode === 'manual' ? <ManualEditor poster={poster} onCancel={() => setMode('candidates')} onDone={addManual} />
                : <CandidateWorkspace poster={poster} refIndex={refIndex} checked={checked} manuals={selection.manuals}
                  decision={decision} onToggle={toggle} onManual={() => setMode('manual')} onNone={markNone} onRemoveManual={removeManual} />}
              {decision && <section className="vl-savebar">
                <div><strong>{decision === 'none' ? '포스터에 이 원본 로고가 없는 것으로 표시했습니다.' : '선택한 위치 ' + (selection.checked.length + selection.manuals.length) + '곳을 확인해 주세요.'}</strong>
                  <span>{decision === 'none' ? '정밀 대조 결과에 사람 확인 기록으로 남습니다.' : '정밀 대조에는 이 브라우저에 저장한 위치가 전달됩니다.'}</span></div>
                <div className="vl-save-actions">
                  <button type="button" className="vl-button subtle" onClick={resetSelection}>다시 선택</button>
                  <button type="button" className="vl-button primary" onClick={saveAnswer} disabled={saved}>{saved ? '저장됨' : '이 원본 로고 확정 저장'}</button>
                  {refIndex + 1 < refs.length && <button type="button" className="vl-button secondary" onClick={() => {
                    const answer = answerFor(refIndex, selection);
                    const nextAnswers = answer ? { ...answers, [refIndex]: { ...answer, selection } } : answers;
                    keep(nextAnswers, { ...drafts, [refIndex]: selection }); setRefIndex(value => value + 1);
                  }}>저장하고 다음 로고</button>}
                </div>
              </section>}
            </section>
          </div> : <CompareResults base={base} refs={refs} onBack={() => changeView('confirm')} onStatus={onCompareStatus} />}
        </>}
      </div>}
    </main>
  </div>;
}
