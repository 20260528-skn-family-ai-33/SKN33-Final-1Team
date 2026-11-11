// 관계 조회 응답(기준 변경 영향 · 수정본 검수 이력)을 화면 문구로 바꾼다. 판정·승인을 새로 만들지 않는다.

const changeWords = { changed: '변경됐어요', added: '추가됐어요', removed: '빠졌어요' };
const changeTags = { changed: '변경', added: '추가', removed: '삭제' };
const stateLabels = {
  final_approved: '최종 승인', exception_approved: '예외 승인', changes_requested: '수정 요청',
  awaiting_decision: '승인 대기', in_progress: '검수 중', failed: '검수 실패', completed: '검수 완료',
};
const runStatusLabels = { final_approved: '최종 승인', exception_approved: '예외 승인', changes_approved: '수정 요청' };
const recordLabels = { approve_changes: '수정 요청', approve_final: '최종 승인', approve_exception: '예외 승인' };

// 받침이 있으면 '이', 없으면 '가'. 한글로 끝나지 않으면 '이(가)'로 둔다.
export function subjectParticle(word) {
  const code = (word || '').trim().charCodeAt((word || '').trim().length - 1);
  if (!(code >= 0xac00 && code <= 0xd7a3)) return '이(가)';
  return (code - 0xac00) % 28 ? '이' : '가';
}

function valueText(item) {
  if (!item) return null;
  return [item.expected?.trim() || '기대값 미정', item.unit].filter(Boolean).join(' ');
}

// 이전 승인 기준과 달라진 항목이 있을 때만 안내를 만든다.
export function changeNotice(impact) {
  const changes = impact?.previous ? impact.changes || [] : [];
  if (!changes.length) return null;
  const first = changes[0];
  const label = first.label || '검수 항목';
  const headline = changes.length === 1
    ? `${label}${subjectParticle(label)} ${changeWords[first.change]}.`
    : `${label} 외 ${changes.length - 1}개 항목이 달라졌어요.`;
  const { affected_runs: total = 0, approved = 0 } = impact.summary || {};
  return {
    headline,
    detail: total ? `이전 기준을 사용한 포스터 검수 ${total}건 중 ${approved}건이 승인됐어요.`
      : '이전 기준을 사용한 포스터 검수는 아직 없어요.',
    action: total ? `영향받는 검수 ${total}건 보기` : null,
    basis: impact.previous.basis === 'document_revision' ? '같은 문서의 이전 버전에서 승인한 기준과 비교했어요.'
      : '이 프로젝트에서 직전에 승인한 기준과 비교했어요.',
    changes: changes.map((item, index) => ({
      key: `${item.change}-${index}`, tag: changeTags[item.change], label: item.label || (item.after || item.before).text,
      before: valueText(item.before), after: valueText(item.after),
    })),
    runs: (impact.affected_runs || []).map(run => ({
      runId: run.run_id,
      status: runStatusLabels[run.human_decision] || (run.execution_status === 'waiting_review' ? '승인 대기'
        : run.execution_status === 'completed' ? '검수 완료' : '검수 중'),
      approved: ['final_approved', 'exception_approved'].includes(run.human_decision),
      createdAt: run.created_at,
    })),
    truncated: Boolean(impact.truncated),
  };
}

function revisionTitle(number) {
  return number ? `${number}차 수정본 검수` : '원본 검수';
}

function recordText(entry) {
  const lines = entry.records.filter(record => recordLabels[record.decision]).map(record => ({
    key: record.approval_id, label: recordLabels[record.decision], reason: record.reason?.trim() || '', at: record.approved_at,
  }));
  if (lines.length) return lines;
  const waiting = { awaiting_decision: '검수가 끝났고 담당자 결정을 기다리고 있어요.', in_progress: '검수를 진행하고 있어요.',
    failed: '검수를 끝내지 못했어요.' };
  return [{ key: 'none', label: null, reason: waiting[entry.state] || '담당자 기록이 없어요.', at: null }];
}

// 수정본이 한 번이라도 있을 때만 이력을 보여 준다.
export function revisionView(history) {
  const entries = history?.entries || [];
  if (entries.length < 2) return null;
  const final = history.summary?.final_approved;
  return {
    rereviews: history.summary?.rereviews ?? entries.length - 1,
    final: final ? (final.revision_no ? `${final.revision_no}차 수정본` : '원본') : null,
    waiting: Boolean(history.summary?.awaiting_decision),
    rows: entries.map(entry => ({
      runId: entry.run_id, title: revisionTitle(entry.revision_no), current: Boolean(entry.current),
      state: entry.state, status: stateLabels[entry.state] || '확인 필요', approved: entry.state === 'final_approved',
      assetVersionId: entry.asset_version_id,
      criteria: entry.criteria_version_no ? `검수 기준 v${entry.criteria_version_no}` : '승인된 검수 기준',
      createdAt: entry.created_at, records: recordText(entry),
    })),
    truncated: Boolean(history.truncated),
  };
}
