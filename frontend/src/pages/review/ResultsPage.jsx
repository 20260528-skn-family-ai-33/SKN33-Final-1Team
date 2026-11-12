import React, { useEffect, useMemo, useRef, useState } from 'react';
import DocumentConditionDetails from './DocumentConditionDetails.jsx';
import { Icon } from '../../components/Icon.jsx';
import ImageStage from '../../components/ImageStage.jsx';
import { NoMatchArt } from '../../components/ReviewArt.jsx';
import { statuses } from './reviewConstants.js';
import { filterResults, selectVisibleResult, evidenceOverlays } from './resultSelection.js';
import '../../styles/review-results-v2.css';

// 재인식 후보가 충돌한 경우 처음 읽은 문구와 새 후보를 함께 보여 준다.
function RereadComparison({ evidence = [] }) {
  const conflicts = evidence.filter(item => item.requires_review && item.alternatives?.length);
  if (!conflicts.length) return null;
  return <div className="observed-text" aria-label="재인식 비교"><span>재인식 후보를 원본과 비교해 주세요.</span>
    {conflicts.map(item => <div key={item.ocr_id}><p>처음 읽기: {item.baseline_text || '이전 OCR에서 전체 문구를 읽지 못함'}</p>
      <p>다시 읽기: {[...new Set(item.alternatives.map(candidate => `${candidate.engine === 'paddleocr' ? 'PaddleOCR: ' : candidate.engine === 'qwen3-vl' ? 'Qwen: ' : ''}${candidate.text}`))].join(' / ')}</p></div>)}
  </div>;
}

function ModelAnalysis({ analysis }) {
  if (!analysis) return null;
  if (analysis.status !== 'success') return <p>요구조건을 자동으로 해석하지 못했습니다. 원문을 확인해 주세요.</p>;
  const operations = { required: '필수', forbidden: '금지', optional: '선택', at_least: '이상', at_most: '이하', exactly: '정확히', equals: '일치', contains: '포함' };
  return <div className="observed-text" aria-label="모델 분석"><span>요구조건 분석 · 이미지 분류 참고</span>
    {(analysis.conditions || []).map((item, index) => <p key={index}>{item.target || item.value || item.field} · {operations[item.operator] || item.operator}{item.target && item.value != null ? ` · ${item.value}` : ''}</p>)}
    {(analysis.visual_candidates || []).map((item, index) => <p key={`visual-${index}`}>{item.target || item.label}: {item.prediction ? '있음 가능성' : '없음 가능성'}</p>)}
    {analysis.needs_review?.length > 0 && <p>모호한 조건이 있어 원문 확인이 필요합니다.</p>}
    {!analysis.conditions?.length && !analysis.needs_review?.length && <p>추출된 조건이 없습니다. 원문을 확인해 주세요.</p>}
  </div>;
}

const EMPTY = [];

function StatusLabel({ status }) {
  return <span className={`rv-result-status ${status || 'review'}`}>
    <span aria-hidden="true" />{statuses[status] || '확인 필요'}
  </span>;
}

function RetryNote({ retry }) {
  if (!retry) return null;
  const text = retry.status === 'success'
    ? `조건별 영역 재인식 ${retry.attempts?.length || 0}회 완료${retry.skipped?.length ? ' · 일부 모델 미준비' : ''} · 새로 읽은 후보는 원본과 비교해 주세요.`
    : retry.status === 'failed' ? '영역 재인식을 완료하지 못해 기존 OCR 근거를 유지했습니다.'
      : retry.skipped?.length ? '일부 재인식 모델이 준비되지 않아 기존 OCR 근거를 유지했습니다.'
        : '기존 OCR 근거로 확인 가능한 조건을 비교했습니다.';
  return <p className="rv-result-runtime-note">{text}</p>;
}

