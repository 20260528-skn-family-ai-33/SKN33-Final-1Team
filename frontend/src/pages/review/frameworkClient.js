import { fetchJson, fetchResponse } from '../../api/client.js';

export const frameworkPrefix = '/api/framework/v1';

// 문서 서버에서 발급한 CSRF로 요청한다. 모델 키는 브라우저에 전달하지 않는다.
export async function frameworkResponse(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = new Headers(options.headers);
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = await fetchJson(`${frameworkPrefix}/csrf`, { signal: options.signal, notifyAuthFailure: false });
    if (typeof csrf?.csrf_token !== 'string' || !csrf.csrf_token) throw new Error('PDF 분석의 보안 확인을 준비하지 못했습니다.');
    headers.set('X-CSRFToken', csrf.csrf_token);
  }
  return fetchResponse(frameworkPrefix + path, { ...options, headers: Object.fromEntries(headers), notifyAuthFailure: false });
}

export async function frameworkJson(path, options = {}) {
  const response = await frameworkResponse(path, options);
  return response.json();
}

export async function loadDocumentProjects({ signal } = {}) {
  const health = await frameworkJson('/health', { signal });
  if (!health.requirements_presentation || health.pdf_extraction_mode !== 'qwen') {
    throw new Error('PDF 문단 추출 설정을 적용한 분석 서버가 필요합니다. 서버 실행 설정을 확인해 주세요.');
  }
  const items = [];
  let cursor;
  do {
    const query = new URLSearchParams({ limit: '100', ...(cursor ? { cursor } : {}) });
    const page = await frameworkJson(`/projects?${query}`, { signal });
    if (!Array.isArray(page.items)) throw new Error('저장할 프로젝트 목록을 확인하지 못했습니다.');
    items.push(...page.items);
    cursor = page.next_cursor || null;
    if (items.length > 10000) throw new Error('프로젝트 목록이 너무 많습니다. 목록을 정리한 뒤 다시 시도해 주세요.');
  } while (cursor);
  return items.filter(item => ['owner', 'editor'].includes(item.my_role));
}

export function createDocumentProject(name, { signal } = {}) {
  return frameworkJson('/projects', { method: 'POST', signal,
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
}
