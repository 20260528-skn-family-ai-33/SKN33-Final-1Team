export const MODEL_STATUS = { unverified: '미검증', verified: '실행 확인', needs_preparation: '준비 필요', implementation_required: '추가 구현 필요' };
export const GENERAL = ['text_input', 'vector_compare', 'phash', 'orb', 'sift', 'disk_lightglue', 'bm25', 'rrf', 'tfidf_lr'];
export const EXTRA_STAGES = [
  ['image_embed', '이미지 임베딩'], ['text_embed', '텍스트 임베딩'], ['multimodal_embed', '이미지·텍스트 임베딩'], ['rerank', '후보 재정렬'],
  ['image_classify', '이미지 분류'], ['text_classify', '텍스트 분류'], ['document_classify', '문서 단어 분류'],
  ['text_input', '텍스트 후보 입력'], ['vector_compare', '벡터 코사인 비교'], ['phash', 'pHash 거리'], ['orb', 'ORB 특징점 대조'], ['sift', 'SIFT 특징점 대조'],
  ['disk_lightglue', 'DISK + LightGlue 대조'], ['bm25', 'BM25 검색'], ['rrf', 'RRF 순위 합성'], ['tfidf_lr', 'TF-IDF / LR 분류'],
].map(([id, title]) => ({ id, title, icon: 'grid', agentOnly: true, video: ['phash', 'orb', 'sift', 'disk_lightglue'].includes(id), description: GENERAL.includes(id) ? '일반 처리 · HF 모델 아님' : '등록한 모델 설정으로 실행' }));
export const OUTPUTS = { conditions: 'text', text_extract: 'text', visual_extract: 'text', text_generate: 'text', text_input: 'text', logo_extract: 'detections', image_embed: 'vectors', text_embed: 'vectors', multimodal_embed: 'vectors', vector_compare: 'ranking', rerank: 'ranking', bm25: 'ranking', rrf: 'ranking', logo_similarity: 'similarity', candidate_filter: 'similarity' };
export const INPUTS = {
  image_embed: { images: ['detections'] }, multimodal_embed: { images: ['detections'], text: ['text'] }, text_embed: { text: ['text'] },
  rerank: { text: ['text'], candidates: ['ranking'], images: ['detections'] }, image_classify: { images: ['detections'] }, text_classify: { text: ['text'] }, document_classify: { ocr: ['text'] },
  vector_compare: { left: ['vectors'], right: ['vectors'] }, bm25: { text: ['text'] }, tfidf_lr: { text: ['text'] }, rrf: { left: ['ranking'], right: ['ranking'] },
  ...Object.fromEntries(['phash', 'orb', 'sift', 'disk_lightglue'].map(kind => [kind, { logo_extract: ['detections'], candidate_filter: ['similarity'] }])),
};
export function acceptsInput(kind, role, sourceKind) { return INPUTS[kind] ? (INPUTS[kind][role] || []).includes(OUTPUTS[sourceKind]) : sourceKind === role; }
export function registration(value, health) { return health?.registered?.find(model => `registered:${model.id}` === value); }
export function modelChoice(value, health) { const model = registration(value, health); return { model: value, modelFingerprint: model?.fingerprint || '', imageSimilarity: false, promptOverride: null, prompt: '' }; }
export function paramsFor(node) {
  if (!node.paramsText?.trim()) return {};
  let params; try { params = JSON.parse(node.paramsText); } catch { throw new Error('입력 설정 JSON을 확인해 주세요.'); }
  if (!params || Array.isArray(params) || typeof params !== 'object') throw new Error('입력 설정은 JSON 객체여야 합니다.');
  return params;
}
