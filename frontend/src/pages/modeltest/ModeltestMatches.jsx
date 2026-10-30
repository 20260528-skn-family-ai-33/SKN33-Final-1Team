import React, { useEffect, useRef, useState } from 'react';
import { containedPoint } from './modeltestResults.js';
import { FILTER_SCOPES } from './modeltest.js';

export function MatchedImages({ match, sizes, children }) {
  const root = useRef(null); const [layout, setLayout] = useState([]); const [visible, setVisible] = useState(true);
  const points = Array.isArray(match?.matched_points) ? match.matched_points.slice(0, 64) : []; const dimensions = match?.image_sizes || sizes;
  const signature = JSON.stringify([points, dimensions]);
  function measure() {
    const element = root.current; if (!element) return;
    const images = [...element.querySelectorAll('.mt-logo-pair img')]; const rect = element.getBoundingClientRect();
    const next = images.length === 2 && images.every(image => image.complete && image.naturalWidth) ? points.map(pair => {
      const ends = images.map((image, i) => { const box = image.getBoundingClientRect(); return containedPoint(pair?.[i ? 'reference' : 'crop'], dimensions?.[i], { x: box.x - rect.x, y: box.y - rect.y, width: box.width, height: box.height }, [image.naturalWidth, image.naturalHeight]); });
      return ends.every(Boolean) ? ends : null;
    }).filter(Boolean) : [];
    setLayout(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
  }
  useEffect(() => { const observer = new ResizeObserver(measure); observer.observe(root.current); measure(); return () => observer.disconnect(); }, [signature]);
  return <>
    <div className="mt-match-overlay-frame" ref={root} onLoadCapture={measure}>
      <div className="mt-logo-pair">{children}</div>
      {visible && layout.length > 0 && <svg className="mt-match-overlay" role="img" aria-label={`기하 검증 대응점 ${layout.length}쌍`}>
        {layout.map(([a, b], i) => <g key={i} stroke={`hsl(${i * 137.5 % 360} 90% 55%)`} fill={`hsl(${i * 137.5 % 360} 90% 55%)`}><line x1={a.x} y1={a.y} x2={b.x} y2={b.y} strokeWidth="1.5" opacity=".75" /><circle cx={a.x} cy={a.y} r="3" stroke="white" strokeWidth="1" /><circle cx={b.x} cy={b.y} r="3" stroke="white" strokeWidth="1" /></g>)}
      </svg>}
    </div>
    {match && <label className="mt-match-toggle"><input type="checkbox" checked={visible} onChange={event => setVisible(event.target.checked)} disabled={!points.length} />기하 검증 대응점 표시 <span>{points.length} / {match.inliers ?? 0}쌍 · 최대 64쌍 표시</span></label>}
  </>;
}

export function CandidateAudit({ row, references }) {
  if (!row?.filter) return null;
  const filter = row.filter;
  const rows = [...(row.matches || []).map(match => ({ ...match, selected: true })), ...(row.excluded_matches || [])].sort((a, b) => a.logo_index - b.logo_index || a.reference_index - b.reference_index);
  return <details className="mt-candidate-audit"><summary>후보 선정 · {FILTER_SCOPES[filter.scope] || filter.scope} · 이 프레임 {row.selected_count} / {row.candidate_count}개</summary>
    <p>최소 유사도 {filter.threshold ?? '제한 없음'} · 상위 {filter.top_k ?? '전체'} · 전체 입력 {filter.total_selected} / {filter.total_candidates}개 선택</p>
    <div className="mt-timing-scroll"><table className="mt-timing-table"><thead><tr><th>검출 크롭</th><th>기준 로고</th><th>코사인</th><th>그룹 내 순위</th><th>선정 결과</th></tr></thead><tbody>{rows.map((match, i) => <tr key={i}><td>로고 {match.logo_index + 1}</td><td>{references[match.reference_index]?.name || match.reference_index + 1}</td><td>{match.image_cosine_similarity?.toFixed(4)}</td><td>{match.candidate_rank}</td><td>{match.selected ? '선정' : match.exclusion_reason === 'below_threshold' ? '제외 · 최소 유사도 미달' : '제외 · 상위 N개 밖'}</td></tr>)}</tbody></table></div>
  </details>;
}

export function MatchingSettings({ match }) {
  if (!match?.matching_settings) return null;
  const value = match.matching_settings;
  return <details className="mt-matching-settings"><summary>XFeat 실행 설정 · 팀원 재현 미확인 · 색상 비교 미적용</summary>
    <dl><dt>실행 코드</dt><dd>{value.code_source}</dd><dt>가중치</dt><dd>{value.model_id || 'vismatch/xfeat'} @ {value.revision || 'resolved revision 미제공'}</dd><dt>가중치 SHA256</dt><dd>{value.weights_sha256 || '미제공'}</dd>
      <dt>입력 크기 정규화</dt><dd>긴 변 최대 {value.resize_max_side}px · 각 변 최소 {value.resize_min_side}px</dd><dt>특징점 / 매칭 임계값</dt><dd>{value.top_k}개 / {value.match_min_cosine}</dd><dt>RANSAC</dt><dd>{value.ransac_px}px · {value.ransac_max_iters}회 · confidence {value.ransac_confidence}</dd></dl>
    <p>색상 지표는 계산하지 않았습니다. 팀원 코드 revision·가중치·전처리·특징점·매칭·RANSAC 설정이 일치하는지는 별도 확인이 필요합니다.</p>
  </details>;
}
