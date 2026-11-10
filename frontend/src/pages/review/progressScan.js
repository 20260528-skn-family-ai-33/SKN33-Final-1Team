// 요구사항 문서 분석을 진행 화면의 세 부분으로 나눈다.
// 화면이 받은 단계(frameworkDocument.js의 onProgress)만 쓰고, 경과 시간으로 다음 부분을 추정하지 않는다.
const parts = [
  { key: 'source', label: '원문과 버전 저장', short: '원문 저장', kind: 'document', stages: ['preparing', 'uploading'] },
  { key: 'extract', label: '원문에서 요구사항 추출', short: '요구사항 추출', kind: 'extract', stages: ['queued', 'extracting'] },
  { key: 'organize', label: '요구사항과 원문 근거 정리', short: '기준 정리', kind: 'criteria', stages: ['formatting'] },
];

// 돋보기를 올릴 칸: 진행·대기·멈춤 단계, 없으면 다음 단계, 모두 끝났으면 마지막 단계.
export function scanFocus(steps) {
  const index = steps.findIndex(step => ['active', 'waiting', 'failed'].includes(step.status));
  if (index >= 0) return { index, phase: { active: 'working', waiting: 'waiting', failed: 'failed' }[steps[index].status] };
  if (steps.every(step => step.status === 'completed')) return { index: steps.length - 1, phase: 'done' };
  return { index: Math.max(0, steps.findIndex(step => step.status === 'pending')), phase: 'between' };
}

export function documentSteps(stage) {
  const current = stage === 'completed' ? parts.length : parts.findIndex(part => part.stages.includes(stage));
  return parts.map(({ stages, ...part }, index) => ({ ...part,
    status: current < 0 ? 'pending' : index < current ? 'completed' : index > current ? 'pending'
      : stage === 'queued' ? 'waiting' : 'active' }));
}
