import { isAnonymousError } from '../auth/helpers.js';

// HTTP 상태와 서버 오류 코드를 함께 보관해 화면에서 실패 원인을 구분한다.
export class ApiError extends Error {
  constructor(message, { status = 0, code = 'network_error', fields = {} } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

// 새 Django 오류와 기존 FastAPI detail 응답을 같은 오류 형식으로 읽는다.
function responseError(body, status) {
  const detail = body?.error ?? body?.detail;
  const message = typeof detail === 'string' ? detail : Array.isArray(detail)
    ? detail.map(item => item.msg || String(item)).join(' ')
    : detail?.message;
  return new ApiError(message || `요청을 처리하지 못했습니다. (${status})`, {
    status,
    code: typeof detail?.code === 'string' ? detail.code : 'http_error',
    fields: detail?.fields || {},
  });
}

// 같은 출처의 세션 쿠키를 전송하고 통신 오류와 HTTP 오류를 구분한다.
export async function fetchResponse(url, options = {}) {
  const { notifyAuthFailure = true, ...requestOptions } = options;
  let response;
  try {
    const method = (requestOptions.method || 'GET').toUpperCase();
    const headers = new Headers(requestOptions.headers);
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && !headers.has('X-CSRFToken')) {
      const csrfResponse = await fetch('/api/v1/auth/csrf', { credentials: 'same-origin', signal: requestOptions.signal });
      const csrf = await csrfResponse.json();
      if (!csrfResponse.ok || typeof csrf.csrf_token !== 'string') {
        throw new ApiError('보안 확인을 준비하지 못했습니다. 다시 시도해 주세요.', { status: csrfResponse.status });
      }
      requestOptions.headers = { ...Object.fromEntries(headers), 'X-CSRFToken': csrf.csrf_token };
    }
    response = await fetch(url, { ...requestOptions, credentials: 'same-origin' });
  } catch (error) {
    if (error.name === 'AbortError' || error instanceof ApiError) throw error;
    throw new ApiError('서버에 연결하지 못했습니다. 연결 상태를 확인하고 다시 시도해 주세요.');
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const error = responseError(body, response.status);
    // 업무 API가 명시적으로 세션 만료를 알리면 보호 화면의 로그인 상태도 갱신한다.
    if (notifyAuthFailure && isAnonymousError(error) && typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('baro:session-expired'));
    }
    throw error;
  }
  return response;
}

export async function fetchJson(url, options = {}) {
  const response = await fetchResponse(url, options);
  if (response.status === 204) return null;
  const body = await response.json().catch(() => null);
  if (body === null) {
    throw new ApiError('서버 응답을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.', {
      status: response.status,
      code: 'invalid_response',
    });
  }
  return body;
}
