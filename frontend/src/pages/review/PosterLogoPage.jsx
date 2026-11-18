import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { fetchJson } from '../../api/client.js';
import { SiteHeader } from '../../components/SiteHeader.jsx';
import { WorkspaceProjectBar } from '../../layouts/ReviewLayout.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { useWorkspaceReset } from '../../workspace/WorkspaceSession.jsx';
import { POSTER_LOGO_API, posterLogoAnswer, posterLogoSteps, rankedCandidates, TOP_N } from './posterLogoFlow.js';
import { findingText, groupDifferences, LINK_RATIO, OUTCOME } from '../video/logoFlow.js';
import LogoJobProgress from '../video/LogoJobProgress.jsx';
import '../video/logoConfirm.css';
import './posterLogo.css';

// 포스터 로고 정밀 대조 결과(기본 화면) · 위치 직접 확인 · 직접 지정. 영상 로고 확정 화면과 같은 흐름이며, 포스터는 한 장이라 시간 구간이 없다.
// 순위는 후보를 찾는 데만 쓰고, 같은 로고인지와 최종 일치 여부는 사람이 판단한다.
// 화면 짜임(2026-11-17): 동영상 로고 화면과 같은 카드·표현을 쓰고, 포스터 한 장이라는 점을 살려
// 왼쪽 잉크 무대(포스터 + 후보 상자·결과 번호)와 오른쪽 종이 면(후보 카드·결과 카드)으로 나눈다.
const RUNNING = new Set(['queued', 'running']);
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

// 후보 순위 점수를 화면용 일치도(0~100%)로: 0.912 → 91 (동영상 로고 화면과 같은 표시)
function matchPercent(rank) {
  return Math.max(0, Math.min(100, Math.round(Number(rank || 0) * 100)));
}

// 로고 조각 미리보기. 칸 너비에 맞춰 줄어들어 조각 전체가 항상 보인다(예전에는 고정 배율이라 좁은 칸에서 오른쪽이 잘렸다).
function CropPreview({ poster, box, compact = false }) {
  if (!poster || !box) return <div className="vl-crop vl-crop-empty">미리보기 없음</div>;
  const W = poster.size[0], H = poster.size[1];
  const x1 = Math.max(0, Math.min(W - 1, box[0])), y1 = Math.max(0, Math.min(H - 1, box[1]));
  const x2 = Math.max(x1 + 1, Math.min(W, box[2])), y2 = Math.max(y1 + 1, Math.min(H, box[3]));
  const width = x2 - x1, height = y2 - y1;
  return <div className={'vl-crop pl-crop' + (compact ? ' compact' : '')} role="img" aria-label="로고 영역 미리보기">
    <span style={{ aspectRatio: width + ' / ' + height, width: 'min(100%, calc(var(--pl-crop-h) * ' + (width / height).toFixed(4) + '))',
      backgroundImage: 'url("' + poster.url + '")', backgroundSize: (W / width * 100) + '% ' + (H / height * 100) + '%',
      backgroundPosition: (W > width ? x1 / (W - width) * 100 : 0) + '% ' + (H > height ? y1 / (H - height) * 100 : 0) + '%' }} />
  </div>;
}

// 포스터 위 후보 상자. 이름표는 동영상 화면처럼 상자 바깥 위(맨 위 상자는 아래)에 두고, 번호만 적어 붙어 있는 로고끼리 겹치지 않게 한다.
function PosterViewer({ poster, overlays, onSelect }) {
  return <div className="vl-frame pl-poster" style={posterFit(poster.size, 68)}>
    <img src={poster.url} alt="검수 포스터" />
    {overlays.map(item => <button type="button" key={item.id}
      className={'vl-box' + (item.selected ? ' selected' : '') + (item.box[1] / (poster.size[1] || 1) < 0.06 ? ' label-below' : '')}
      style={boxStyle(item.box, poster.size)} aria-label={item.label + (item.selected ? ' (선택됨)' : '')} title={item.label} aria-pressed={!!item.selected}
      onClick={() => onSelect && onSelect(item.id)}><span>{item.selected && <i aria-hidden="true">✓</i>}{item.short}</span></button>)}
  </div>;
}

