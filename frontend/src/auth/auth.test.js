import assert from 'node:assert/strict';
import test from 'node:test';
import { authErrorMessage, csrfToken, isAnonymousError, loginDestination, sessionUser } from './helpers.js';
import { ApiError, fetchJson } from '../api/client.js';
import { getSession, loginSession, logoutSession } from '../api/auth.js';

// 응답 목록을 순서대로 소비해 실제 인증 서버 없이 요청 계약을 확인한다.
function mockRequests(context, responses) {
  const requests = [];
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options });
    const response = responses.shift();
    if (response instanceof Error) throw response;
    assert.ok(response, `예상하지 못한 요청: ${url}`);
    return new Response(response.status === 204 ? null : JSON.stringify(response.body), { status: response.status || 200, headers: { 'Content-Type': 'application/json' } });
  });
  return requests;
}

test('외부 주소와 허용되지 않은 경로는 로그인 복귀에 사용하지 않는다', () => {
  assert.equal(loginDestination({ pathname: '/review/new', search: '?sample=1', hash: '#result' }), '/review/new?sample=1#result');
  assert.equal(loginDestination({ pathname: '/video', search: '?id=abc' }), '/video?id=abc');
  for (const from of ['https://example.com', { pathname: '//example.com' }, { pathname: '/login' }, null]) {
    assert.equal(loginDestination(from), '/review/new');
  }
});

test('세션 만료와 일반 권한·CSRF·통신 오류를 구분한다', () => {
  assert.equal(isAnonymousError({ status: 401 }), true);
  assert.equal(isAnonymousError({ status: 403, code: 'NOT_AUTHENTICATED' }), true);
  for (const error of [{ status: 403, code: 'permission_denied' }, { status: 403, code: 'csrf_failed' }, { status: 500, code: 'session_expired' }, { status: 404 }, { status: 0 }]) {
    assert.equal(isAnonymousError(error), false);
  }
  assert.match(authErrorMessage({ code: 'CSRF_FAILED', status: 403 }), /보안 확인/);
  assert.match(authErrorMessage({ code: 'invalid_credentials', status: 403 }), /아이디 또는 비밀번호/);
  assert.match(authErrorMessage({ status: 0 }), /서버에 연결/);
});

test('명시적 익명과 확인된 사용자만 세션 응답으로 처리한다', () => {
  const user = { id: 'u1', username: 'tester' };
  assert.equal(sessionUser({ user }), user);
  assert.equal(sessionUser({ authenticated: false, user: null }), null);
  for (const body of [{}, { user: 'tester' }, { user: {} }, { authenticated: true, user: null }]) {
    assert.throws(() => sessionUser(body));
  }
});

test('CSRF 응답 토큰과 표준 쿠키를 읽되 손상된 쿠키는 거절한다', () => {
  assert.equal(csrfToken({ csrf_token: 'response-token' }, 'csrftoken=cookie-token'), 'response-token');
  assert.equal(csrfToken({}, 'other=a; csrftoken=cookie%2Dtoken; test=b'), 'cookie-token');
  assert.equal(csrfToken({}, 'csrftoken=%'), '');
});

test('Django 오류 코드를 보존하고 FastAPI 문자열 오류도 전달한다', async context => {
  mockRequests(context, [
    { status: 403, body: { error: { code: 'CSRF_FAILED', message: 'csrf rejected', fields: { username: ['required'] } } } },
    { status: 422, body: { detail: '이미지 형식 오류' } },
  ]);
  await assert.rejects(fetchJson('/test'), error => error instanceof ApiError && error.status === 403 && error.code === 'CSRF_FAILED' && error.fields.username[0] === 'required');
  await assert.rejects(fetchJson('/test'), /이미지 형식 오류/);
});

test('401은 익명으로 처리하고 미구현·통신 오류는 그대로 전달한다', async context => {
  mockRequests(context, [
    { status: 401, body: { detail: 'not authenticated' } },
    { status: 404, body: { detail: 'not found' } },
    new TypeError('offline'),
  ]);
  assert.equal(await getSession(), null);
  await assert.rejects(getSession(), error => error.status === 404);
  await assert.rejects(getSession(), error => error.code === 'network_error');
});

test('로그인은 CSRF 확인 후 자격 증명을 제출하고 me의 사용자로 완료한다', async context => {
  const user = { id: 'u1', username: 'tester' };
  const requests = mockRequests(context, [
    { body: { csrf_token: 'token' } }, { status: 204 }, { body: { authenticated: true, user } },
  ]);
  assert.deepEqual(await loginSession({ username: 'tester', password: 'secret' }), user);
  assert.deepEqual(requests.map(request => request.url), ['/api/v1/auth/csrf', '/api/v1/auth/login', '/api/v1/auth/me']);
  assert.equal(requests[1].options.headers['X-CSRFToken'], 'token');
  assert.deepEqual(JSON.parse(requests[1].options.body), { username: 'tester', password: 'secret' });
  assert.ok(requests.every(request => request.options.credentials === 'same-origin'));
  assert.ok(requests.every(request => !('notifyAuthFailure' in request.options)));
});

test('CSRF 토큰 누락과 로그인 후 익명 세션은 성공으로 처리하지 않는다', async context => {
  const requests = mockRequests(context, [
    { body: {} },
    { body: { csrf_token: 'token' } }, { status: 204 }, { body: { authenticated: false, user: null } },
  ]);
  await assert.rejects(loginSession({ username: 'tester', password: 'secret' }), error => error.code === 'csrf_token_missing');
  assert.equal(requests.length, 1);
  await assert.rejects(loginSession({ username: 'tester', password: 'secret' }), error => error.code === 'session_not_established');
});

test('로그아웃은 최신 CSRF 토큰을 사용하고 204 응답을 허용한다', async context => {
  const requests = mockRequests(context, [{ body: { csrf_token: 'rotated-token' } }, { status: 204 }]);
  await logoutSession();
  assert.equal(requests[1].url, '/api/v1/auth/logout');
  assert.equal(requests[1].options.headers['X-CSRFToken'], 'rotated-token');
});

test('업무 API의 명시적 세션 만료만 알리고 인증 요청과 일반 403은 제외한다', async context => {
  const originalWindow = globalThis.window;
  const events = [];
  globalThis.window = { dispatchEvent: event => events.push(event.type) };
  context.after(() => {
    if (originalWindow === undefined) delete globalThis.window;
    else globalThis.window = originalWindow;
  });
  mockRequests(context, [
    { status: 403, body: { error: { code: 'SESSION_EXPIRED' } } },
    { status: 401, body: { detail: 'invalid credentials' } },
    { status: 403, body: { error: { code: 'permission_denied' } } },
  ]);
  await assert.rejects(fetchJson('/api/reviews/1'));
  await assert.rejects(fetchJson('/api/v1/auth/login', { notifyAuthFailure: false }));
  await assert.rejects(fetchJson('/api/reviews/1'));
  assert.deepEqual(events, ['baro:session-expired']);
});
