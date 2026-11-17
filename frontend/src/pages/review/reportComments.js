// 포스터 보고서 '확인 필요' 항목의 담당자 코멘트(2026-11-17 베타 피드백).
// 서버에 보내지 않고 이 브라우저(localStorage)에만 실행(run_id)별로 둔다. 다른 PC·다른 브라우저에서는 보이지 않는다.
// 저장소를 쓸 수 없으면(사생활 보호 창·차단) 읽기는 빈 값, 쓰기는 false를 돌려준다. 화면은 그대로 쓸 수 있다.
export const REPORT_COMMENT_MAX = 1000;
const PREFIX = 'poster-report-comments:';

function storage(store) {
  try { return store === undefined ? globalThis.localStorage : store; } catch { return null; }
}

export function readReportComments(runId, store) {
  if (!runId) return {};
  try {
    const parsed = JSON.parse(storage(store)?.getItem(PREFIX + runId) || 'null');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return Object.fromEntries(Object.entries(parsed).filter(([, text]) => typeof text === 'string' && text.trim())
      .map(([id, text]) => [id, text.slice(0, REPORT_COMMENT_MAX)]));
  } catch { return {}; }
}

// 내용이 있는 코멘트만 남긴다. 모두 비우면 저장 항목을 지운다.
export function writeReportComments(runId, comments, store) {
  if (!runId) return false;
  const kept = Object.fromEntries(Object.entries(comments || {}).filter(([, text]) => typeof text === 'string' && text.trim()));
  try {
    const target = storage(store);
    if (!target) return false;
    if (Object.keys(kept).length) target.setItem(PREFIX + runId, JSON.stringify(kept));
    else target.removeItem(PREFIX + runId);
    return true;
  } catch { return false; }
}