function CandidateCard({ poster, refIndex, candidateIndex, rank, selected, onToggle }) {
  const candidate = poster.candidates[candidateIndex];
  const box = candidate.rep[refIndex];
  const ocr = poster.refs[refIndex].ocr[String(candidateIndex)];
  const percent = matchPercent(candidate.rank[refIndex]);
  return <article className={'vl-candidate' + (selected ? ' selected' : '')}>
    <button type="button" className="vl-candidate-image" onClick={onToggle} aria-pressed={selected} aria-label={'#' + rank + ' 후보 ' + (selected ? '선택 해제' : '선택')}>
      <span className="vl-candidate-top"><span className="vl-rank">#{rank}</span>
        <span className="vl-candidate-check" aria-hidden="true">{selected ? '✓' : ''}</span></span>
      <CropPreview poster={poster} box={box} compact />
    </button>
    <div className="vl-candidate-info">
      {/* 순위 점수·유사도·탐지 점수·픽셀 크기 대신 '일치도 n%' 하나만 보인다. 원래 값은 마우스를 올리면 보인다. */}
      <div className="vl-candidate-score" title={'순위 점수 ' + Number(candidate.rank[refIndex]).toFixed(3)}>
        <span>원본과 일치도</span><strong>{percent}%</strong>
        <i className="vl-candidate-meter" aria-hidden="true"><b style={{ width: percent + '%' }} /></i></div>
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
  return <div className="vl-main-stack">
    <div className="vl-stage-heading vl-confirm-heading">
      <h2>포스터에서 로고 영역 표시하기</h2>
      <div className="vl-stage-actions"><button type="button" className="vl-button secondary" onClick={onCancel}>후보 화면으로</button></div>
    </div>
    <div className="pl-confirm-grid">
      <section className="pl-stage" aria-label="로고 영역을 표시할 포스터">
        <div ref={canvas} className="vl-manual-canvas pl-manual-canvas" style={posterFit(poster.size, 68)}
          onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp}>
          <img src={poster.url} alt="검수 포스터" draggable="false" />
          {box && <span className="vl-draw-box" style={boxStyle(box, poster.size)} />}
          {draft && <span className="vl-draw-box drawing" style={boxStyle(draft, poster.size)} />}
        </div>
        <div className="pl-stage-scale"><span>직접 지정</span><span>포스터 위에서 드래그해 로고를 감싸세요</span><span>{box ? '영역 1곳' : '영역 없음'}</span></div>
      </section>
      <div className="pl-confirm-side">
        <section className="vl-panel pl-manual-panel">
          <div className="vl-section-heading"><h2>표시한 영역</h2></div>
          {box ? <CropPreview poster={poster} box={box} /> : <div className="vl-selection-empty">아직 표시한 영역이 없어요.</div>}
          <p className="vl-help">표시한 영역은 넓히지 않고 그대로 원본과 대조합니다.</p>
          <div className="vl-manual-actions">
            {box && <button type="button" className="vl-button subtle" onClick={() => setBox(null)}>다시 그리기</button>}
            <button type="button" className="vl-button secondary" onClick={onCancel}>취소</button>
            <button type="button" className="vl-button primary" disabled={!box} onClick={() => onDone(box)}>영역 확정</button>
          </div>
        </section>
      </div>
    </div>
  </div>;
}

