export function imageStageSize(width, height, viewportWidth, viewportHeight, zoom = 'fit') {
  if (![width, height, viewportWidth, viewportHeight].every(value => Number.isFinite(value) && value > 0)) return null;
  const fit = Math.min(Math.max(1, viewportWidth - 64) / width, Math.max(1, viewportHeight - 64) / height, 1.5);
  const scale = zoom === 'fit' ? fit : Math.max(.25, Math.min(3, Number.isFinite(Number(zoom)) ? Number(zoom) : 1));
  return { width: width * scale, height: height * scale, scale };
}
// clientWidth/clientHeight round the real content box (498.64px -> 499). A canvas
// sized from the rounded value overflows by under a pixel, shows a scrollbar,
// refits smaller, drops the scrollbar and repeats every frame. Keep only the
// whole pixels that really fit inside the measured content box.
export function imageStageBounds(contentWidth, contentHeight) {
  return { width: Math.max(0, Math.floor(contentWidth)), height: Math.max(0, Math.floor(contentHeight)) };
}
// Labels of boxes centred in the right half open toward the image, so they stay readable
// instead of running past the canvas edge, where the stage clips decorations.
export function evidenceLabelOnRight(bbox, width) {
  return Array.isArray(bbox) && bbox.length === 4 && Number.isFinite(width) && width > 0 && (bbox[0] + bbox[2]) / 2 > width / 2;
}
// 이름표는 상자 위에 붙는다. 그 자리가 다른 상자와 겹치면(바로 윗줄에 다른 근거가 있을 때) 아래로 내린다.
// boxes: 화면에 그리는 모든 상자(원본 픽셀) · scale: 원본 픽셀 → 화면 픽셀 배율 · label: 이름표 글자
// 돌려주는 값: true면 아래에 둔다. 위·아래가 모두 겹치면 위(기본 자리)에 둔다.
export function evidenceLabelBelow(index, boxes, scale, label = '') {
  const box = boxes[index];
  if (!Array.isArray(box) || !(scale > 0)) return false;
  const wide = Math.min(220, String(label).length * 9 + 18) / scale, tall = 28 / scale;   // 이름표 크기(바깥 선 포함, 원본 픽셀)
  const onRight = evidenceLabelOnRight(box, Infinity);
  const spot = below => {
    const x1 = onRight ? box[2] - wide : box[0], y1 = below ? box[3] : box[1] - tall;
    return [x1, y1, x1 + wide, y1 + tall];
  };
  const blocked = area => boxes.some((other, at) => at !== index && Array.isArray(other)
    && Math.min(area[2], other[2]) - Math.max(area[0], other[0]) > 0 && Math.min(area[3], other[3]) - Math.max(area[1], other[1]) > 0);
  return blocked(spot(false)) && !blocked(spot(true));
}
export function canShowImageEvidence(naturalWidth, naturalHeight, declaredWidth, declaredHeight) {
  if (![naturalWidth, naturalHeight].every(value => Number.isFinite(value) && value > 0)) return false;
  if (declaredWidth == null && declaredHeight == null) return true;
  return naturalWidth === declaredWidth && naturalHeight === declaredHeight;
}

// Focus uses original pixels; padding leaves enough surrounding image to read context.
export function imageEvidenceFocus(boxes, width, height, viewportWidth, viewportHeight) {
  if (![width, height, viewportWidth, viewportHeight].every(value => Number.isFinite(value) && value > 0)) return null;
  const valid = boxes.filter(box => Array.isArray(box) && box.length === 4 && box.every(Number.isFinite)
    && box[0] >= 0 && box[1] >= 0 && box[2] <= width && box[3] <= height && box[2] > box[0] && box[3] > box[1]);
  if (!valid.length) return null;
  const left = Math.min(...valid.map(box => box[0])), top = Math.min(...valid.map(box => box[1]));
  const right = Math.max(...valid.map(box => box[2])), bottom = Math.max(...valid.map(box => box[3]));
  return { zoom: Math.max(.25, Math.min(3, (viewportWidth - 64) / (right - left + 80), (viewportHeight - 64) / (bottom - top + 80))),
    target: { x: (left + right) / 2, y: (top + bottom) / 2 } };
}

export function imageStageScroll(size, viewportWidth, viewportHeight, target) {
  if (!size) return { left: 0, top: 0 };
  const areaWidth = Math.max(viewportWidth, size.width + 64), areaHeight = Math.max(viewportHeight, size.height + 64);
  const x = target ? (areaWidth - size.width) / 2 + target.x * size.scale : areaWidth / 2;
  const y = target ? (areaHeight - size.height) / 2 + target.y * size.scale : areaHeight / 2;
  return { left: Math.max(0, Math.min(areaWidth - viewportWidth, x - viewportWidth / 2)),
    top: Math.max(0, Math.min(areaHeight - viewportHeight, y - viewportHeight / 2)) };
}