// The list, canvas and inspector always share one criterion ID.
// evidence(선택): 항목을 누르면 열 판정 근거 창을 그리는 함수. 주면 항목을 누를 때마다 창이 열리고, 없으면 예전처럼 펼치고 접는다.
export default function ResultsPage({ review, filter, setFilter, activeEvidence, setActiveEvidence, edit, newReview,
  busy = false, exportData = review, actions, revise, finalApproved = false, exceptionApproved = false, header = null, imageActions,
  evidence }) {
  const results = review.results || EMPTY;
  const [query, setQuery] = useState('');
  const [selectedOnly, setSelectedOnly] = useState(false);
  const [closedId, setClosedId] = useState(null);
  const [evidenceId, setEvidenceId] = useState(null);
  const list = useRef(null);
  const canvas = useRef(null);
  const focusRow = useRef(false);
  const counts = Object.fromEntries(Object.keys(statuses).map(status => [status, results.filter(row => row.status === status).length]));
  const visible = useMemo(() => filterResults(results, filter, query), [results, filter, query]);
  const selected = selectVisibleResult(visible, activeEvidence?.criterion_id);
  const expanded = selected && selected.criterion_id !== closedId;
  const allOverlays = useMemo(() => {
    const numbers = new Map(results.map((result, index) => [result.criterion_id, String(index + 1).padStart(2, '0')]));
    return evidenceOverlays(results).map(overlay => ({ ...overlay, label: numbers.get(overlay.id) }));
  }, [results]);
  const selectedOverlays = selected ? allOverlays.filter(item => item.id === selected.criterion_id) : EMPTY;
  const selectedEvidence = selected?.evidence || EMPTY;
  const image = review.image;
  const selectedCondition = selected && review.criteria?.find(item => item.id === selected.criterion_id)?.document_condition;
  const selectedIndex = visible.findIndex(item => item.criterion_id === selected?.criterion_id);

  useEffect(() => {
    if (!focusRow.current) return;
    focusRow.current = false;
    list.current?.querySelector('.rv-result-item-button.is-selected')?.focus();
  }, [selected, closedId]);

  // A hidden or removed result must not leave stale evidence on the canvas.
  useEffect(() => {
    if (!selected) {
      if (activeEvidence) setActiveEvidence(null);
      return;
    }
    const evidence = selected.evidence || EMPTY;
    if (!activeEvidence || activeEvidence.criterion_id !== selected.criterion_id || activeEvidence.evidence !== evidence) {
      setActiveEvidence({ criterion_id: selected.criterion_id, evidence });
    }
  }, [selected, activeEvidence, setActiveEvidence]);

  function chooseResult(result) {
    setClosedId(null);
    setActiveEvidence(result ? { criterion_id: result.criterion_id, evidence: result.evidence || EMPTY } : null);
  }
  const evidenceResult = evidence && evidenceId ? results.find(item => item.criterion_id === evidenceId) : null;
  const evidenceIndex = evidenceResult ? visible.indexOf(evidenceResult) : -1;
  function openEvidence(result) {
    chooseResult(result);
    setEvidenceId(result.criterion_id);
  }
  function closeEvidence() {
    setEvidenceId(null);
    list.current?.querySelector('.rv-result-item-button.is-selected')?.focus();
  }
  function chooseOverlay(id) {
    const result = results.find(item => item.criterion_id === id);
    if (!result) return;
    if (!visible.includes(result)) { setQuery(''); setFilter('all'); }
    if (result !== selected || !expanded) focusRow.current = true;
    else list.current?.querySelector('.rv-result-item-button.is-selected')?.focus();
    chooseResult(result);
  }
  function showImageEvidence() {
    const zoom = canvas.current?.querySelector('.image-stage-focus:enabled');
    if (!zoom) return;
    zoom.click();
    canvas.current.querySelector('.image-stage-viewport')?.focus();
  }
  // 목록에서 ↑·↓·Home·End로 항목을 옮기며 근거를 바로 본다. 초점도 함께 옮겨 키보드만으로 이어서 볼 수 있다.
  function moveWithKeys(event) {
    if (!event.target.closest('.rv-result-item-button')) return;
    const steps = { ArrowDown: 1, ArrowUp: -1, Home: -Infinity, End: Infinity };
    if (!(event.key in steps) || !visible.length || event.altKey || event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    const step = steps[event.key];
    const next = step === -Infinity ? 0 : step === Infinity ? visible.length - 1
      : Math.min(visible.length - 1, Math.max(0, (selectedIndex < 0 ? -1 : selectedIndex) + step));
    chooseResult(visible[next]);
    list.current?.querySelectorAll('.rv-result-item-button')[next]?.focus();
  }
  function changeFilter(value) {
    // The provider clears its old evidence on filter change. Restore the same
    // criterion if it remains visible, otherwise select the first visible row.
    setFilter(value);
    chooseResult(selectVisibleResult(filterResults(results, value, query), selected?.criterion_id));
  }
  function resetFilters() {
    setQuery('');
    setFilter('all');
    chooseResult(selectVisibleResult(results, selected?.criterion_id));
  }
  function exportJson() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `baro-review-${review.id}.json`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const details = selected && <aside id="rv-result-inspector" className={`rv-result-inspector rv-panel is-${selected.status}`}
    aria-labelledby="rv-result-inspector-title">
    <h2 id="rv-result-inspector-title" className="sr-only">선택 항목 상세</h2>
    <div className="rv-result-inspector-body">
      <section className="rv-result-detail-section"><h3>검수 기준</h3><p className="rv-result-selected-criterion">{selected.criterion}</p>
        <DocumentConditionDetails condition={selectedCondition} />
      </section>
      <section className="rv-result-detail-section"><h3>{selected.check_method === 'source_image_metadata' ? '파일에서 확인한 값' : '이미지에서 읽은 문구 / 요소'}</h3>
        <p className={`rv-result-observation ${!selected.observed_text ? 'is-empty' : ''}`}>{selected.observed_text || '기록된 관측 문구가 없습니다. 판정 이유와 이미지 근거를 확인해 주세요.'}</p>
      </section>
      <section className="rv-result-detail-section"><h3>판정 이유</h3><p>{selected.reason || '제공된 판정 이유가 없습니다. 원본과 기준을 직접 확인해 주세요.'}</p></section>
      <section className="rv-result-detail-section"><h3>근거</h3>
        {selectedOverlays.length > 0 && <button type="button" className="rv-button secondary rv-result-evidence-link" onClick={showImageEvidence}>
          <Icon name="search" />이미지 위치 {selectedOverlays.length}곳 보기</button>}
        {selectedEvidence.length ? <ol className="rv-result-evidence-list">{selectedEvidence.map((evidence, index) =>
          <li key={`${evidence.ocr_id || 'evidence'}-${index}`}><span className="rv-result-evidence-index">{String(index + 1).padStart(2, '0')}</span>
            <p>{evidence.text || '문구 없이 위치만 제공된 근거입니다.'}</p></li>)}</ol>
          : <p className="rv-result-muted">연결된 이미지 근거가 없습니다.</p>}
        <RereadComparison evidence={selectedEvidence} />
      </section>
      <ModelAnalysis analysis={selected.model_analysis} />
      <details className="rv-result-runtime"><summary>검수 안내와 처리 내역</summary>
        <p>{finalApproved ? '자동 판정에 대한 담당자의 확인 기록은 보고서에서 볼 수 있습니다.'
          : '자동 판정은 검토를 돕는 결과입니다. ‘확인 필요’ 항목과 최종 승인 여부는 담당자가 검토해 주세요.'}</p>
        {review.model_runtime?.status === 'not_configured' && <p>시각 분석 모델이 연결되지 않아 OCR·파일 정보로 확인 가능한 항목만 자동 판정합니다.</p>}
        <RetryNote retry={review.ocr?.conditional_retry} />
      </details>
    </div>
  </aside>;

  return <section className="rv-results" aria-labelledby="page-title">
    <h1 id="page-title" className="sr-only" tabIndex={-1}>{review.project_name || '포스터 검수'}</h1>
    <p className="rv-result-notice" role="status">{review.status_label || '자동 검수 완료'} · {exceptionApproved
      ? <strong>담당자 예외 승인</strong> : finalApproved ? <strong>담당자 승인 완료</strong> : '최종 승인은 담당자가 별도로 판단합니다.'}</p>
    {header}

    <div className="rv-result-workbench">
      <div className="rv-result-canvas-column" ref={canvas}>
        <ImageStage src={image?.url} alt={`${review.project_name || '검수 대상'} 원본 이미지`}
          title="검수 대상 이미지" subtitle="검수 항목의 위치 근거를 표시합니다."
          headingActions={imageActions}
          width={image?.width} height={image?.height} overlays={selectedOnly ? selectedOverlays : allOverlays}
          selectedOverlayId={selected?.criterion_id} onOverlaySelect={chooseOverlay}
          evidenceScope={{ label: selectedOnly ? '선택 근거 표시' : '모든 근거 표시', available: allOverlays.length > 0,
            toggle: () => setSelectedOnly(value => !value) }}
          empty="검수 대상 이미지를 표시할 수 없습니다." />
        <div className="rv-result-canvas-caption" role="status">
          <Icon name="info" /><p>{!selected ? '검색이나 상태 필터를 바꾸면 검수 항목의 근거를 볼 수 있습니다.'
              : !selectedOverlays.length ? selected.check_method === 'source_image_metadata'
                ? '파일 정보로 확인한 항목입니다. 표시할 이미지 영역은 없습니다.'
                : '이 항목에는 이미지 위치 근거가 제공되지 않았습니다.'
              : `선택한 항목의 위치 근거 ${selectedOverlays.length}곳 · 드래그로 이동 · 0 키로 화면 맞춤`}</p>
        </div>
      </div>

      <aside className="rv-result-rail rv-panel" aria-labelledby="rv-result-list-title">
        <div className="rv-result-rail-heading"><div><h2 id="rv-result-list-title">검수 요구사항</h2>
          <p>{evidence ? '항목을 누르면 판정 근거가 창으로 열립니다.' : '항목을 펼치면 판정 근거가 표시됩니다.'}</p></div><span>{results.length}개 항목</span></div>
        <details className="rv-result-search"><summary>항목 검색{query.trim() ? ` · ${visible.length}개 표시` : ''}</summary>
          <div><Icon name="search" /><input id="rv-result-search" type="search" value={query}
            aria-label="항목 검색" placeholder="기준·문구·판정 이유" onChange={event => setQuery(event.target.value)} /></div>
        </details>
        <div className="rv-result-filters" role="group" aria-label="검수 결과 상태 필터">
          {[['all', '전체', results.length], ['pass', '충족', counts.pass], ['review', '확인 필요', counts.review], ['fail', '위반', counts.fail]]
            .map(([status, label, count]) => <button type="button" className={filter === status ? 'is-active' : ''}
              aria-pressed={filter === status} key={status} onClick={() => changeFilter(status)}>{label}<span>{count}</span></button>)}
        </div>
        <p className="sr-only" role="status">{visible.length}개 표시</p>
        <span id="rv-result-keys" className="sr-only">위아래 화살표로 항목을 옮길 수 있습니다.</span>
        {visible.length ? <ul ref={list} className="rv-result-items" aria-label="검수 항목 목록" aria-describedby="rv-result-keys" onKeyDown={moveWithKeys}>{visible.map(result => {
          const active = selected?.criterion_id === result.criterion_id;
          const open = active && expanded;
          return <li key={result.criterion_id}>
            <button type="button" className={`rv-result-item-button${active ? ' is-selected' : ''}`} aria-pressed={active}
              aria-expanded={Boolean(open)} aria-controls={open ? 'rv-result-inspector' : undefined}
              aria-haspopup={evidence ? 'dialog' : undefined}
              onClick={() => { if (evidence) openEvidence(result); else if (open) setClosedId(result.criterion_id); else chooseResult(result); }}>
              <span className="rv-result-item-number">{String(results.indexOf(result) + 1).padStart(2, '0')}</span>
              <span className="rv-result-item-title">{result.criterion}</span>
              <StatusLabel status={result.status} />{evidence ? <span className="rv-result-item-open">근거 보기</span>
                : <span aria-hidden="true">{open ? '⌃' : '⌄'}</span>}
            </button>
            {open && details}
          </li>;
        })}</ul> : <div className="rv-result-empty"><NoMatchArt /><strong>표시할 항목이 없습니다.</strong>
          <p>{results.length ? '검색어나 상태 필터를 바꿔 주세요.' : '기준과 검수 내용을 다시 확인해 주세요.'}</p>
          {results.length > 0 && <button type="button" className="rv-button secondary" onClick={resetFilters}>검색·필터 초기화</button>}
        </div>}
      </aside>

    </div>
    <footer className="rv-result-footer">
      <div className="rv-result-secondary-actions">
        {edit && <button type="button" className="text-btn" disabled={busy} onClick={edit}>기준 다시 확인하기</button>}
        <button type="button" className="text-btn" onClick={exportJson}>결과 JSON 저장</button>
      </div>
      <div>{revise && <button type="button" className="rv-button secondary" disabled={busy} onClick={revise}>수정본 재검수</button>}
        <button type="button" className={`rv-button ${actions ? 'secondary' : 'primary'}`} disabled={busy} onClick={newReview}>새 검수 시작하기{!actions && <Icon name="arrow" />}</button>{actions}</div>
    </footer>
    {evidenceResult && evidence({
      criterionId: evidenceResult.criterion_id, number: String(results.indexOf(evidenceResult) + 1).padStart(2, '0'),
      position: evidenceIndex >= 0 ? { index: evidenceIndex + 1, total: visible.length } : null,
      onPrev: evidenceIndex > 0 ? () => openEvidence(visible[evidenceIndex - 1]) : undefined,
      onNext: evidenceIndex >= 0 && evidenceIndex < visible.length - 1 ? () => openEvidence(visible[evidenceIndex + 1]) : undefined,
      onClose: closeEvidence,
    })}
  </section>;
}