function SelectionSummary({ poster, refIndex, checked, manuals, onRemoveCandidate, onRemoveManual }) {
  if (!checked.size && !manuals.length) return <div className="vl-selection-empty">선택한 위치가 없어요.</div>;
  const ranked = rankedCandidates(poster.candidates, refIndex);
  return <div className="vl-selection-list">
    {[...checked].map(index => <article className="vl-selected-item" key={'candidate-' + index}>
      <CropPreview poster={poster} box={poster.candidates[index].rep[refIndex]} compact />
      <div><span className="vl-mini-label">후보 #{ranked.indexOf(index) + 1}</span>
        <small>일치도 {matchPercent(poster.candidates[index].rank[refIndex])}%</small></div>
      <button type="button" aria-label="선택에서 제거" onClick={() => onRemoveCandidate(index)}>×</button>
    </article>)}
    {manuals.map((box, index) => <article className="vl-selected-item" key={'manual-' + index}>
      <CropPreview poster={poster} box={box} compact />
      <div><span className="vl-mini-label manual">직접 지정</span><small>표시한 영역 그대로</small></div>
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
    selected: checked.has(index), label: '#' + (order + 1) + ' 후보', short: '#' + (order + 1) }))
    .concat(manuals.map((box, i) => ({ id: 'manual-' + i, box, selected: true, label: '직접 지정 ' + (i + 1), short: '직접 ' + (i + 1) })));
  return <div className="vl-main-stack">
    <div className="vl-stage-heading vl-confirm-heading">
      <h2>로고가 있는 위치 고르기</h2>
      <div className="vl-stage-actions">
        <button type="button" className="vl-button subtle" onClick={onManual}>직접 지정</button>
        <button type="button" className="vl-button secondary" onClick={onNone}>포스터에 없음</button>
      </div>
    </div>
    <div className="pl-confirm-grid">
      {/* 왼쪽: 잉크 무대 위 포스터. 화면을 내려도 따라와서 후보 카드와 포스터를 한눈에 본다. */}
      <section className="pl-stage" aria-label="후보 위치가 표시된 포스터">
        <PosterViewer poster={poster} overlays={overlays} onSelect={id => typeof id === 'number' && onToggle(id)} />
        <div className="pl-stage-scale">
          <span>후보 {ranked.length}곳</span>
          <span>{ranked.length ? '상자를 눌러 선택하거나 해제하세요' : '찾은 후보가 없어요. 직접 지정할 수 있어요'}</span>
          <label>표시 <select value={limit} onChange={event => setLimit(Number(event.target.value))}>
            {[3, 10, 30, 1000].map(value => <option key={value} value={value}>{value >= 1000 ? '전체' : '상위 ' + value + '개'}</option>)}
          </select></label>
        </div>
      </section>
      <div className="pl-confirm-side">
        <section className="vl-panel">
          <div className="vl-section-heading"><h2>후보 위치</h2><span className="vl-soft-tag">여러 개 선택 가능</span></div>
          <div className="vl-candidate-grid">{top.map((index, order) => <CandidateCard key={index} poster={poster} refIndex={refIndex}
            candidateIndex={index} rank={order + 1} selected={checked.has(index)} onToggle={() => onToggle(index)} />)}</div>
          {!top.length && <p className="vl-empty-inline">후보가 없어요. ‘직접 지정’으로 영역을 표시하세요.</p>}
          {extraCount > 0 && <button type="button" className="vl-text-button" onClick={() => setExtra(value => value >= extraCount ? 0 : Math.min(extraCount, value + 15))}>
            {extra >= extraCount ? '상위 ' + TOP_N + '개만 보기' : extra === 0 ? '다른 후보도 보기 (' + extraCount + '개)' : '후보 더 보기 · ' + extra + ' / ' + extraCount}
          </button>}
          {rest.length > 0 && <div className="vl-candidate-grid vl-candidate-grid-extra">{rest.map(index => <CandidateCard key={index} poster={poster}
            refIndex={refIndex} candidateIndex={index} rank={ranked.indexOf(index) + 1} selected={checked.has(index)} onToggle={() => onToggle(index)} />)}</div>}
          <p className="vl-help">순위는 후보를 찾는 데만 써요. 같은 로고인지는 직접 확인해 주세요.</p>
        </section>
        <section className="vl-panel vl-selection-panel">
          <div className="vl-section-heading"><h2>선택한 위치</h2><span className="vl-count">{checked.size + manuals.length}</span></div>
          <SelectionSummary poster={poster} refIndex={refIndex} checked={checked} manuals={manuals}
            onRemoveCandidate={onToggle} onRemoveManual={onRemoveManual} />
          {decision === 'none' && <div className="vl-none-state">이 원본 로고는 포스터에 없는 것으로 표시했습니다.</div>}
          {decision !== 'none' && <button type="button" className="vl-button subtle vl-wide" onClick={onManual}>영역 직접 추가</button>}
        </section>
      </div>
    </div>
  </div>;
}

