export function inspectionSize(width, height, limits = {}) {
  const maxPixels = limits.image_pixels || 10_000_000;
  const maxSide = limits.image_side || 8192;
  const scale = Math.min(1, Math.sqrt(maxPixels / (width * height)), maxSide / Math.max(width, height));
  return { width: Math.max(1, Math.floor(width * scale)), height: Math.max(1, Math.floor(height * scale)) };
}

export async function makeInspectionCopy(file, limits) {
  const bitmap = await createImageBitmap(file);
  const canvas = document.createElement('canvas');
  const size = inspectionSize(bitmap.width, bitmap.height, limits);
  try {
    canvas.width = size.width;
    canvas.height = size.height;
    canvas.getContext('2d').drawImage(bitmap, 0, 0, size.width, size.height);
  } finally { bitmap.close(); }
  const maxBytes = limits?.upload_bytes || 20 * 1024 * 1024;
  let blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
  if (!blob || blob.size > maxBytes) blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/webp', 0.94));
  if (!blob || blob.size > maxBytes) throw new Error('검수용 사본을 파일 한도 안으로 줄이지 못했습니다.');
  const extension = blob.type === 'image/webp' ? 'webp' : 'png';
  return new File([blob], file.name.replace(/\.[^.]+$/, '') + '_검수용.' + extension, { type: blob.type });
}
