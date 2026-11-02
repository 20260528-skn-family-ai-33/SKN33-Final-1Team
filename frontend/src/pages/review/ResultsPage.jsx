import React, { useEffect, useMemo, useRef, useState } from 'react';
import DocumentConditionDetails from './DocumentConditionDetails.jsx';
import { Icon } from '../../components/Icon.jsx';
import ImageStage from '../../components/ImageStage.jsx';
import { statuses } from '../../reviewConstants.js';
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
export default function ResultsPage({ review, filter, setFilter, activeEvidence, setActiveEvidence, edit, newReview }) {
  const results = review.results || EMPTY;
  const [query, setQuery] = useState('');
  const list = useRef(null);
  const inspector = useRef(null);
  const counts = Object.fromEntries(Object.keys(statuses).map(status => [status, results.filter(row => row.status === status).length]));
  const visible = useMemo(() => filterResults(results, filter, query), [results, filter, query]);
  const selected = selectVisibleResult(visible, activeEvidence?.criterion_id);
  const overlays = useMemo(() => evidenceOverlays(visible), [visible]);
  const selectedOverlays = overlays.filter(item => item.id === selected?.criterion_id);
  const selectedEvidence = selected?.evidence || EMPTY;
  const image = review.image;
  const selectedCondition = selected && review.criteria?.find(item => item.id === selected.criterion_id)?.document_condition;
  const selectedIndex = visible.findIndex(item => item.criterion_id === selected?.criterion_id);

  useEffect(() => {
    const button = list.current?.querySelector('[aria-pressed="true"]');
    if (button) button.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    if (inspector.current) inspector.current.scrollTop = 0;
  }, [selected?.criterion_id]);

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
    setActiveEvidence(result ? { criterion_id: result.criterion_id, evidence: result.evidence || EMPTY } : null);
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
  function selectOverlay(id) {
    const result = visible.find(item => item.criterion_id === id);
    if (result) chooseResult(result);
  }
  function exportJson() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(review, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `baro-review-${review.id}.json`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return <section className="rv-results" aria-labelledby="page-title">
    <header className="rv-result-header">
      <div><p className="rv-eyebrow">REVIEW RESULTS · 기준 v{review.criteria_version}</p>
        <h1 id="page-title" tabIndex={-1}>{review.project_name || '새로운 검수'}</h1>
        <p className="rv-result-header-note">기준과 이미지 근거를 나란히 확인하세요.</p></div>
      <button type="button" className="rv-button secondary" onClick={exportJson}><Icon name="file" />결과 JSON 저장</button>
    </header>

    <div className="rv-result-summary" aria-label="자동 검수 결과 요약">
      <span className="rv-result-completed"><Icon name="check" />자동 검수 완료</span>
      <span className="rv-result-total">전체 <strong>{results.length}</strong></span>
      {Object.entries(statuses).map(([status, label]) => <span className={`rv-result-summary-count ${status}`} key={status}>
        <span className="rv-result-dot" aria-hidden="true" />{label}<strong>{counts[status]}</strong>
      </span>)}
      <span className="rv-result-summary-notice">최종 승인은 담당자가 별도로 판단합니다.</span>
    </div>

    <div className="rv-result-workbench">
      <aside className="rv-result-rail rv-panel" aria-labelledby="rv-result-list-title">
        <div className="rv-result-rail-heading"><h2 id="rv-result-list-title">검수 항목</h2><span>{results.length}</span></div>
        <div className="rv-result-search">
          <label htmlFor="rv-result-search">항목 검색</label>
          <div><Icon name="search" /><input id="rv-result-search" type="search" value={query}
            placeholder="기준·문구·판정 이유" onChange={event => setQuery(event.target.value)} /></div>
        </div>
        <div className="rv-result-filters" role="group" aria-label="검수 결과 상태 필터">
          {[['all', '전체', results.length], ['fail', '위반', counts.fail], ['review', '확인 필요', counts.review], ['pass', '충족', counts.pass]]
            .map(([status, label, count]) => <button type="button" className={filter === status ? 'is-active' : ''}
              aria-pressed={filter === status} key={status} onClick={() => changeFilter(status)}>{label}<span>{count}</span></button>)}
        </div>
        <p className="rv-result-list-count" role="status">{visible.length}개 표시{query.trim() ? ' · 검색 적용' : ''}</p>
        {visible.length ? <ul ref={list} className="rv-result-items" aria-label="검수 항목 목록">{visible.map(result =>
          <li key={result.criterion_id}><button type="button" className={`rv-result-item-button ${selected?.criterion_id === result.criterion_id ? 'is-selected' : ''}`}
            aria-pressed={selected?.criterion_id === result.criterion_id} aria-controls="rv-result-inspector" onClick={() => chooseResult(result)}>
            <span className="rv-result-item-meta"><StatusLabel status={result.status} /><span>{String(results.indexOf(result) + 1).padStart(2, '0')}</span></span>
            <span className="rv-result-item-title">{result.criterion}</span>
            <span className="rv-result-item-subtitle">{result.type === 'visual' ? '시각 검수' : '정보 검수'}<span>근거 {result.evidence?.length || 0}개</span></span>
          </button></li>)}</ul> : <div className="rv-result-empty rv-result-empty-list">
          <Icon name="search" /><strong>{results.length ? '일치하는 항목이 없습니다.' : '검수 항목이 없습니다.'}</strong>
          <p>{results.length ? '검색어나 상태 필터를 바꿔 주세요.' : '기준과 검수 내용을 다시 확인해 주세요.'}</p>
          {results.length > 0 && <button type="button" className="rv-button secondary" onClick={resetFilters}>검색·필터 초기화</button>}
        </div>}
      </aside>

      <div className="rv-result-canvas-column">
        <ImageStage src={image?.url} alt={`${review.project_name || '검수 대상'} 원본 이미지`}
          title="검수 대상 이미지" subtitle="영역을 선택하면 해당 검수 항목으로 이동합니다."
          width={image?.width} height={image?.height} overlays={overlays}
          selectedOverlayId={selected?.criterion_id} onOverlaySelect={selectOverlay}
          empty="검수 대상 이미지를 표시할 수 없습니다." />
          <div className="rv-result-canvas-caption" role="status">
            <Icon name="info" /><p>{!selected ? '검색이나 상태 필터를 바꾸면 검수 항목의 근거를 볼 수 있습니다.'
                : !selectedOverlays.length ? selected.check_method === 'source_image_metadata'
                  ? '파일 정보로 확인한 항목입니다. 표시할 이미지 영역은 없습니다.'
                  : '이 항목에는 이미지 위치 근거가 제공되지 않았습니다.'
                : `선택한 항목의 위치 근거 ${selectedOverlays.length}곳 · 드래그로 이동 · 0 키로 화면 맞춤`}</p>
          </div>
      </div>

      <aside id="rv-result-inspector" className="rv-result-inspector rv-panel" aria-labelledby="rv-result-inspector-title">
        <div className="rv-result-inspector-heading"><div><p className="rv-eyebrow">INSPECTOR</p><h2 id="rv-result-inspector-title">선택 항목 상세</h2></div>
          <div className="rv-result-item-navigation"><span>{selectedIndex < 0 ? '0' : String(selectedIndex + 1).padStart(2, '0')} / {visible.length}</span>
            <button type="button" aria-label="이전 검수 항목" disabled={selectedIndex <= 0} onClick={() => chooseResult(visible[selectedIndex - 1])}>←</button>
            <button type="button" aria-label="다음 검수 항목" disabled={selectedIndex < 0 || selectedIndex >= visible.length - 1} onClick={() => chooseResult(visible[selectedIndex + 1])}>→</button>
          </div>
        </div>
        {selected ? <div ref={inspector} className="rv-result-inspector-body">
          <div className="rv-result-verdict"><StatusLabel status={selected.status} /><span>{selected.type === 'visual' ? '시각 검수' : '정보 검수'}</span></div>
          <section className="rv-result-detail-section"><h3>검수 기준</h3><p className="rv-result-selected-criterion">{selected.criterion}</p>
            <DocumentConditionDetails condition={selectedCondition} />
          </section>
          <section className="rv-result-detail-section"><h3>{selected.check_method === 'source_image_metadata' ? '파일에서 확인한 값' : '이미지에서 읽은 문구'}</h3>
            <p className={`rv-result-observation ${!selected.observed_text ? 'is-empty' : ''}`}>{selected.observed_text || '기록된 관측 문구가 없습니다. 판정 이유와 이미지 근거를 확인해 주세요.'}</p>
          </section>
          <section className="rv-result-detail-section"><h3>판정 이유</h3><p>{selected.reason || '제공된 판정 이유가 없습니다. 원본과 기준을 직접 확인해 주세요.'}</p></section>
          <section className="rv-result-detail-section"><h3>근거 <span>{selectedEvidence.length}</span></h3>
            {selectedEvidence.length ? <ol className="rv-result-evidence-list">{selectedEvidence.map((evidence, index) =>
              <li key={`${evidence.ocr_id || 'evidence'}-${index}`}><span className="rv-result-evidence-index">{String(index + 1).padStart(2, '0')}</span>
                <p>{evidence.text || '문구 없이 위치만 제공된 근거입니다.'}</p></li>)}</ol>
              : <p className="rv-result-muted">연결된 이미지 근거가 없습니다.</p>}
            <RereadComparison evidence={selectedEvidence} />
          </section>
          <ModelAnalysis analysis={selected.model_analysis} />
          <details className="rv-result-runtime"><summary>검수 안내와 처리 내역</summary>
            <p>자동 판정은 검토를 돕는 결과입니다. ‘확인 필요’ 항목과 최종 승인 여부는 담당자가 검토해 주세요.</p>
            {review.model_runtime?.status === 'not_configured' && <p>시각 분석 모델이 연결되지 않아 OCR·파일 정보로 확인 가능한 항목만 자동 판정합니다.</p>}
            <RetryNote retry={review.ocr?.conditional_retry} />
          </details>
        </div> : <div className="rv-result-empty"><Icon name="list" /><strong>표시할 항목이 없습니다.</strong><p>목록에 표시되는 항목을 선택하면 기준·관측·이유·근거를 확인할 수 있습니다.</p></div>}
      </aside>
    </div>
    <footer className="rv-result-footer"><p><Icon name="shield" />검수 실행 완료는 최종 승인을 의미하지 않습니다.</p>
      <div><button type="button" className="rv-button secondary" onClick={edit}>기준 다시 확인하기</button>
        <button type="button" className="rv-button primary" onClick={newReview}>새 검수 시작하기<Icon name="arrow" /></button></div>
    </footer>
  </section>;
}
