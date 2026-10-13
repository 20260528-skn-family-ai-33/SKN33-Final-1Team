import React from 'react';
import { Icon } from '../../components/Icon.jsx';
import { Heading } from '../../components/Heading.jsx';
import { statuses } from '../../reviewConstants.js';
import { evidenceBoxStyle } from '../../workflow.js';

// 재인식 후보가 충돌한 경우 처음 읽은 문구와 새 후보를 함께 보여 준다.
function RereadComparison({ evidence = [] }) {
  const conflicts = evidence.filter(item => item.requires_review && item.alternatives?.length);
  if (!conflicts.length) return null;
  return <div className="observed-text" aria-label="재인식 비교"><span>재인식 후보를 원본과 비교해 주세요.</span>
    {conflicts.map(item => <div key={item.ocr_id}><p>처음 읽기: {item.baseline_text}</p>
      <p>다시 읽기: {[...new Set(item.alternatives.map(candidate => `${candidate.engine === 'paddleocr' ? 'PaddleOCR: ' : candidate.engine === 'qwen3-vl' ? 'Qwen: ' : ''}${candidate.text}`))].join(' / ')}</p></div>)}
  </div>;
}

// 항목별 판정과 원본 좌표에 해당하는 이미지 근거를 함께 표시한다.
export default function ResultsPage({ review, filter, setFilter, activeEvidence, setActiveEvidence, edit, newReview }) {
  const results = review.results || [];
  const counts = Object.fromEntries(Object.keys(statuses).map(status => [status, results.filter(row => row.status === status).length]));
  const visible = results.filter(row => filter === 'all' || row.status === filter);
  const image = review.image;
  // 현재 검수 결과를 내려받고 임시 다운로드 URL을 해제한다.
  function exportJson() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(review, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `baro-review-${review.id}.json`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <section aria-labelledby="page-title"><Heading step={4} title="항목별 검수 결과를 확인하세요.">{review.project_name || '새로운 검수'} · 기준 v{review.criteria_version} · 총 {results.length}개 항목</Heading><div className="result-alert"><Icon name={counts.fail || counts.review ? 'warning' : 'check'} /><div><strong>{counts.fail || counts.review ? '담당자 확인이 필요한 항목을 살펴보세요.' : '선택한 정보 기준의 근거를 확인했습니다.'}</strong><p>OCR에서 읽은 문구를 기준과 비교한 결과입니다. ‘확인 필요’ 항목과 최종 승인 여부는 담당자가 검토해 주세요.</p></div></div><div className="result-metrics">{Object.entries(statuses).map(([status, label]) => <div className={`metric ${status}`} key={status}><span>{label}</span><strong>{counts[status]}</strong></div>)}</div><div className="result-grid"><div className="form-card result-image-card"><div className="card-heading"><h2>검수 대상 이미지</h2><span className="pill">기준 v{review.criteria_version}</span></div><div className="result-image-wrapper"><div className="result-canvas"><img src={image?.url} alt="검수 대상 이미지와 선택한 근거 영역" />{activeEvidence?.evidence.map((evidence, i) => { const style = evidenceBoxStyle(evidence.bbox_xyxy, image?.width, image?.height); return style ? <div className="evidence-box" style={style} key={i} title={evidence.text} aria-label={`근거 영역: ${evidence.text}`} /> : null; })}</div></div><div className="result-caption" aria-live="polite">{activeEvidence ? <><strong>선택한 근거</strong><br />{activeEvidence.evidence.map(item => item.text).filter(Boolean).join(' · ')}<button className="text-btn clear-evidence" onClick={() => setActiveEvidence(null)}>표시 해제</button></> : '항목의 ‘위치 보기’를 누르면 이미지에서 해당 영역을 확인할 수 있어요.'}</div></div><div className="form-card"><div className="result-filter" role="group" aria-label="검수 결과 필터">{[['all', '전체', results.length], ['fail', '위반', counts.fail], ['review', '확인 필요', counts.review], ['pass', '충족', counts.pass]].map(([status, label, count]) => <button className={`filter-button ${filter === status ? 'active' : ''}`} aria-pressed={filter === status} key={status} onClick={() => setFilter(status)}>{label}<span>{count}</span></button>)}</div><div className="results-list">{visible.length ? visible.map((result, i) => <article className={`result-item ${activeEvidence?.criterion_id === result.criterion_id ? 'selected-evidence' : ''}`} key={result.criterion_id || i}><div className="result-item-top"><span className={`status-badge ${result.status}`}><Icon name={result.status === 'pass' ? 'check' : 'warning'} />{statuses[result.status] || '확인 필요'}</span><span className="result-category">{result.type === 'visual' ? '시각 검수' : '정보 검수'}</span></div><h3>{result.criterion}</h3><p className="result-reason">{result.reason}</p>{result.observed_text && <div className="observed-text"><span>이미지에서 읽은 문구</span><p>{result.observed_text}</p></div>}<RereadComparison evidence={result.evidence} />{result.evidence?.length > 0 && <div className="evidence-actions"><button className="text-btn" onClick={() => setActiveEvidence({ criterion_id: result.criterion_id, evidence: result.evidence })}><Icon name="search" />위치 보기 · {result.evidence.length}곳</button></div>}</article>) : <div className="empty-results"><Icon name="check" /><p>이 상태에 해당하는 항목이 없습니다.</p></div>}</div></div></div><div className="result-bottom"><p>검수 실행 완료는 최종 승인을 의미하지 않습니다.</p><div className="result-actions"><button className="btn secondary" onClick={exportJson}>결과 JSON 저장</button><button className="btn secondary" onClick={edit}>기준 다시 확인하기</button><button className="btn primary" onClick={newReview}>새 검수 시작하기</button></div></div></section>;
}
