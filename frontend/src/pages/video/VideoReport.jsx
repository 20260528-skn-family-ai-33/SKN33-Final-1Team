import React from 'react';
import ReviewReport from '../../components/ReviewReport.jsx';
import { coverageNote, formatTime, judgmentItems, statusLabels } from './videoFormat.js';

export default function VideoReport({ job, onBack }) {
  const items = judgmentItems(job);
  return <ReviewReport projectName={job.project_name || '숏폼 검수'} mode="동영상 검수" detail={`기준 v${job.criteria_version} · 실행 ${job.id}`}
    verdicts={items.map(item => item.status)} onBack={onBack}>
    <section><h3>검수 대상과 범위</h3>
      {job.media && <p>영상 {formatTime(job.media.duration_ms)} · {job.media.width}×{job.media.height}</p>}
      <p>{coverageNote(job.coverage)}</p>
      {job.warnings?.length > 0 && <ul>{job.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
    </section>
    <section><h3>항목별 결과</h3>
      {items.map(({ key, result, disclosure, status }, index) => <section className="rv-report-result" key={key}>
        <div className="rv-report-result-heading"><h4>{index + 1}. {result?.criterion || '광고·협찬 표시'}</h4><strong>{statusLabels[status] || '확인 필요'}</strong></div>
        {result?.observed_text && <p>관측 · {result.observed_text}</p>}
        <p>{result?.reason || disclosure?.reason || '기록된 판정 이유가 없습니다.'}</p>
        {result?.evidence_segments?.length > 0 && <ul>{result.evidence_segments.map((item, evidenceIndex) => <li key={evidenceIndex}>
          {Number.isFinite(item.start_ms) && Number.isFinite(item.end_ms)
            ? `${formatTime(item.start_ms)}–${formatTime(item.end_ms)}`
            : Number.isFinite(item.time_ms) ? formatTime(item.time_ms) : '시각 정보 없음'} · {item.text || '위치 근거'}</li>)}</ul>}
      </section>)}
      {!items.length && <p>기록된 항목별 판정이 없습니다.</p>}
    </section>
    <footer>판정은 검사한 표본 시각을 기준으로 합니다. 확인 필요 항목은 원본 영상에서 확인해 주세요.</footer>
  </ReviewReport>;
}
