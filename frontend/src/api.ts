import type { ListResponse } from './types';

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string) {
    super(message); this.name = 'ApiError';
  }
}

let csrfToken = '';
export function setCsrfToken(value: string) { csrfToken = value; }
export const fileUrl = (id: string) => `/api/files/${encodeURIComponent(id)}`;
const segment = (value: string) => encodeURIComponent(value);
export const projectUrl = (id: string, resource = '') => `/projects/${segment(id)}${resource ? `/${resource}` : ''}`;

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  const method = (options.method ?? 'GET').toUpperCase();
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && csrfToken) headers.set('X-CSRF-Token', csrfToken);
  headers.set('Accept', 'application/json');
  let response: Response;
  try { response = await fetch(`/api${path}`, { ...options, method, headers, credentials: 'include', cache: 'no-store' }); }
  catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(0, 'network_error', '서버에 연결할 수 없습니다. 연결 상태를 확인한 뒤 다시 시도해 주세요.');
  }
  const raw = await response.text();
  let body: unknown = null;
  try { body = raw ? JSON.parse(raw) : null; }
  catch { if (response.ok) throw new ApiError(response.status, 'invalid_response', '서버 응답 형식이 올바르지 않습니다.'); }
  if (!response.ok) {
    const envelope = body as { error?: { code?: string; message?: string }; request_id?: string; detail?: unknown } | null;
    const defaultMessage: Record<number, string> = {
      401: '로그인이 필요합니다. 다시 로그인해 주세요.', 403: '이 작업을 수행할 권한이 없습니다.',
      404: '대상을 찾을 수 없거나 접근할 수 없습니다.', 409: '상태가 변경되어 요청과 충돌했습니다. 새로 조회해 주세요.',
      413: '파일 크기 제한을 초과했습니다.', 422: '입력한 값을 확인해 주세요.',
      501: '서버가 아직 이 기능을 지원하지 않습니다.', 503: '서비스가 준비되지 않았습니다. 잠시 후 다시 시도해 주세요.',
    };
    if (response.status === 401 && path !== '/auth/login') window.dispatchEvent(new Event('cc:session-expired'));
    throw new ApiError(response.status, envelope?.error?.code ?? `http_${response.status}`,
      envelope?.error?.message ?? defaultMessage[response.status] ?? `서버 요청을 처리하지 못했습니다. (HTTP ${response.status})`, envelope?.request_id);
  }
  return body as T;
}

export async function list<T>(path: string, signal?: AbortSignal): Promise<T[]> {
  const result = await api<ListResponse<T>>(path, { signal });
  if (!result || !Array.isArray(result.items)) throw new ApiError(200, 'invalid_list', '목록 응답에 items가 없습니다. 서버 응답을 확인해야 합니다.');
  return result.items;
}
export const post = <T,>(path: string, body?: unknown, headers?: HeadersInit) => api<T>(path, {
  method: 'POST', headers, body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
});
export const put = <T,>(path: string, body: unknown) => api<T>(path, { method: 'PUT', body: JSON.stringify(body) });

// 동일 입력의 불확실한 네트워크 재전송에는 같은 키를 유지한다. 새 입력은 새 키를 사용한다.
export function createIdempotencyKey() { return crypto.randomUUID(); }
