import { fetchResponse } from '../../api/client.js';

const placeFields = new Set(['venue', 'location', 'event_location', 'event_venue', 'address', 'venue_address', 'event_address']);
const placeLabel = /^(?:(?:행사|전시|공연|개최|촬영)\s*)?(?:장소|장소명|주소|위치)\s*(?:[:：]|$)/u;
const quietZone = 4;

export function posterPlaceCandidates(criteria, results) {
  const byId = new Map(criteria.map(criterion => [criterion.criterion_id, criterion]));
  const observed = new Set();
  const expected = new Set();
  for (const item of results) {
    const details = byId.get(item.criterion_id)?.details;
    if (item.kind !== 'text' || !(placeFields.has(details?.field) ||
      placeLabel.test(details?.presentation?.label || '') || placeLabel.test(item.criterion_text || ''))) continue;
    for (const [values, value] of [[observed, item.observed], [expected, item.expected]]) {
      const place = typeof value === 'string' ? value.trim().replace(/\s+/gu, ' ') : '';
      if (place && place.length <= 120 && !/^(?:미정|확인\s*중|미확인|없음)$/u.test(place)) values.add(place);
    }
  }
  return [...(observed.size ? observed : expected)].slice(0, 5);
}

export function naverMapUrl(place) {
  const query = place.trim();
  return query ? 'https://map.naver.com/p/search/' + encodeURIComponent(query.toWellFormed()) : null;
}

export async function renderPosterMapQr(imageUrl, region, mapUrl, signal) {
  const { default: QRCode } = await import('qrcode');
  const response = await fetchResponse(imageUrl, { signal });
  const image = await createImageBitmap(await response.blob());
  try {
    signal.throwIfAborted();
    const { modules } = QRCode.create(mapUrl, { errorCorrectionLevel: 'M' });
    const moduleCount = modules.size + 2 * quietZone;
    const shortestSide = Math.min(image.width, image.height);
    const margin = Math.floor(shortestSide * 0.05);
    let scale;
    let x;
    let y;
    if (region) {
      const left = Math.ceil(region.x * image.width);
      const top = Math.ceil(region.y * image.height);
      const width = Math.floor((region.x + region.width) * image.width) - left;
      const height = Math.floor((region.y + region.height) * image.height) - top;
      scale = Math.floor(Math.min(width, height) / moduleCount);
      x = left + Math.floor((width - moduleCount * scale) / 2);
      y = top + Math.floor((height - moduleCount * scale) / 2);
    } else {
      scale = Math.min(Math.floor((shortestSide - margin) / moduleCount),
        Math.max(4, Math.floor(shortestSide * 0.2 / moduleCount)));
      x = image.width - margin - moduleCount * scale;
      y = image.height - margin - moduleCount * scale;
    }
    if (scale < 4) throw new Error(region
      ? 'QR을 읽을 수 있도록 포스터에서 더 넓은 영역을 선택해 주세요.'
      : '이 포스터의 해상도가 낮아 읽을 수 있는 QR을 넣기 어려워요.');

    const size = moduleCount * scale;
    const canvas = document.createElement('canvas');
    canvas.width = image.width;
    canvas.height = image.height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('포스터에 QR을 넣지 못했어요.');
    ctx.drawImage(image, 0, 0);
    ctx.fillStyle = '#fff';
    ctx.fillRect(x, y, size, size);
    ctx.fillStyle = '#000';
    for (let row = 0; row < modules.size; row += 1) {
      for (let column = 0; column < modules.size; column += 1) {
        if (modules.get(row, column)) {
          ctx.fillRect(x + (column + quietZone) * scale, y + (row + quietZone) * scale, scale, scale);
        }
      }
    }
    return await new Promise((resolve, reject) => {
      canvas.toBlob(blob => {
        if (blob) resolve(blob);
        else reject(new Error('QR을 넣은 포스터를 저장하지 못했어요.'));
      }, 'image/png');
    });
  } finally {
    image.close();
  }
}
