import React from 'react';
import ReviewReport from '../../components/ReviewReport.jsx';

const verdicts = { pass: '충족', fail: '불충족', review: '확인 필요' };
const decisions = { unreviewed: '검토 대기', changes_approved: '수정 요청 승인', final_approved: '최종 승인', exception_approved: '예외 승인' };

export default function FrameworkReport({ run, projectName, onBack }) {
  const criteria = run.criteria?.criteria || [];
  const items = run.validated?.items || [];
  const evidence = run.evidence?.evidence || [];
  const approvals = run.approvals || [];
  return <ReviewReport projectName={projectName || '포스터 검수'} mode="포스터 검수" detail={`실행 ${run.context.run_id}`}
    verdicts={items.map(item => item.verdict)} onBack={onBack}>
      <section><h3>검수 대상과 기준</h3><dl className="rv-report-facts">
        <div><dt>포스터 버전</dt><dd>{run.context.asset_version_id || '정보 없음'}</dd></div>
        <div><dt>승인 기준</dt><dd>{run.baseline?.criteria_version_id || run.context.criteria_version_id || '정보 없음'}</dd></div>
        <div><dt>실행 상태</dt><dd>{run.execution_status}</dd></div>
        <div><dt>기계 판정</dt><dd>기계 판정 · {verdicts[run.machine_verdict] || '판정 없음'}</dd></div>
        <div><dt>담당자 결정</dt><dd>{decisions[run.human_decision] || '기록 확인 필요'}</dd></div>
      </dl>{run.synthetic && <p>합성 검증 데이터</p>}</section>
      <section><h3>조건별 결과·근거</h3>{items.length ? items.map(item => {
        const criterion = criteria.find(entry => entry.criterion_id === item.criterion_id);
        const sources = (item.evidence_ids || []).map(id => evidence.find(entry => entry.evidence_id === id)).filter(Boolean);
        const image = item.image?.asset_version_id === run.context.asset_version_id ? item.image : null;
        return <section className="rv-report-result" key={item.criterion_id}>
          <h4>{item.criterion_text || criterion?.text || item.criterion_id}</h4>
          <p>{item.criterion_id} · {item.required ? '필수' : '참고'} · {verdicts[item.verdict] || '판정 없음'}</p>
          <dl className="rv-report-comparison"><div><dt>기대값</dt><dd>{item.expected || '기록 없음'}{item.expected_unit ? ` ${item.expected_unit}` : ''}</dd></div>
            <div><dt>검사 모델이 읽은 값</dt><dd>{item.observed || '기록 없음'}{item.observed_unit ? ` ${item.observed_unit}` : ''}</dd></div></dl>
          {criterion?.source && <p>기준 원문 · {criterion.source.page}쪽<br />{criterion.source.quote}</p>}
          {criterion?.value_source && <p>보완 회신 · {criterion.value_source.reference}<br />{criterion.value_source.quote}</p>}
          {sources.map(source => <p key={source.evidence_id}>근거 · {source.source?.page}쪽<br />{source.source?.quote}</p>)}
          {image?.bbox && <p>이미지 위치 · {image.bbox.join(', ')} (정규화 좌표)</p>}
          {item.issues?.length > 0 && <p>확인 사유 · {item.issues.join(', ')}</p>}
        </section>;
      }) : <p>기록된 검사 결과가 없습니다.</p>}</section>
      {run.validated?.limitations?.length > 0 && <section><h3>검수 제한 사항</h3><ul>{run.validated.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></section>}
      {run.report && <section><h3>보고서 원문과 수정 요청</h3>
        <details><summary>보고서 원문 보기</summary><p className="rv-report-original">{run.report.summary}</p></details>
        {run.report.polish_status && <p>보고 문장 다듬기 상태 · {run.report.polish_status}</p>}
        {run.report.change_requests?.length > 0 && <ul>{run.report.change_requests.map((item, index) => <li key={index}>{item}</li>)}</ul>}
      </section>}
      <section><h3>담당자 승인 이력</h3>{approvals.length ? approvals.map((approval, index) => <div className="rv-report-approval" key={index}>
        <p>{approval.decision} · {approval.actor_id || '담당자 정보 없음'}</p>{approval.reason && <p>사유 · {approval.reason}</p>}
        {(approval.human_reviews || []).map(review => <p key={review.criterion_id}>{review.criterion_id} · {verdicts[review.verdict]}<br />확인 내용 · {review.observed}<br />근거 · {review.reference}<br />사유 · {review.reason}</p>)}
      </div>) : <p>담당자 승인 이력이 없습니다.</p>}</section>
      <footer>자동 판정과 담당자의 최종 판단을 함께 확인해 주세요.</footer>
  </ReviewReport>;
}
