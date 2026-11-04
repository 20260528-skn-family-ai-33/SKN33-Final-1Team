import { ApiError, fetchJson } from './client.js';
import { csrfToken, isAnonymousError, sessionUser } from '../auth/helpers.js';

const authPath = '/api/v1/auth';

// 현재 세션을 서버에서 확인하며 연결 장애를 미로그인으로 처리하지 않는다.
export async function getSession() {
  let body;
  try {
    body = await fetchJson(`${authPath}/me`, { notifyAuthFailure: false });
  } catch (error) {
    if (isAnonymousError(error)) return null;
    throw error;
  }
  try {
    return sessionUser(body);
  } catch {
    throw new ApiError('로그인 상태를 확인하지 못했습니다. 다시 시도해 주세요.', {
      status: 200,
      code: 'invalid_response',
    });
  }
}

// 로그인에도 새 CSRF 토큰을 준비하고 인증 정보는 요청 본문으로만 전송한다.
async function postAuth(action, body) {
  const csrf = await fetchJson(`${authPath}/csrf`, { notifyAuthFailure: false });
  const token = csrfToken(csrf, typeof document === 'undefined' ? '' : document.cookie);
  if (!token) {
    throw new ApiError('보안 확인을 준비하지 못했습니다. 다시 시도해 주세요.', { code: 'csrf_token_missing' });
  }
  return fetchJson(`${authPath}/${action}`, {
    notifyAuthFailure: false,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
    body: JSON.stringify(body),
  });
}

// 로그인 응답 이후에도 서버 세션의 사용자를 확인해야 진입을 허용한다.
export async function loginSession({ username, password }) {
  const identifier = username.trim();
  await postAuth('login', identifier.includes('@') ? { email: identifier, password } : { username: identifier, password });
  const user = await getSession();
  if (!user) {
    throw new ApiError('로그인 후 세션을 확인하지 못했습니다. 다시 시도해 주세요.', { status: 200, code: 'session_not_established' });
  }
  return user;
}

// 가입은 기존 PostgreSQL 계정만 생성하고 로그인 세션은 별도 로그인에서 발급한다.
export async function registerAccount({ email, displayName, password }) {
  const body = await postAuth('signup', { email: email.trim(), display_name: displayName.trim(), password });
  const user = sessionUser(body);
  if (!user) throw new ApiError('회원가입 결과를 확인하지 못했습니다.', { status: 201, code: 'invalid_response' });
  return user;
}

// 서버의 로그아웃이 성공한 뒤에만 화면의 로그인 상태를 해제한다.
export async function logoutSession() {
  await postAuth('logout', {});
}
