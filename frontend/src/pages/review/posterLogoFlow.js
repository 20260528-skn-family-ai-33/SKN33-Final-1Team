import { fetchJson, fetchResponse } from '../../api/client.js';

// 포스터 로고 검수(/api/poster-logos)의 접수·계산. 순위 점수(rank)는 원본 로고 SigLIP2 유사도에 OCR 가산점(+0.10)을 더한 값으로,
// 영상 로고 1부와 같은 규칙이다. 포스터는 한 장이라 시간 구간·연결 후보가 없고, 위치는 포스터 원본 픽셀 좌표다.
export const POSTER_LOGO_API = '/api/poster-logos';
export const TOP_N = 3;
const SVG_SIDE = 1024;

// 원본 로고 기준 순위 상위 n개 후보 번호
export function rankedCandidates(candidates, ref, n = candidates.length) {
  return candidates.map((_, i) => i).sort((a, b) => candidates[b].rank[ref] - candidates[a].rank[ref]).slice(0, n);
}

// 정밀 대조로 넘기는 값: 후보는 번호만(서버가 저장한 대표 상자로 자른다), 직접 지정은 포스터 원본 좌표 상자.
export function posterLogoAnswer({ ref, refName, decision, checked, manuals }) {
  if (decision === 'none') return { logo: ref, reference: refName, decision, crops: [] };
  const crops = [...checked].sort((a, b) => a - b).map(candidate => ({ source: 'candidate', candidate }))
    .concat(manuals.map(box => ({ source: 'manual', bbox: box.map(v => Math.round(v * 10) / 10) })));
  return { logo: ref, reference: refName, decision, crops };
}

// 브라우저가 그린 SVG를 PNG로 바꾼다. 서버의 이미지 라이브러리는 SVG를 읽지 않는다.
async function rasterizeSvg(file) {
  const url = URL.createObjectURL(file);
  try {
    const image = await new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error(`${file.name}: SVG 로고를 읽지 못했어요. PNG로 바꿔 첨부해 주세요.`));
      img.src = url;
    });
    const width = image.naturalWidth || SVG_SIDE, height = image.naturalHeight || SVG_SIDE;
    const scale = SVG_SIDE / Math.max(width, height);
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve, reject) => canvas.toBlob(value => value ? resolve(value)
      : reject(new Error(`${file.name}: SVG 로고를 PNG로 바꾸지 못했어요.`)), 'image/png'));
    return new File([blob], file.name.replace(/\.svg$/i, '') + '.png', { type: 'image/png' });
  } finally {
    URL.revokeObjectURL(url);
  }
}

const isSvg = file => file.type === 'image/svg+xml' || /\.svg$/i.test(file.name);

// 포스터 검수 실행(runId)에 연결한 포스터 로고 작업을 만든다. 같은 실행에 이미 작업이 있으면 다시 만들지 않는다.
export async function submitPosterLogoJob({ poster, logos, title, runId, signal }) {
  if (!poster || !logos?.length) return null;
  const existing = await fetchJson(`${POSTER_LOGO_API}?run_id=${encodeURIComponent(runId)}`, { signal });
  if (existing.items?.length) return existing.items[0];
  const form = new FormData();
  form.append('poster', poster);
  for (const item of logos) form.append('logos', isSvg(item.file) ? await rasterizeSvg(item.file) : item.file);
  form.append('logo_texts', JSON.stringify(logos.map(item => (item.text || '').trim())));
  form.append('title', title || '포스터 로고 검수');
  form.append('run_id', runId);
  return fetchJson(POSTER_LOGO_API, { method: 'POST', body: form, signal });
}

// 수정본 재검수: 이전 실행의 로고 작업에서 원본 로고와 기준 문구를 가져와 새 포스터에 다시 쓴다.
export async function previousLogos(runId, { signal } = {}) {
  const list = await fetchJson(`${POSTER_LOGO_API}?run_id=${encodeURIComponent(runId)}`, { signal });
  const job = list.items?.[0];
  if (!job) return [];
  const meta = await fetchJson(`${POSTER_LOGO_API}/${encodeURIComponent(job.id)}`, { signal });
  return Promise.all((meta.logos || []).map(async (logo, index) => {
    const response = await fetchResponse(`${POSTER_LOGO_API}/${encodeURIComponent(job.id)}/refs/${index}.png`, { signal });
    return { file: new File([await response.blob()], `${logo.name}.png`, { type: 'image/png' }), text: logo.text || '' };
  }));
}
