// Strict projections of cc-api-v1. Missing state is never interpreted as success.
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export const KEY = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const revision = value => typeof value === 'string' && /^[1-9][0-9]{0,18}$/.test(value) && BigInt(value) <= 9223372036854775807n;
export const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
export const id = value => typeof value === 'string' && UUID.test(value);
export const text = value => typeof value === 'string';
export const integer = value => Number.isSafeInteger(value) && value >= 0;
const positive = value => integer(value) && value > 0;
const nullable = (value, test) => value === null || test(value);
export const state = (value, values) => values.includes(value);
export function requireValue(condition) { if (!condition) throw new Error('invalid_response'); }
export function envelope(body) { requireValue(object(body) && body.meta?.contract_version === 'cc-api-v1' && id(body.meta.request_id)); return body; }
export function user(value) { requireValue(object(value) && id(value.id) && text(value.display_name) && state(value.account_kind, ['email', 'developer']) && nullable(value.email, text)); return value; }
export function session(body) {
  envelope(body); requireValue(typeof body.authenticated === 'boolean');
  if (!body.authenticated) { requireValue(body.user === null); return null; }
  return user(body.user);
}
export function csrf(body) { envelope(body); requireValue(text(body.csrf_token) && body.csrf_token.length > 0 && body.csrf_token.length <= 256 && typeof body.developer_login_available === 'boolean'); return body; }
export function project(body) { requireValue(id(body.id) && text(body.name) && state(body.my_role, ['owner', 'editor', 'viewer']) && state(body.business_state, ['active', 'archived'])); return body; }
export function set(body) { requireValue(id(body.id) && id(body.project_id) && text(body.name)); return body; }
export function image(body) {
  requireValue(id(body.id) && id(body.project_id) && id(body.asset_id) && positive(body.width) && positive(body.height) && id(body.frame_id)
    && /^[0-9a-f]{64}$/.test(body.original_sha256) && /^[0-9a-f]{64}$/.test(body.display_sha256) && body.content_type === 'image/png'
    && body.coordinate_space === 'normalized_original_pixels' && body.url === `/api/v1/images/${body.id}/content`);
  return body;
}
export function item(row) { return object(row) && text(row.id) && text(row.text) && text(row.source) && state(row.type, ['information', 'visual']) && typeof row.selected === 'boolean'; }
export function draft(body) {
  requireValue(id(body.id) && id(body.project_id) && id(body.criteria_set_id) && id(body.image_id) && revision(body.revision)
    && text(body.raw_requirements) && Array.isArray(body.items) && body.items.length <= 100 && body.items.every(item)
    && new Set(body.items.map(row => row.id)).size === body.items.length && state(body.state, ['editing', 'archived'])
    && (body.source_documents === null || Array.isArray(body.source_documents)));
  return body;
}
export function criteria(body) {
  requireValue(id(body.id) && id(body.project_id) && id(body.image_id) && id(body.criteria_set_id) && positive(body.version_no)
    && typeof body.draft_is_current === 'boolean' && nullable(body.source_draft_id, id) && nullable(body.source_draft_revision, revision)
    && state(body.confirmation_state, ['confirmed', 'unconfirmed']) && Array.isArray(body.criteria) && body.criteria.length <= 100
    && text(body.origin_kind) && new Set(body.criteria.map(row => row.criteria_item_id)).size === body.criteria.length
    && body.criteria.every(row => id(row.criteria_item_id) && nullable(row.id, text) && text(row.text) && state(row.type, ['information', 'visual']) && nullable(row.source, text) && positive(row.position) && nullable(row.is_required, value => typeof value === 'boolean')));
  return body;
}
export function receipt(body) {
  requireValue(id(body.id) && id(body.project_id) && id(body.image_id) && id(body.criteria_version_id)
    && state(body.status, ['queued', 'running', 'completed', 'failed', 'imported']) && nullable(body.retry_of, id));
  return body;
}
export function review(body) {
  receipt(body); image(body.image);
  requireValue(body.image.id === body.image_id && body.image.project_id === body.project_id && typeof body.results_complete === 'boolean'
    && nullable(body.overall_verdict, value => state(value, ['pass', 'fail', 'review'])) && nullable(body.progress, value => integer(value) && value <= 100)
    && Array.isArray(body.results) && body.results.length <= 100 && Array.isArray(body.criteria) && text(body.stage) && text(body.origin_kind) && positive(body.criteria_version)
    && new Set(body.results.map(row => row.result_id)).size === body.results.length
    && body.results.every(row => id(row.result_id) && id(row.criteria_item_id) && text(row.criterion) && integer(row.evidence_count)
      && Array.isArray(row.evidence) && typeof row.evidence_complete === 'boolean'
      && state(row.processing_status, ['completed', 'failed', 'unsupported', 'imported'])
      && (row.processing_status === 'completed' ? state(row.verdict, ['pass', 'fail', 'review']) : row.verdict === null)));
  return body;
}
export function list(body, validator) {
  requireValue(Array.isArray(body.items) && body.items.length <= 100 && typeof body.has_more === 'boolean'
    && (body.has_more ? text(body.next_cursor) && body.next_cursor.length > 0 && body.next_cursor.length <= 2048 : body.next_cursor === null));
  body.items.forEach(validator); requireValue(new Set(body.items.map(row => row.id)).size === body.items.length); return body;
}
export function evidence(row) {
  requireValue(id(row.id) && nullable(row.frame_id, id) && nullable(row.confidence, value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1)
    && nullable(row.coordinate_space, text) && nullable(row.bbox_xyxy, value => Array.isArray(value) && value.length === 4 && value.every(Number.isFinite))
    && Array.isArray(row.token_ids) && row.token_ids.every(id) && nullable(row.text, text));
  return row;
}
export function ocr(row) { requireValue(id(row.id) && text(row.text) && id(row.frame_id) && state(row.external_token_type, ['integer', 'string']) && nullable(row.confidence, value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1)); return row; }
export function sameId(value, identifier) { requireValue(value.id === identifier); return value; }
export function sameProject(value, projectId) { requireValue(value.project_id === projectId); return value; }
export function codepoints(value) { return Array.from(value).length; }
export function editable(project) { return project.business_state === 'active' && ['owner', 'editor'].includes(project.my_role); }
export function content(draft) { return { raw_requirements: draft.raw_requirements, items: draft.items.map(row => ({ id: row.id, text: row.text, type: row.type, source: row.source, selected: row.selected })), source_documents: draft.source_documents }; }
export function contentError(value, confirming = false) {
  if (!value.raw_requirements.trim() || codepoints(value.raw_requirements) > 20000) return '요구사항은 1~20,000자로 입력해 주세요.';
  if (value.items.length > 100 || new Set(value.items.map(row => row.id)).size !== value.items.length) return '항목은 고유한 ID로 최대 100개까지 저장할 수 있습니다.';
  if (value.items.some(row => !item(row) || !row.id.trim() || codepoints(row.id) > 100 || codepoints(row.text) > 500 || !row.source.trim() || codepoints(row.source) > 500)) return '각 항목의 문구와 출처는 500자 이하여야 합니다.';
  const selected = value.items.filter(row => row.selected);
  if (confirming && (!selected.length || selected.some(row => !row.text.trim()))) return '확정할 항목을 선택하고 빈 문구를 채워 주세요.';
  return '';
}
export function boxStyle(row, image) {
  const box = row.bbox_xyxy;
  if (row.frame_id !== image.frame_id || row.coordinate_space !== image.coordinate_space || !Array.isArray(box) || box.length !== 4
    || !box.every(Number.isFinite) || box[0] < 0 || box[1] < 0 || box[2] > image.width || box[3] > image.height || box[0] >= box[2] || box[1] >= box[3]) return null;
  return { left: `${box[0] / image.width * 100}%`, top: `${box[1] / image.height * 100}%`, width: `${(box[2] - box[0]) / image.width * 100}%`, height: `${(box[3] - box[1]) / image.height * 100}%` };
}
const root = /^\/(?:projects|review\/new)$/;
const projectRoute = /^\/projects\/([0-9a-f-]{36})\/(?:review\/new|drafts|reviews|(?:drafts|criteria|reviews)\/[0-9a-f-]{36})$/;
export function allowedPath(path) { if (root.test(path)) return true; const match = projectRoute.exec(path); return !!match && path.split('/').filter(part => part.length === 36).every(id); }
export function destination(from) {
  if (!object(from) || !text(from.pathname) || !allowedPath(from.pathname)) return '/projects';
  const search = text(from.search) && from.search.startsWith('?') && !/[\u0000-\u001f#]/.test(from.search) ? from.search : '';
  return from.pathname + search;
}
