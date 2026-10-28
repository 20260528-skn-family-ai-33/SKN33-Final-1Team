export function frameLabel(frame) {
  const number = Number.isInteger(frame?.index) && frame.index >= 0 ? `${frame.index} 프레임` : '이미지';
  return Number.isFinite(frame?.timestamp_seconds) && frame.timestamp_seconds >= 0 ? `${number} · ${frame.timestamp_seconds.toFixed(2)}초` : number;
}

export function boxStyle(box, width, height) {
  if (!Array.isArray(box) || box.length !== 4 || !box.every(Number.isFinite) || !(width > 0) || !(height > 0)) return null;
  const [x1, y1, x2, y2] = box;
  if (x1 < 0 || y1 < 0 || x2 > width || y2 > height || x2 <= x1 || y2 <= y1) return null;
  return { left: `${x1 / width * 100}%`, top: `${y1 / height * 100}%`, width: `${(x2 - x1) / width * 100}%`, height: `${(y2 - y1) / height * 100}%` };
}

export function previewURL(value) {
  if (value?.mime !== 'image/jpeg' || typeof value.data !== 'string' || value.data.length > 2 * 1024 ** 2 || !/^\/9j\/[A-Za-z0-9+/]*={0,2}$/.test(value.data)) throw new Error('미리보기 이미지 응답이 올바르지 않습니다.');
  return `data:image/jpeg;base64,${value.data}`;
}

export function decisionLabel(value) {
  return ({ candidate_match: '일치 후보', match: '일치', mismatch: '불일치', uncertain: '판단 보류', pass: '통과', fail: '미통과' })[value] || '모델 설명 확인';
}
