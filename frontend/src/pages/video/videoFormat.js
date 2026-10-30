// 숏폼 검수 화면의 단계·판정 표시 문구와 시간·글자 비교 도우미를 정의한다.
export const stageLabels = {
  queued: '검수 순서를 기다리고 있어요.',
  extracting: '표본 시각마다 프레임을 뽑고 있어요.',
  scene_detection: '장면 경계를 찾고 있어요.',
  ocr: '프레임마다 글자를 읽고 있어요.',
  classifying: '자막과 배경 글자를 나누고 있어요.',
  segmenting: '같은 자막을 트랙과 구간으로 묶고 있어요.',
  review: '확정한 기준과 광고 표시를 판정하고 있어요.',
  double_check: '결과를 정리하고 있어요.',
  completed: '동영상 검수를 완료했어요.',
  failed: '동영상 검수를 완료하지 못했어요.',
};
export const statusLabels = { pass: '충족', fail: '위반', review: '확인 필요', not_applicable: '대상 아님', skipped: '생략' };
export const severityLabels = { block: '차단', warn: '경고', info: '정보' };
export const reviewerLabels = { auto: '자동', manager: '담당자' };
export const roleLabels = { subtitle: '자막', background: '배경', other: '기타' };
export const mismatchLabels = { variants: '프레임마다 다르게 읽힘', spacing: '띄어쓰기 다름', case: '대소문자 다름', one_character: '한 글자 다름' };
export const ruleLabels = {
  R0: '적용 대상', R1: '표시 문구', R2: '제목 앞 15자', R3a: '시작 3초', R3b: '끝 3초', R3c: '중간 반복', R4: '캡션만', R5: '형태·시인성', R6: '가상인물',
};

// 서버 기준 형식(포스터와 같이 정의되지 않은 필드는 거부)에 맞게 화면용 필드(selected 등)를 뺀다.
export function criteriaPayload(criteria) {
  return criteria.map(({ id, text, type, source }) => ({ id, text, type, source }));
}

// 완료·실패면 더 이상 상태를 다시 읽지 않는다.
export function isTerminal(status) { return status === 'completed' || status === 'failed'; }

// 밀리초를 m:ss 형식으로, 0.2초 단위 시각은 m:ss.s로 바꾼다.
export function formatTime(ms) {
  const value = Math.max(0, Number(ms) || 0);
  const seconds = Math.floor(value / 1000);
  const tenth = Math.floor((value % 1000) / 100);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}${tenth ? `.${tenth}` : ''}`;
}

// 기준 문구와 화면 문구를 글자 단위로 맞춰 다른 글자만 표시한다(LCS).
export function diffChars(expected = '', observed = '') {
  const a = Array.from(expected), b = Array.from(observed);
  const table = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i -= 1) for (let j = b.length - 1; j >= 0; j -= 1) {
    table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
  }
  const marks = []; let i = 0, j = 0;
  while (j < b.length) {
    if (i < a.length && a[i] === b[j]) { marks.push([b[j], false]); i += 1; j += 1; }
    else if (i < a.length && table[i + 1][j] >= table[i][j + 1]) i += 1;
    else { marks.push([b[j], true]); j += 1; }
  }
  return marks;
}
