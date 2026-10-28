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

const secondsValue = value => Number.isFinite(value) && value >= 0;

export function formatDuration(seconds) {
  if (!secondsValue(seconds)) return '미측정';
  if (seconds < 1) return `${seconds.toFixed(3)}초`;
  const rounded = Math.round(seconds * 10) / 10;
  const minutes = Math.floor(rounded / 60);
  return `${minutes ? `${minutes}분 ` : ''}${(rounded % 60).toFixed(1)}초`;
}

export function elapsedSeconds(stage, now = Date.now()) {
  const measured = secondsValue(stage?.elapsed_seconds) ? stage.elapsed_seconds : null;
  if (stage?.status === 'running' && secondsValue(stage.started_at)) return Math.max(measured || 0, now / 1000 - stage.started_at);
  return measured;
}

export function modelTimings(stages, now = Date.now()) {
  const models = new Map();
  for (const stage of stages) {
    for (const timing of Array.isArray(stage.model_timings) ? stage.model_timings : []) {
      if (!timing || typeof timing.model_id !== 'string' || !secondsValue(timing.load_seconds) || !secondsValue(timing.inference_seconds)
        || !Number.isSafeInteger(timing.calls) || timing.calls < 0 || (timing.revision != null && typeof timing.revision !== 'string')) continue;
      const key = JSON.stringify([timing.model_id, timing.revision || '']);
      const row = models.get(key) || { key, model_id: timing.model_id, revision: timing.revision, load_seconds: 0, inference_seconds: 0, calls: 0 };
      row.load_seconds += timing.load_seconds; row.inference_seconds += timing.inference_seconds; row.calls += timing.calls;
      if (stage.status === 'running' && ['load', 'inference'].includes(timing.active_phase) && secondsValue(timing.active_started_at)) {
        row[`${timing.active_phase}_seconds`] += Math.max(0, now / 1000 - timing.active_started_at);
        row.running = true;
      }
      row.estimated ||= !!timing.timing_estimated;
      models.set(key, row);
    }
  }
  return [...models.values()];
}