// 정밀 대조 재검수의 진행 화면(2026-11-18부터 재검수에만 쓴다). 위치를 직접 확인하고 다시 대조할 때 원본 로고 카드를 훑는 모습을 보인다.
// 처음 접수한 작업의 자동 진행(후보 찾기 → 자동 정밀 대조)은 LogoJobProgress 한 화면이 이어서 보여 준다.
const COMPARE_STEPS = [['request', '대조 요청'], ['compare', '위치별 정밀 대조'], ['organize', '결과 정리']];
function CompareProgress({ status, progress, base, refs, auto }) {
  const value = Math.min(100, Math.max(0, Math.round(progress || 0)));
  const active = status === 'running' ? (value >= 95 ? 2 : 1) : 0;
  const title = status === 'running' ? '원본 로고와 포스터 속 로고를 정밀 대조하고 있어요.' : '정밀 대조 차례를 기다리고 있어요.';
  return <section className="vl-compare-progress" aria-labelledby="compare-progress-title">
    <div className="vl-compare-stage" aria-hidden="true">
      <div className="vl-compare-stage-logos">{(refs || []).slice(0, 3).map((ref, index) =>
        <span key={index} className="vl-compare-stage-logo"><img src={base + '/' + (ref.img || ('refs/' + index + '.png'))} alt="" /><i /></span>)}</div>
      <span className="vl-compare-stage-caption">{status === 'running' ? '포스터 속 영역을 원본과 비교하는 중' : '대조 준비 중'}</span>
    </div>
    <div className="vl-compare-progress-body">
      <span className="vl-compare-progress-eyebrow">LOGO PRECISION CHECK IN PROGRESS</span>
      <h2 id="compare-progress-title">{title}</h2>
      <span className="visually-hidden" role="status">{COMPARE_STEPS[active][1]} 단계 진행 중</span>
      <div className="vl-compare-progress-value"><span>정밀 대조 진행률</span><strong>{status === 'running' ? value + '%' : '대기'}</strong></div>
      <div className="vl-compare-progress-track" role="progressbar" aria-label="정밀 대조 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value}>
        <i style={{ width: (status === 'running' ? Math.max(4, value) : 4) + '%' }} /></div>
      <ol className="vl-compare-progress-steps">{COMPARE_STEPS.map(([key, label], index) =>
        <li key={key} className={index < active ? 'is-completed' : index === active ? 'is-active' : 'is-pending'} aria-current={index === active ? 'step' : undefined}>
          <span aria-hidden="true">{index < active ? '✓' : String(index + 1).padStart(2, '0')}</span>{label}</li>)}</ol>
      <p className="vl-compare-progress-help">{auto ? '순위가 높은 후보를 원본과 비교해요. 검수하려던 로고가 아니면 위치 직접 확인에서 고칠 수 있어요.'
        : '확정한 위치를 원본 로고와 비교하고 있어요.'} 페이지를 닫아도 서버에서 계속 대조합니다.</p>
    </div>
  </section>;
}

// 결과 카드: 동영상 로고의 결과 카드(vl-cmp-card)와 같은 모양. 번호는 왼쪽 포스터의 표시와 짝을 이룬다.
function LocationEvidence({ base, item, number, refInfo, several, active, onActive }) {
  const refName = refInfo?.name || item.reference || ('원본 로고 ' + (item.logo + 1));
  const outcome = OUTCOME[item.outcome] || OUTCOME.unknown;
  const differences = groupDifferences([{ sec: 0, outcome: item.outcome, findings: item.findings }]);
  return <article id={'pl-result-' + number} className={'vl-evidence-card vl-cmp-card pl-cmp-card is-' + outcome.tone + (active ? ' is-active' : '')}
    onMouseEnter={() => onActive(number)} onMouseLeave={() => onActive(null)} onFocus={() => onActive(number)} onBlur={() => onActive(null)}>
    <header className="vl-cmp-head">
      <div className="vl-cmp-title"><span className="pl-result-no" aria-hidden="true">{String(number).padStart(2, '0')}</span>
        <h2>{refName}{several ? ' · 위치 ' + (item.crop + 1) : ''}</h2>
        <span className={'vl-result-pill vl-bundle-outcome ' + outcome.tone}>{outcome.label}</span>
        {item.source !== 'candidate' && <span className="vl-cmp-tag">직접 지정</span>}</div>
    </header>
    <div className="vl-cmp-pair">
      <figure><div className="vl-cmp-frame"><img src={base + '/' + (refInfo?.img || ('refs/' + item.logo + '.png'))} alt={refName + ' 원본'} /></div>
        <figcaption>원본 로고</figcaption></figure>
      <span className="vl-cmp-arrow" aria-hidden="true">→</span>
      <figure><div className="vl-cmp-frame"><img src={base + '/compare/crop/' + item.logo + '/' + item.crop + '.jpg'} alt={'포스터 속 ' + refName} /></div>
        <figcaption>포스터</figcaption></figure>
    </div>
    {differences.length > 0 && <div className="vl-diff-groups vl-cmp-diffs" aria-label="종류별 차이">
      {differences.map(group => <section className="vl-diff-group" key={group.kind}>
        <h3>{group.kind}</h3>
        <ul className="vl-finding-list">{[...new Set(group.items.map(([text]) => findingText(text, '포스터')))].map(text =>
          <li key={text}><span>{text}</span></li>)}</ul>
      </section>)}
    </div>}
    {outcome.tone === 'unknown' && !differences.length && <p className="vl-cmp-note">로고가 작거나 글자를 읽지 못해 비교하지 못했어요. 원본과 직접 비교해 주세요.</p>}
    {item.small && <p className="vl-cmp-note">작은 영역이라 정상 로고도 차이 근거가 나오기 쉬워요. 원본과 직접 비교해 주세요.</p>}
  </article>;
}

