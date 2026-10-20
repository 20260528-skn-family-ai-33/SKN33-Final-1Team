import * as C from './contracts.js';
export class ApiError extends Error {
  constructor(code, status = 0, fields = {}) {
    const messages = { network_error: '서버에 연결하지 못했습니다. 저장 여부를 확인한 뒤 다시 시도해 주세요.', invalid_response: '서버 응답을 확인하지 못했습니다. 이력에서 저장 여부를 확인해 주세요.',
      DRAFT_REVISION_CONFLICT: '다른 곳에서 초안이 수정되었습니다. 최신 내용과 비교해 주세요.', CRITERIA_STALE: '초안이 바뀌었습니다. 최신 초안을 확인하고 다시 확정해 주세요.',
      PROJECT_ARCHIVED: '보관된 프로젝트에서는 새 내용을 저장하거나 실행할 수 없습니다.', IDEMPOTENCY_KEY_REUSED: '이 요청 키에 연결된 내용이 다릅니다. 기존 요청 기록을 확인해 주세요.',
      AUTHENTICATION_REQUIRED: '로그인이 만료되었습니다. 다시 로그인해 주세요.', PROJECT_PERMISSION_DENIED: '이 작업을 수행할 권한이 없습니다.', RESOURCE_NOT_FOUND: '자료를 찾을 수 없거나 접근 권한이 없습니다.',
      REVIEW_RUNTIME_UNAVAILABLE: '검수 실행 준비가 필요합니다. 서버 준비 상태를 확인해 주세요.', FILE_UNAVAILABLE: '저장된 이미지 파일을 불러올 수 없습니다.', INVALID_CREDENTIALS: '이메일 또는 비밀번호를 확인해 주세요.',
      CSRF_FAILED: '보안 확인에 실패했습니다. 다시 시도해 주세요.', EXPORT_TOO_LARGE: '다운로드 가능한 결과 크기를 초과했습니다.' };
    super(messages[code] || (status === 403 ? '요청 권한을 확인해 주세요.' : status === 404 ? '자료를 찾을 수 없습니다.' : status === 429 ? '요청이 많습니다. 잠시 후 다시 시도해 주세요.' : '요청을 처리하지 못했습니다. 저장된 상태를 확인해 주세요.'));
    this.name = 'ApiError'; this.code = code; this.status = status; this.fields = fields;
  }
}
function expired() { if (typeof window !== 'undefined') window.dispatchEvent(new Event('cc:v1:expired')); }
export function apiPath(path) { if (!path.startsWith('/api/v1/') || path.includes('..') || /[\\\u0000-\u001f]/.test(path)) throw new Error('Invalid API path'); return path; }
async function response(path, options = {}) {
  let result;
  try { result = await fetch(apiPath(path), { ...options, credentials: 'same-origin', cache: 'no-store', redirect: 'error' }); }
  catch (error) { if (error.name === 'AbortError') throw error; throw new ApiError('network_error'); }
  if (!result.ok) {
    const value = await result.json().catch(() => null);
    if (result.status === 401) expired();
    throw new ApiError(value?.error?.code || 'http_error', result.status, value?.error?.fields || {});
  }
  return result;
}
export async function request(path, validator, options = {}) {
  const result = await response(path, options);
  if (result.status === 204) { C.requireValue(validator === null); return null; }
  try {
    if (!result.headers.get('content-type')?.toLowerCase().startsWith('application/json')) throw new Error();
    const value = C.envelope(await result.json()); return validator(value);
  } catch (error) { if (error.name === 'AbortError' || error instanceof ApiError) throw error; throw new ApiError('invalid_response', result.status); }
}
export const csrf = signal => request('/api/v1/auth/csrf', C.csrf, { signal });
export const me = signal => request('/api/v1/auth/me', C.session, { signal });
async function write(path, body, validator, { signal, key, method = 'POST', file } = {}) {
  const prepared = await csrf(signal);
  const headers = { 'X-CSRFToken': prepared.csrf_token };
  if (key) { C.requireValue(C.KEY.test(key)); headers['Idempotency-Key'] = key; }
  if (!file) headers['Content-Type'] = 'application/json';
  return request(path, validator, { method, headers, body: file || JSON.stringify(body), signal });
}
export async function login(credentials, signal) {
  const { password, ...identifier } = credentials;
  C.requireValue(typeof password === 'string' && Object.keys(identifier).length === 1 && ['email', 'username'].includes(Object.keys(identifier)[0]));
  await write('/api/v1/auth/login', credentials, body => C.user(body.user), { signal });
  const current = await me(signal); if (!current) throw new ApiError('invalid_response'); return current;
}
export const logout = signal => write('/api/v1/auth/logout', {}, null, { signal });
const scoped = (value, pid) => { if (value.project_id !== pid) throw new ApiError('RESOURCE_NOT_FOUND', 404); return value; };
const uuid = value => { C.requireValue(C.id(value)); return value; };
const query = values => { const params = new URLSearchParams(); for (const [key, value] of Object.entries(values)) if (value !== undefined && value !== null && value !== '') params.set(key, String(value)); return params.size ? '?' + params : ''; };
export const projects = (filters = {}, signal) => request('/api/v1/projects' + query(filters), body => C.list(body, C.project), { signal });
export const project = (pid, signal) => request(`/api/v1/projects/${uuid(pid)}`, body => C.sameId(C.project(body), pid), { signal });
export const createProject = (name, signal) => write('/api/v1/projects', { name }, C.project, { signal });
export const patchProject = (pid, body, signal) => write(`/api/v1/projects/${uuid(pid)}`, body, value => C.sameId(C.project(value), pid), { method: 'PATCH', signal });
export const image = (identifier, pid, signal) => request(`/api/v1/images/${uuid(identifier)}`, body => C.sameId(scoped(C.image(body), pid), identifier), { signal });
export const images = (pid, filters = {}, signal) => request(`/api/v1/projects/${uuid(pid)}/images` + query(filters), body => C.list(body, value => scoped(C.image(value), pid)), { signal });
export const sets = (pid, filters = {}, signal) => request(`/api/v1/projects/${uuid(pid)}/criteria-sets` + query(filters), body => C.list(body, value => scoped(C.set(value), pid)), { signal });
export const createSet = (pid, name, signal) => write(`/api/v1/projects/${uuid(pid)}/criteria-sets`, { name }, body => scoped(C.set(body), pid), { signal });
export const drafts = (pid, filters = {}, signal) => request(`/api/v1/projects/${uuid(pid)}/criteria-drafts` + query(filters), body => C.list(body, value => scoped(C.draft(value), pid)), { signal });
export const draft = (pid, identifier, signal) => request(`/api/v1/projects/${uuid(pid)}/criteria-drafts/${uuid(identifier)}`, body => C.sameId(scoped(C.draft(body), pid), identifier), { signal });
export const createDraft = (pid, value, signal) => write(`/api/v1/projects/${uuid(pid)}/criteria-drafts`, value, body => scoped(C.draft(body), pid), { signal });
export const putDraft = (pid, identifier, value, signal) => write(`/api/v1/projects/${uuid(pid)}/criteria-drafts/${uuid(identifier)}`, value, body => C.sameId(scoped(C.draft(body), pid), identifier), { method: 'PUT', signal });
export const criteria = (pid, identifier, signal) => request(`/api/v1/projects/${uuid(pid)}/criteria-versions/${uuid(identifier)}`, body => C.sameId(scoped(C.criteria(body), pid), identifier), { signal });
export const versions = (pid, filters = {}, signal) => request(`/api/v1/projects/${uuid(pid)}/criteria-versions` + query(filters), body => C.list(body, value => scoped(C.criteria(value), pid)), { signal });
export async function upload(pid, file, signal) {
  if (!file || file.size <= 0 || file.size > 20 * 1024 * 1024) throw new ApiError('PAYLOAD_TOO_LARGE', 413);
  const form = new FormData(); form.append('image', file);
  return write(`/api/v1/projects/${uuid(pid)}/images`, null, body => scoped(C.image(body), pid), { file: form, signal });
}
export const review = (pid, identifier, signal) => request(`/api/v1/reviews/${uuid(identifier)}`, body => C.sameId(scoped(C.review(body), pid), identifier), { signal });
export const reviews = (pid, filters = {}, signal) => request(`/api/v1/projects/${uuid(pid)}/reviews` + query(filters), body => C.list(body, value => scoped(C.receipt(value), pid)), { signal });
export const evidence = (run, result, cursor, signal) => request(`/api/v1/reviews/${uuid(run)}/results/${uuid(result)}/evidence` + query({ cursor }), body => { C.requireValue(C.integer(body.evidence_count)); return C.list(body, C.evidence); }, { signal });
export const ocr = (run, cursor, signal) => request(`/api/v1/reviews/${uuid(run)}/ocr-records` + query({ cursor }), body => { C.requireValue(['pending', 'none', 'available'].includes(body.availability) && C.integer(body.record_count)); return C.list(body, C.ocr); }, { signal });
export const health = signal => request('/api/v1/health', body => { C.requireValue(C.object(body.checks)); return body; }, { signal });
export function sendPending(entry, signal) {
  const options = { key: entry.key, signal };
  if (entry.operation === 'confirm_criteria') return write(`/api/v1/projects/${uuid(entry.project_id)}/criteria-versions`, entry.body, body => { scoped(C.criteria(body), entry.project_id); C.requireValue(body.source_draft_id === entry.body.draft_id && body.source_draft_revision === entry.body.expected_revision); return body; }, options);
  if (entry.operation === 'create_review') return write('/api/v1/reviews', entry.body, body => { scoped(C.receipt(body), entry.project_id); C.requireValue(body.image_id === entry.body.image_id && body.criteria_version_id === entry.body.criteria_version_id); return body; }, options);
  if (entry.operation === 'retry_review') return write(`/api/v1/reviews/${uuid(entry.source_run_id)}/retry`, entry.body, body => { scoped(C.receipt(body), entry.project_id); C.requireValue(body.retry_of === entry.source_run_id); return body; }, options);
  throw new Error('Unknown operation');
}
export async function download(pid, run, signal) {
  const result = await response(`/api/v1/reviews/${uuid(run)}/export`, { signal });
  if (!result.headers.get('content-type')?.startsWith('application/json') || !result.body?.getReader) throw new ApiError('invalid_response');
  const reader = result.body.getReader(); const chunks = []; let size = 0;
  try {
    for (;;) { const { value, done } = await reader.read(); if (done) break; size += value.byteLength; if (size > 50 * 1024 * 1024) throw new ApiError('EXPORT_TOO_LARGE', 422); chunks.push(value); }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  const blob = new Blob(chunks, { type: 'application/json' });
  try { const value = JSON.parse(await blob.text()); C.requireValue(value.contract_version === 'cc-review-export-v1' && value.id === run && value.project_id === pid); }
  catch { throw new ApiError('invalid_response'); }
  return blob;
}
