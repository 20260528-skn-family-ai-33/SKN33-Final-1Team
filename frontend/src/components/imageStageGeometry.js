export function imageStageSize(width, height, viewportWidth, viewportHeight, zoom = 'fit') {
  if (![width, height, viewportWidth, viewportHeight].every(value => Number.isFinite(value) && value > 0)) return null;
  const fit = Math.min(Math.max(1, viewportWidth - 64) / width, Math.max(1, viewportHeight - 64) / height, 1.5);
  const scale = zoom === 'fit' ? fit : Math.max(.25, Math.min(3, Number.isFinite(Number(zoom)) ? Number(zoom) : 1));
  return { width: width * scale, height: height * scale, scale };
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
