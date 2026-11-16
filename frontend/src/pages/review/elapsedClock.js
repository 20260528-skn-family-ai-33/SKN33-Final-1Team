// 포스터 검수 진행(ReviewProgress.jsx)과 문서 분석 카드(DocumentInput.jsx)가 경과 시간을 같은 모양으로 적는다. 예: 0:44, 12:05
export function formatClock(value) {
  const seconds = Math.max(0, Math.floor(value || 0));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}
