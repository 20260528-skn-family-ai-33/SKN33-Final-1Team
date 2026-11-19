// 포스터 보고서의 '검수 확인서 인쇄'(2026-11-18 요청)에 싣는 내용.
// 양식은 공공기관 보고서 형식 샘플(output/pdf/creative-checker-confirmation-gimcheon-public.html)을 따르고,
// 값은 보고서와 같은 buildReportContext 결과에서만 가져온다. 판정은 바꾸지 않고, 실행 기록에 없는 값은 지어내지 않는다.
const pad = number => String(number).padStart(2, '0');
const quoted = text => text ? `‘${text}’` : '';

export const DECISION_OPTIONS = [
  { code: 'final_approved', label: '최종 승인' },
  { code: 'changes_approved', label: '수정 요청 승인' },
  { code: 'exception_approved', label: '예외 승인' },
];

// context: buildReportContext(run) 결과. comments: 확인 필요 항목의 담당자 코멘트({criterion_id: text}).
// decision: run.human_decision. 번호는 보고서 화면의 항목 번호(01, 02 …)와 같다.
// subject·optionalLabel: 동영상 보고서(2026-11-18)는 '동영상'·'기타'를 넘긴다. 항목의 basis가 있으면 '기준 …' 대신 쓴다
// (동영상 기준은 문장 하나라 따로 적을 기대값이 없다).
export function buildConfirmationForm(context, { comments = {}, decision = null, subject = '포스터', optionalLabel = '권장' } = {}) {
  const numbered = (context?.items || []).map((item, index) => ({ item, number: pad(index + 1) }));
  const required = numbered.filter(({ item }) => item.required);
  const basis = (item, fallback) => item.basis !== undefined ? item.basis : item.expected ? `기준 ${quoted(item.expected)}` : fallback;
  const fixes = required.filter(({ item }) => item.verdict.code === 'fail').map(({ item, number }) => ({
    number, label: item.label,
    observed: item.observed ? quoted(item.observed) : item.observation,
    request: [basis(item, '기준 미정'), item.reason].filter(Boolean).join(' · '),
  }));
  const checks = required.filter(({ item }) => item.verdict.code === 'review').map(({ item, number }) => ({
    number, label: item.label,
    content: [basis(item, null), item.reason || `원본 ${subject}으로 확인`].filter(Boolean).join(' · '),
    human: item.human ? item.human.verdict.label : null,
    comment: (comments[item.criterion_id] || '').trim(),
  }));
  const optional = numbered.filter(({ item }) => !item.required).map(({ item, number }) => `${number} ${item.label}`);
  const { counts = {} } = context || {};
  return {
    subject, optionalLabel,
    title: context?.project?.name ? `${context.project.name} ${subject} 검수 확인서` : `${subject} 검수 확인서`,
    status: decision === 'final_approved' ? '담당자 최종 승인' : context?.synthetic ? '합성 데이터 · 자동 검수 초안' : '자동 검수 초안',
    checkedAt: context?.checked_at || '기록 없음',
    runId: context?.run_id || null,
    target: context?.project?.name ? `${context.project.name} ${subject}` : `검수 ${subject}`,
    targetVersion: context?.target?.version || null,
    criteriaDocument: context?.criteria?.document || null,
    criteriaVersion: context?.criteria?.version || null,
    verdict: context?.verdict?.label || '판정 없음',
    counts: { required: counts.required || 0, pass: counts.pass || 0, review: counts.review || 0, fail: counts.fail || 0, optional: counts.optional || 0 },
    fixes, checks, optional,
    decision: DECISION_OPTIONS.some(option => option.code === decision) ? decision : null,
    notes: context?.notes || [],
  };
}