// recompare: 위치를 직접 확인하고 다시 대조하는 중인가(대조가 도는 동안에는 서버 응답에 origin이 없어 화면이 알려 준다).
function CompareResults({ base, poster, refs, onConfirm, onStatus, recompare = false }) {
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [active, setActive] = useState(null);
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
  const running = RUNNING.has(result.compare_status);
  const auto = !recompare && result.origin !== 'manual';
  const tally = locations.reduce((counts, item) => {
    const tone = (OUTCOME[item.outcome] || OUTCOME.unknown).tone;
    return { ...counts, [tone]: (counts[tone] || 0) + 1 };
  }, {});
  const boxOf = item => item.box || (item.candidate != null ? poster.candidates[item.candidate]?.rep[item.logo] : null);
  const jump = number => document.getElementById('pl-result-' + number)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  return <div className="vl-main-stack vl-compare">
    <div className="vl-stage-heading vl-compare-heading">
      <div><h1>로고 정밀 대조</h1>
        <p>{auto ? '' : '직접 확인한 위치로 다시 대조했어요. '}차이 근거가 있으면 보류, 없으면 통과예요. 최종 판정은 담당자가 합니다.</p>
        {locations.length > 0 && <div className="vl-compare-tally" aria-label="정밀 대조 결과 요약">
          <span className="vl-compare-total">결과 {locations.length}건</span>
          {['match', 'diff', 'unknown'].filter(tone => tally[tone])
            .map(tone => <span key={tone} className={'vl-result-pill ' + tone}>{OUTCOME[tone].label} {tally[tone]}</span>)}</div>}</div>
      <button type="button" className="vl-button secondary" onClick={onConfirm}>위치 직접 확인</button>
    </div>
    {running && <CompareProgress status={result.compare_status} progress={result.compare_progress} base={base} refs={refs} auto={auto} />}
    {result.compare_status === 'failed' && <section className="vl-panel"><ErrorMessage message={result.compare_error || '정밀 대조가 완료되지 않았습니다.'} /></section>}
    {!result.compare_status && <CompareProgress status="starting" progress={0} base={base} refs={refs} auto={auto} />}
    {(result.warnings || []).map(warning => <div className="vl-warning" key={warning}>⚠ {warning}</div>)}
    {result.compare_status === 'completed' && !locations.length && !absent.length && <section className="vl-panel vl-empty">
      <strong>대조 결과가 없습니다.</strong><p>포스터에서 원본 로고 후보를 찾지 못했어요. 위치를 직접 지정해 다시 대조할 수 있어요.</p></section>}
    {!running && result.compare_status && (locations.length > 0 || absent.length > 0) && <div className="pl-result-grid">
      {/* 왼쪽: 포스터 위에 대조한 위치를 결과 색 번호로 표시한다. 번호를 누르면 오른쪽 카드로 간다. */}
      <section className="pl-stage pl-result-stage" aria-label="포스터 위 대조 위치">
        <div className="vl-frame pl-poster" style={posterFit(poster.size, 68)}>
          <img src={poster.url} alt="검수 포스터" />
          {locations.map((item, index) => {
            const box = boxOf(item); if (!box) return null;
            const outcome = OUTCOME[item.outcome] || OUTCOME.unknown;
            const name = refs[item.logo]?.name || item.reference;
            return <button type="button" key={item.logo + '-' + item.crop}
              className={'pl-mark is-' + outcome.tone + (active === index + 1 ? ' is-active' : '') + (box[1] / (poster.size[1] || 1) < 0.06 ? ' label-below' : '')}
              style={boxStyle(box, poster.size)} aria-label={name + ' · ' + outcome.label + ' · 결과 카드로 이동'} title={name + ' · ' + outcome.label}
              onClick={() => jump(index + 1)} onMouseEnter={() => setActive(index + 1)} onMouseLeave={() => setActive(null)}
              onFocus={() => setActive(index + 1)} onBlur={() => setActive(null)}><span>{String(index + 1).padStart(2, '0')}</span></button>;
          })}
        </div>
        <div className="pl-stage-scale"><span>대조 위치 {locations.length}곳</span><span>번호를 누르면 해당 결과로 이동해요</span>
          <span className="pl-stage-legend"><i className="match" />통과<i className="diff" />보류</span></div>
      </section>
      <div className="pl-result-list">
        {locations.map((item, index) => <LocationEvidence key={item.logo + '-' + item.crop} base={base} item={item} number={index + 1}
          refInfo={refs[item.logo]} several={locations.filter(other => other.logo === item.logo).length > 1}
          active={active === index + 1} onActive={setActive} />)}
        {absent.length > 0 && <section className="vl-panel pl-absent">
          <div className="vl-section-heading"><h2>포스터에 없음으로 표시한 원본 로고</h2><span className="vl-count">{absent.length}</span></div>
          <ul className="pl-absent-list">{absent.map(item => <li key={item.logo}>
            <img src={base + '/' + (refs[item.logo]?.img || ('refs/' + item.logo + '.png'))} alt="" />{refs[item.logo]?.name || item.reference}</li>)}</ul>
        </section>}
      </div>
    </div>}
  </div>;
}

