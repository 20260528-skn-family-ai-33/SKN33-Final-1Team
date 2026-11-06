const labels = {
  A02: '원문에서 요구사항 추출',
  A04_A05: '포스터 문구·시각 조건 검사',
  A06: '검사 결과와 원문 근거 확인',
  A07: '검수 보고서 정리',
};

export const processingStatuses = new Set(['queued', 'extracting', 'reviewing']);

// Count completed role boundaries, never elapsed time or guessed model progress.
export function frameworkProgress(run) {
  const document = !run?.baseline;
  const roles = document ? ['A02'] : ['A04_A05', 'A06', 'A07'];
  const records = new Map((run?.step_records || []).map(record => [record.role, record]));
  const stages = new Set(run?.stages || []);
  const artifacts = { A02: run?.criteria, A04_A05: run?.inspection, A06: run?.validated, A07: run?.report };
  const steps = roles.map(role => {
    const record = records.get(role);
    const done = record ? ['completed', 'reused'].includes(record.status) : stages.has(role) || !!artifacts[role];
    return { key: role, label: labels[role], status: done ? 'completed'
      : record?.status === 'running' ? 'active' : 'pending' };
  });
  const completed = steps.filter(step => step.status === 'completed').length;
  const active = steps.find(step => step.status === 'active');
  return {
    title: document ? '문서에서 검수 요구사항을 정리하고 있어요.' : '확정한 기준으로 포스터를 검수하고 있어요.',
    message: run?.execution_status === 'queued' ? '작업을 접수했습니다. 처리 순서를 기다리고 있어요.'
      : active?.label || (document ? '요구사항과 원문 출처를 확인하고 있어요.' : '검사 상태를 확인하고 있어요.'),
    detail: `${completed} / ${roles.length}개 처리 단계 완료`,
    startedAt: run?.step_records?.find(record => record.started_at)?.started_at,
    percent: completed ? Math.round(completed / roles.length * 100) : null,
    steps,
  };
}
