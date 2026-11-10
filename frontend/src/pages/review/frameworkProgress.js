const labels = {
  A02: '원문에서 요구사항 추출',
  A04_A05: '포스터 문구·시각 조건 검사',
  A06: '검사 결과와 원문 근거 확인',
  A07: '검수 보고서 정리',
};

// 진행 화면의 돋보기 띠에 쓰는 짧은 이름과 칸 그림 종류.
const parts = {
  A02: { short: '요구사항 추출', kind: 'extract' },
  A04_A05: { short: '문구·시각 조건 검사', kind: 'poster', headline: '포스터의 문구와 시각 조건을 확인하고 있어요.' },
  A06: { short: '근거 확인', kind: 'compare', headline: '검사 결과와 원문 근거를 확인하고 있어요.' },
  A07: { short: '결과 정리', kind: 'report', headline: '항목별 검수 결과를 정리하고 있어요.' },
};

export const processingStatuses = new Set(['queued', 'extracting', 'reviewing']);

// Count completed role boundaries, never elapsed time or guessed model progress.
export function frameworkProgress(run) {
  const document = !run?.baseline;
  const roles = document ? ['A02'] : ['A04_A05', 'A06', 'A07'];
  const records = new Map((run?.step_records || []).map(record => [record.role, record]));
  const stages = new Set(run?.stages || []);
  const artifacts = { A02: run?.criteria, A04_A05: run?.inspection, A06: run?.validated, A07: run?.report };
  const queued = run?.execution_status === 'queued';
  const steps = roles.map(role => {
    const record = records.get(role);
    const done = record ? ['completed', 'reused'].includes(record.status) : stages.has(role) || !!artifacts[role];
    return { key: role, label: labels[role], ...parts[role], status: done ? 'completed'
      : record?.status === 'running' ? 'active' : 'pending' };
  });
  // 접수 대기 중이면 다음에 처리할 단계를 '차례 대기'로 표시한다(진행률에는 반영하지 않는다).
  const next = queued && !steps.some(step => step.status === 'active') ? steps.find(step => step.status === 'pending') : null;
  if (next) next.status = 'waiting';
  const completed = steps.filter(step => step.status === 'completed').length;
  const active = steps.find(step => step.status === 'active');
  return {
    poster: !document,
    queued,
    title: document ? '문서에서 검수 요구사항을 정리하고 있어요.'
      : queued ? '포스터 검수 순서를 기다리고 있어요.'
        : active?.headline || '확정한 기준으로 포스터를 검수하고 있어요.',
    message: queued ? '작업을 접수했습니다. 처리 순서를 기다리고 있어요.'
      : active?.label || (document ? '요구사항과 원문 출처를 확인하고 있어요.' : '검사 상태를 확인하고 있어요.'),
    detail: `${completed} / ${roles.length}개 처리 단계 완료`,
    startedAt: run?.step_records?.find(record => record.started_at)?.started_at,
    percent: completed ? Math.round(completed / roles.length * 100) : null,
    steps,
  };
}