export default function PosterLogoPage() {
  const go = useNavigate();
  const [params, setParams] = useSearchParams();
  const job = params.get('job');
  // 동영상 로고와 같이 기본 화면은 자동 대조 결과(compare)이고, 위치 직접 확인(confirm)은 검수하려던 로고가 아닐 때만 연다(2026-11-17).
  const view = params.get('view') === 'confirm' ? 'confirm' : 'compare';
  const base = POSTER_LOGO_API + '/' + encodeURIComponent(job || '');
  const storageKey = 'poster-logo-review:v1:' + (job || 'new');
  // 이 탭에서 '정밀 대조 재검수'를 누른 작업인가. 대조가 도는 동안 새로고침해도 재검수 화면을 그대로 보이려고 탭에 적어 둔다.
  const recompareKey = 'poster-logo-recompare:' + (job || 'new');
  const [meta, setMeta] = useState(null);
  const [poster, setPoster] = useState(null);
  const [error, setError] = useState('');
  const [refIndex, setRefIndex] = useState(0);
  const [answers, setAnswers] = useState({});
  const [drafts, setDrafts] = useState({});
  const [selection, setSelection] = useState(EMPTY_SELECTION);
  const [mode, setMode] = useState('candidates');
  const [busy, setBusy] = useState(false);
  const [recompare, setRecompare] = useState(false);
  const active = useRef(null);
  useWorkspaceReset(() => active.current?.abort());
  useEffect(() => () => active.current?.abort(), []);

  useEffect(() => {
    if (!job) return undefined;
    const controller = new AbortController(); let timer;
    setMeta(null); setPoster(null); setError(''); setRefIndex(0);
    try { setRecompare(sessionStorage.getItem(recompareKey) === '1'); } catch { setRecompare(false); }
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
  }, [job, base, storageKey, recompareKey]);

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
  // 자동 진행의 진행 화면은 하나다(2026-11-18): 후보를 찾은 뒤에도 자동 정밀 대조가 끝날 때까지 같은 화면(LogoJobProgress)이 이어진다.
  // 위치를 직접 확인하고 다시 대조하는 재검수는 정밀 대조 화면(CompareResults의 CompareProgress)이 보여 준다.
  // 위치 직접 확인 화면을 열어 둔 동안에는 그 화면을 그대로 보인다.
  const compareRunning = meta?.id === job && meta.status === 'candidates_ready' && comparing;
  const autoComparing = compareRunning && !recompare && view !== 'confirm';
  const showProgress = meta?.id === job && (RUNNING.has(meta.status) || meta.status === 'failed' || autoComparing);

  // 재검수가 끝나면(완료 · 실패) 재검수 표시를 지운다.
  useEffect(() => {
    if (!recompare || meta?.id !== job || comparing) return;
    setRecompare(false);
    try { sessionStorage.removeItem(recompareKey); } catch { /* 탭 저장소를 쓸 수 없으면 화면 안에서만 기억한다 */ }
  }, [recompare, comparing, meta?.id, job, recompareKey]);

  // 자동 정밀 대조가 도는 동안 작업 상태를 다시 읽어 진행 화면의 02 단계에 진행률을 보인다. 끝나면 결과 화면이 근거를 읽는다.
  // 재검수 중에는 정밀 대조 화면이 직접 읽는다.
  useEffect(() => {
    if (!compareRunning || recompare) return undefined;
    const controller = new AbortController(); let timer;
    async function poll() {
      try {
        const value = await fetchJson(base, { signal: controller.signal });
        setMeta(value); setError('');
        if (RUNNING.has(value.compare_status)) timer = setTimeout(poll, 2000);
      } catch (failure) {
        if (failure.name === 'AbortError') return;
        setError(failure.message); timer = setTimeout(poll, 4000);
      }
    }
    timer = setTimeout(poll, 1000);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [base, compareRunning, recompare]);
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

  // 이 브라우저에 저장한 선택이 없으면 서버가 자동 정밀 대조에 쓴 후보(auto_selection)를 확정 화면의 선택으로 채운다.
  // 자동 대조를 하지 않는 서버가 만든 작업은 auto_selection이 없으므로, 화면이 같은 규칙(상위 후보·LINK_RATIO)으로 채운다.
  useEffect(() => {
    if (!poster || meta?.id !== job || Object.keys(answers).length || Object.keys(drafts).length) return;
    const picks = meta.auto_selection || refs.map((_, logo) => {
      const order = rankedCandidates(poster.candidates, logo);
      if (!order.length) return { logo, decision: 'none', candidates: [] };
      const top = poster.candidates[order[0]].rank[logo];
      const floor = top > 0 ? LINK_RATIO * top : Infinity;
      return { logo, decision: 'done', candidates: [order[0], ...order.slice(1).filter(i => poster.candidates[i].rank[logo] >= floor)].slice(0, TOP_N) };
    });
    const nextAnswers = {}, nextDrafts = {};
    for (const pick of picks) {
      if (!refs[pick.logo]) continue;
      const value = { decision: pick.decision === 'none' ? 'none' : null, checked: pick.candidates || [], manuals: [], auto: true };
      const answer = answerFor(pick.logo, value);
      if (!answer) continue;
      nextDrafts[pick.logo] = value;
      nextAnswers[pick.logo] = { ...answer, selection: value };
    }
    setAnswers(nextAnswers); setDrafts(nextDrafts);
    // 로고를 바꿀 때 선택을 읽는 효과는 같은 커밋에서 아직 빈 drafts를 보므로, 지금 보는 로고의 선택은 여기서 바로 채운다.
    setSelection(nextDrafts[refIndex] || EMPTY_SELECTION);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [poster, meta?.id, meta?.auto_selection, job]);

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
    if (next === 'confirm') query.set('view', 'confirm'); else query.delete('view');
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
      setMeta(previous => previous && previous.id === job ? { ...previous, compare_status: 'queued', compare_progress: 0 } : previous);
      setRecompare(true);
      try { sessionStorage.setItem(recompareKey, '1'); } catch { /* 탭 저장소를 쓸 수 없으면 화면 안에서만 기억한다 */ }
      changeView('compare');
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message);
    } finally {
      if (!controller.signal.aborted) { active.current = null; setBusy(false); }
    }
  }

  // 종합 검수는 포스터 검수 실행에 연결된 작업이라 첫 단계가 그 실행으로, 로고만 검수는 자료 입력으로 돌아간다.
  const linkedRun = meta?.run_id;
  const back = linkedRun ? '/review/new?framework=' + encodeURIComponent(linkedRun) : '/review/new';
  // 동영상 로고와 같은 단계: 자료 입력 → 후보 찾기 → 정밀 대조. 위치 직접 확인은 그 화면을 열었을 때만 단계로 보인다.
  const baseSteps = linkedRun ? { review: '포스터 검수', candidates: posterLogoSteps.candidates, compare: posterLogoSteps.compare } : posterLogoSteps;
  const steps = ready && view === 'confirm' ? { ...baseSteps, confirm: '위치 직접 확인' } : baseSteps;
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
      navigate={key => { if (key === 'input' || key === 'review') go(back); else if (key === 'confirm' || key === 'compare') changeView(key); }} />
    <main id="review-content" className="vl-page pl-page" tabIndex={-1}>
      {!job ? <section className="vl-empty-state">
        <div className="vl-empty-mark">01</div>
        <span className="vl-kicker">자료 입력에서 시작</span>
        <h1>자료 입력에서 로고 검수를 시작하세요</h1>
        <p>검수 유형에서 ‘로고 검수’ 또는 ‘종합 검수’를 고르고 포스터와 원본 로고를 첨부하면 포스터에서 로고 후보를 찾습니다.</p>
        <Link className="vl-button primary" to="/review/new">자료 입력 열기</Link>
      </section> : <div className="vl-content">
        {/* 작업 이름은 위쪽 프로젝트 줄에 이미 있어서, 같은 이름을 크게 한 번 더 적던 작업 줄(파일 이름·픽셀 크기·후보 수)은 뺐다. */}
        {showProgress && <LogoJobProgress media="poster" base={base} meta={meta}
          linkedReview={linkedRun} onRestart={() => go(back)} />}
        {meta?.id === job && meta.status === 'failed' && linkedRun && <p className="vl-help">
          포스터 검수는 그대로 진행됩니다. 로고 워커 연결을 확인한 뒤 포스터를 다시 등록하면 새로 접수합니다.</p>}
        {error && <section className="vl-panel"><ErrorMessage message={error} /></section>}

        {ready && !autoComparing && <>
          {view === 'confirm' ? <div className="vl-layout">
            <aside className="vl-logo-sidebar">
              <div className="vl-sidebar-title"><span className="vl-kicker">기준 로고</span><strong>{refs.length}개</strong></div>
              <div className="vl-logo-list">{refs.map((item, index) => <button type="button" key={index}
                className={'vl-logo-row' + (index === refIndex ? ' active' : '')} onClick={() => changeReference(index)}
                aria-current={index === refIndex ? 'step' : undefined}>
                <img src={base + '/' + item.img} alt="" />
                <span><strong>{item.name}</strong><small>{isSaved(index)
                  ? (answers[index].decision === 'none' ? '포스터에 없음' : '위치 확정') + (answers[index].selection?.auto ? ' · 자동 선택' : ' · 저장됨')
                  : selectionOf(index).decision || selectionOf(index).checked.length || selectionOf(index).manuals.length ? '저장 필요' : '확인 필요'}</small></span>
                <i>{isSaved(index) ? '✓' : index + 1}</i>
              </button>)}</div>
              <p className="vl-sidebar-note">원본 로고마다 위치를 확인하고 저장하세요. 저장한 선택은 이 브라우저에 보관됩니다.</p>
            </aside>

            <section className="vl-workspace">
              {refInfo && <div className="vl-current-ref">
                <img src={base + '/' + refInfo.img} alt={refInfo.name + ' 원본'} />
                <div><span className="vl-current-label">{refIndex + 1} / {refs.length}</span>
                  <span className="vl-kicker">원본 로고</span><h1>{refInfo.name}</h1>
                  <p>{refInfo.bonus_basis === 'openai' ? '확신도가 높은 위치부터 보여 줍니다.'
                    : refInfo.bonus_basis === 'input' ? '기준 문구: ' + refInfo.bonus_text
                    : refInfo.bonus_basis === 'ocr' ? '기준 문구가 없어 원본 로고에서 읽은 글자(' + refInfo.bonus_text + ')를 함께 봤어요.'
                      : '기준 문구가 없어 모양만으로 순위를 매겼어요.'}</p></div>
              </div>}
              {mode === 'manual' ? <ManualEditor poster={poster} onCancel={() => setMode('candidates')} onDone={addManual} />
                : <CandidateWorkspace poster={poster} refIndex={refIndex} checked={checked} manuals={selection.manuals}
                  decision={decision} onToggle={toggle} onManual={() => setMode('manual')} onNone={markNone} onRemoveManual={removeManual} />}
              {decision && mode !== 'manual' && <section className="vl-savebar">
                <div><strong>{decision === 'none' ? '포스터에 이 원본 로고가 없는 것으로 표시했습니다.'
                  : saved ? '선택한 위치 ' + (selection.checked.length + selection.manuals.length) + '곳을 저장했어요.'
                    : '선택한 위치 ' + (selection.checked.length + selection.manuals.length) + '곳을 저장해 주세요.'}</strong>
                  <span>{decision === 'none' ? '정밀 대조 결과에 사람 확인 기록으로 남습니다.' : '정밀 대조 재검수는 저장한 위치로 다시 대조합니다.'}</span></div>
                <div className="vl-save-actions">
                  <button type="button" className="vl-button subtle" onClick={resetSelection}>다시 선택</button>
                  <button type="button" className="vl-button primary" onClick={saveAnswer} disabled={saved}>{saved ? '저장됨' : '이 원본 로고 확정 저장'}</button>
                  {refIndex + 1 < refs.length && <button type="button" className="vl-button secondary" onClick={() => {
                    const answer = answerFor(refIndex, selection);
                    const nextAnswers = answer ? { ...answers, [refIndex]: { ...answer, selection } } : answers;
                    keep(nextAnswers, { ...drafts, [refIndex]: selection }); setRefIndex(value => value + 1);
                  }}>저장하고 다음 로고</button>}
                  {/* 동영상 로고와 같이 저장한 위치로 정밀 대조를 다시 돌리는 단추를 저장 줄 맨 오른쪽에 둔다. */}
                  <button type="button" className="vl-button primary vl-recompare" onClick={runCompare} disabled={!savedIndexes.length || busy || comparing}
                    title={savedIndexes.length ? '저장한 ' + savedIndexes.length + '개 로고 위치로 정밀 대조를 다시 합니다.' : '저장한 위치가 없어요.'}>
                    {comparing ? '정밀 대조 중' : busy ? '요청 중…' : '정밀 대조 재검수'}
                  </button>
                </div>
              </section>}
            </section>
          </div> : <CompareResults base={base} poster={poster} refs={refs} recompare={recompare} onConfirm={() => changeView('confirm')} onStatus={onCompareStatus} />}
        </>}
      </div>}
    </main>
  </div>;
}
