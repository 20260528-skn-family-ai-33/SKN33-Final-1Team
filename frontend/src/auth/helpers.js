const anonymousCodes = new Set(['unauthenticated', 'not_authenticated', 'authentication_required', 'session_expired']);

// 서버 오류 코드의 표기 차이를 정규화해 오류 종류를 비교한다.
function normalizedCode(error) {
  return String(error?.code || '').toLowerCase();
}

// 인증 정보가 없다고 확인된 응답만 익명 상태로 전환한다.
export function isAnonymousError(error) {
  return error?.status === 401 || (error?.status === 403 && anonymousCodes.has(normalizedCode(error)));
}

// 외부 주소나 등록되지 않은 페이지를 로그인 복귀 경로로 사용하지 않는다.
const loginReturnPaths = new Set(['/review/new', '/video']);
export function loginDestination(from) {
  if (!from || typeof from !== 'object' || !loginReturnPaths.has(from.pathname)) return '/review/new';
  const search = typeof from.search === 'string' && from.search.startsWith('?') ? from.search : '';
  const hash = typeof from.hash === 'string' && from.hash.startsWith('#') ? from.hash : '';
  return `${from.pathname}${search}${hash}`;
}

// 로그인 실패, CSRF 검증 실패, 연결 장애를 서로 다른 안내로 표시한다.
export function authErrorMessage(error, action = 'login') {
  const code = normalizedCode(error);
  if (code.includes('csrf')) return '보안 확인이 만료되었거나 실패했습니다. 다시 시도해 주세요.';
  if (['invalid_credentials', 'authentication_failed'].includes(code) || (action === 'login' && error?.status === 401)) {
    return '아이디 또는 비밀번호를 확인해 주세요.';
  }
  if (error?.status === 429) return '요청이 많습니다. 잠시 기다린 뒤 다시 시도해 주세요.';
  if (error?.status === 0 || code === 'network_error') return '로그인 서버에 연결하지 못했습니다. 연결 상태를 확인하고 다시 시도해 주세요.';
  if ([404, 405, 501].includes(error?.status)) return '현재 로그인 서비스를 이용할 수 없습니다. 잠시 후 다시 확인해 주세요.';
  if (error?.status >= 500 || code === 'invalid_response') return '로그인 서버 응답을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.';
  if (error?.status === 403) return '로그인 요청이 거절되었습니다. 계정의 이용 권한을 확인해 주세요.';
  return error?.message || '요청을 처리하지 못했습니다. 다시 시도해 주세요.';
}

// 명시된 로그인 상태와 사용자 객체만 신뢰하고 예상하지 못한 응답은 거절한다.
export function sessionUser(body) {
  if (body?.authenticated === false || (body?.authenticated !== true && body?.user === null)) return null;
  if (body?.user && typeof body.user === 'object' && !Array.isArray(body.user) && body.user.id != null) return body.user;
  throw new Error('invalid_session_response');
}

// CSRF 응답에 토큰이 없으면 Django 기본 쿠키의 값을 확인한다.
export function csrfToken(body, cookie = '') {
  if (typeof body?.csrf_token === 'string' && body.csrf_token) return body.csrf_token;
  const value = cookie.split(';').map(part => part.trim()).find(part => part.startsWith('csrftoken='));
  if (!value) return '';
  try { return decodeURIComponent(value.slice('csrftoken='.length)); }
  catch { return ''; }
}
