import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { after, before, test } from 'node:test';

// 별도 설치된 Playwright 또는 NODE_PATH의 도구를 사용하며 실행 중 다운로드하지 않는다.
const { chromium } = createRequire(import.meta.url)('playwright');
const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:8123';
const origin = new URL(baseURL).origin;
const user = { id: 'test-user', username: 'tester', display_name: '검증 사용자' };
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYPgPAAEDAQAIicLsAAAAAElFTkSuQmCC', 'base64');
let browser;

before(async () => {
  // 개인 브라우저 세션 대신 격리된 임시 프로필을 실행한다.
  browser = await chromium.launch({
    headless: true,
    ...(process.env.E2E_CHROME_PATH
      ? { executablePath: process.env.E2E_CHROME_PATH }
      : { channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' }),
  });
});

after(async () => { await browser?.close(); });

// 인증·OCR 응답을 브라우저 안에서만 대체해 실제 계정이나 OCR 실행 없이 흐름을 검증한다.
async function fixture(t, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  page.setDefaultNavigationTimeout(8000);
  const state = {
    authenticated: false, meStatus: 200, meCode: '', csrfStatus: 200,
    loginStatus: 200, loginCode: 'invalid_credentials', establishSession: true,
    logoutStatus: 204, healthStatus: 200, loginDelay: 0, requests: [], unexpected: [], ...options,
  };
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));

  // 모르는 API 요청은 즉시 차단하여 실제 서버 작업이 발생하지 않게 한다.
  await context.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) return route.abort();
    const path = url.pathname;
    if (!path.startsWith('/api/')) return route.continue();
    state.requests.push({ path, method: request.method(), headers: request.headers(), body: request.postData() });
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/auth/me') {
      if (state.meStatus === 0) return route.abort('connectionrefused');
      if (state.meStatus !== 200) return json({ error: { code: state.meCode, message: '세션 확인 실패' } }, state.meStatus);
      return json({ authenticated: state.authenticated, user: state.authenticated ? user : null });
    }
    if (path === '/api/v1/auth/csrf') {
      if (state.csrfStatus === 0) return route.abort('connectionrefused');
      if (state.csrfStatus !== 200) return json({ error: { code: 'csrf_failed', message: 'CSRF 확인 실패' } }, state.csrfStatus);
      return json({ csrf_token: 'test-csrf-token' });
    }
    if (path === '/api/v1/auth/login') {
      if (state.loginDelay) await new Promise(resolve => setTimeout(resolve, state.loginDelay));
      if (state.loginStatus !== 200) return json({ error: { code: state.loginCode, message: '인증 실패' } }, state.loginStatus);
      state.authenticated = state.establishSession;
      return json({ user });
    }
    if (path === '/api/v1/auth/logout') {
      if (state.logoutStatus !== 204) return json({ error: { code: 'unavailable', message: '로그아웃 실패' } }, state.logoutStatus);
      state.authenticated = false;
      return route.fulfill({ status: 204 });
    }
    if (path === '/api/health') {
      if (state.healthStatus !== 200) {
        state.authenticated = false;
        return json({ error: { code: 'session_expired', message: '세션이 만료되었습니다.' } }, state.healthStatus);
      }
      return json({ runtime: { ready: true, engine: 'easyocr' }, limits: {} });
    }
    if (path === '/api/sample') return json({ project_name: '샘플 프로젝트', requirements: '행사명: 테스트 축제' });
    if (path === '/api/sample/image') return route.fulfill({ contentType: 'image/png', body: png });
    if (path === '/api/reviews' && request.method() === 'POST') return json({ id: 'test-review', status: 'queued' }, 202);
    if (path === '/api/reviews/test-review/image') return route.fulfill({ contentType: 'image/png', body: png });
    if (path === '/api/reviews/test-review') return json({
      id: 'test-review', status: 'completed', stage: 'completed', progress: 100,
      project_name: '화면 통합 검증', criteria_version: 1,
      image: { url: '/api/reviews/test-review/image', width: 1, height: 1 },
      results: [{ criterion_id: 'criterion-1', criterion: '행사명: 테스트 축제', type: 'information', status: 'pass',
        reason: '이미지 문구에서 행사명을 확인했습니다.', observed_text: '테스트 축제',
        evidence: [{ text: '테스트 축제', bbox_xyxy: [0, 0, 1, 1] }] }],
    });
    state.unexpected.push(`${request.method()} ${path}`);
    return route.abort();
  });

  t.after(async () => {
    await context.close();
    assert.deepEqual(pageErrors, [], '브라우저에서 처리되지 않은 오류가 없어야 한다.');
    assert.deepEqual(state.unexpected, [], '명시한 fixture 외의 API 호출은 없어야 한다.');
  });
  return { page, state };
}

// 사용자 입력으로만 로그인 폼을 제출하고 비밀번호는 테스트 값만 사용한다.
async function submitLogin(page) {
  await page.getByLabel('아이디', { exact: true }).fill(' tester ');
  await page.getByLabel('비밀번호', { exact: true }).fill('test-password');
  await page.getByRole('button', { name: '로그인', exact: true }).click();
}

// 요청된 경우에만 검토용 이미지를 별도 출력 폴더에 저장한다.
async function screenshot(page, filename) {
  if (!process.env.E2E_SCREENSHOT_DIR) return;
  await mkdir(process.env.E2E_SCREENSHOT_DIR, { recursive: true });
  await page.screenshot({ path: join(process.env.E2E_SCREENSHOT_DIR, filename), fullPage: true });
}

test('로그인 직접 접근·새로고침·필수 입력·비밀번호 표시·모바일 배치', async t => {
  const { page, state } = await fixture(t);
  const response = await page.goto(`${baseURL}/login`);
  assert.equal(response.status(), 200);
  await page.getByRole('button', { name: '로그인', exact: true }).waitFor();
  await page.reload();
  await page.getByRole('button', { name: '로그인', exact: true }).click();
  await page.getByText('아이디를 입력해 주세요.', { exact: true }).waitFor();
  await page.getByLabel('아이디', { exact: true }).fill('tester');
  await page.getByRole('button', { name: '로그인', exact: true }).click();
  await page.getByText('비밀번호를 입력해 주세요.', { exact: true }).waitFor();
  assert.equal(state.requests.filter(request => request.path.endsWith('/login')).length, 0);
  await page.getByRole('button', { name: '비밀번호 표시', exact: true }).click();
  assert.equal(await page.getByLabel('비밀번호', { exact: true }).getAttribute('type'), 'text');
  await page.getByRole('button', { name: '비밀번호 숨기기', exact: true }).click();
  assert.equal(await page.getByLabel('비밀번호', { exact: true }).getAttribute('type'), 'password');
  await page.getByLabel('아이디', { exact: true }).fill('');
  await screenshot(page, 'login-desktop.png');
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  await screenshot(page, 'login-mobile.png');
});

test('진입 제어·CSRF·로그인·중복 제출 방지·원래 경로 복귀·로그아웃', async t => {
  const { page, state } = await fixture(t, { loginDelay: 250 });
  await page.goto(`${baseURL}/review/new?source=login#input`);
  await page.waitForURL('**/login');
  assert.equal(await page.getByRole('heading', { name: '어떤 광고물을 검수할까요?' }).count(), 0);
  await submitLogin(page);
  await page.locator('form.auth-form').evaluate(form => { form.requestSubmit(); form.requestSubmit(); });
  await page.waitForURL('**/review/new?source=login#input');
  await page.getByRole('heading', { name: '어떤 광고물을 검수할까요?' }).waitFor();
  const loginRequests = state.requests.filter(request => request.path.endsWith('/login'));
  assert.equal(loginRequests.length, 1);
  assert.deepEqual(JSON.parse(loginRequests[0].body), { username: 'tester', password: 'test-password' });
  assert.equal(loginRequests[0].headers['x-csrftoken'], 'test-csrf-token');
  assert.ok(state.requests.findIndex(request => request.path.endsWith('/csrf')) < state.requests.findIndex(request => request.path.endsWith('/login')));
  assert.deepEqual(await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage) })), { local: [], session: [] });
  await page.getByRole('button', { name: '로그아웃', exact: true }).click();
  await page.waitForURL('**/login');
  assert.equal(state.authenticated, false);
  const logout = state.requests.find(request => request.path.endsWith('/logout'));
  assert.equal(logout.headers['x-csrftoken'], 'test-csrf-token');
  assert.deepEqual(JSON.parse(logout.body), {});
});

test('기존 서버 세션은 로그인 화면을 건너뛰고 새로고침 후 복원된다', async t => {
  const { page, state } = await fixture(t, { authenticated: true });
  await page.goto(`${baseURL}/login`);
  await page.waitForURL('**/review/new');
  await page.reload();
  await page.getByRole('heading', { name: '어떤 광고물을 검수할까요?' }).waitFor();
  assert.equal(state.requests.filter(request => request.path.endsWith('/login')).length, 0);
});

test('로그인 요청 중 서비스 소개로 이동하면 늦은 응답이 페이지를 되돌리지 않는다', async t => {
  const { page } = await fixture(t, { loginDelay: 500 });
  await page.goto(`${baseURL}/login`);
  await submitLogin(page);
  const sessionResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/auth/me');
  await page.getByRole('link', { name: '서비스 소개로 돌아가기' }).click();
  await sessionResponse;
  // 세션 갱신 뒤 React 화면 반영까지 기다려 늦은 navigate 호출을 확인한다.
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(new URL(page.url()).pathname, '/');
  await page.getByRole('region', { name: '서비스 소개', exact: true }).waitFor();
});

test('샘플 요청은 로그인 복귀 뒤 한 번만 읽고 쿼리를 소비한다', async t => {
  const { page, state } = await fixture(t);
  await page.goto(`${baseURL}/review/new?sample=1`);
  await page.waitForURL('**/login');
  await submitLogin(page);
  await page.waitForURL('**/review/new');
  await page.getByRole('img', { name: '업로드한 검수 이미지 미리보기' }).waitFor();
  assert.equal(await page.getByLabel('1. 프로젝트 이름').inputValue(), '샘플 프로젝트');
  assert.equal(await page.getByLabel('3. 검수 요구사항').inputValue(), '행사명: 테스트 축제');
  assert.equal(state.requests.filter(request => request.path === '/api/sample').length, 1);
  assert.equal(state.requests.filter(request => request.path === '/api/sample/image').length, 1);
});

test('업무 API의 명시적 세션 만료는 로그인 화면으로 복귀한다', async t => {
  const { page, state } = await fixture(t, { authenticated: true });
  await page.goto(`${baseURL}/review/new`);
  await page.getByRole('heading', { name: '어떤 광고물을 검수할까요?' }).waitFor();
  await page.getByRole('button', { name: '검수 서버 연결됨' }).waitFor();
  state.healthStatus = 401;
  await page.getByRole('button', { name: '검수 서버 연결됨' }).click();
  await page.waitForURL('**/login');
  assert.equal(await page.getByRole('heading', { name: '어떤 광고물을 검수할까요?' }).count(), 0);
});

for (const scenario of [
  { title: '잘못된 계정', options: { loginStatus: 403 }, message: '아이디 또는 비밀번호를 확인해 주세요.', loginRequests: 1 },
  { title: 'CSRF 거절', options: { csrfStatus: 403 }, message: '보안 확인이 만료되었거나 실패했습니다. 다시 시도해 주세요.', loginRequests: 0 },
  { title: '서버 연결 실패', options: { csrfStatus: 0 }, message: '로그인 서버에 연결하지 못했습니다. 연결 상태를 확인하고 다시 시도해 주세요.', loginRequests: 0 },
  { title: '세션 생성 실패', options: { establishSession: false }, message: '로그인 후 세션을 확인하지 못했습니다. 다시 시도해 주세요.', loginRequests: 1 },
]) {
  test(`${scenario.title}: 보호 화면에 진입하지 않고 다시 제출할 수 있다`, async t => {
    const { page, state } = await fixture(t, scenario.options);
    await page.goto(`${baseURL}/login`);
    await submitLogin(page);
    await page.getByText(scenario.message, { exact: true }).waitFor();
    assert.equal(new URL(page.url()).pathname, '/login');
    assert.equal(await page.getByLabel('비밀번호', { exact: true }).inputValue(), '');
    assert.equal(await page.getByRole('button', { name: '로그인', exact: true }).isEnabled(), true);
    assert.equal(state.requests.filter(request => request.path.endsWith('/login')).length, scenario.loginRequests);
  });
}

for (const status of [403, 404, 503]) {
  test(`세션 확인 ${status} 오류는 익명 사용자로 오인하지 않고 재확인한다`, async t => {
    const { page, state } = await fixture(t, { meStatus: status });
    await page.goto(`${baseURL}/review/new`);
    await page.getByRole('heading', { name: '로그인 상태를 확인할 수 없어요.' }).waitFor();
    assert.equal(new URL(page.url()).pathname, '/review/new');
    state.meStatus = 200;
    await page.getByRole('button', { name: '다시 확인하기', exact: true }).click();
    await page.waitForURL('**/login');
    assert.equal(await page.getByRole('button', { name: '로그인', exact: true }).isEnabled(), true);
  });
}

test('로그아웃 실패는 현재 세션을 유지하고 재시도 성공 후 해제한다', async t => {
  const { page, state } = await fixture(t, { authenticated: true, logoutStatus: 503 });
  await page.goto(`${baseURL}/review/new`);
  await page.getByRole('button', { name: '로그아웃', exact: true }).click();
  await page.getByRole('alert').waitFor();
  assert.equal(new URL(page.url()).pathname, '/review/new');
  assert.equal(state.authenticated, true);
  state.logoutStatus = 204;
  await page.getByRole('button', { name: '로그아웃', exact: true }).click();
  await page.waitForURL('**/login');
});

test('이미지 입력·기준 확정·검수·결과 근거와 서비스 소개 왕복 후 결과를 보존한다', async t => {
  const { page, state } = await fixture(t, { authenticated: true });
  await page.goto(`${baseURL}/review/new`);
  await page.getByLabel('1. 프로젝트 이름').fill('화면 통합 검증');
  await page.getByLabel('검수 이미지 선택', { exact: true }).setInputFiles({ name: 'test.png', mimeType: 'image/png', buffer: png });
  await page.getByLabel('3. 검수 요구사항').fill('행사명: 테스트 축제');
  await page.getByRole('button', { name: '검수 항목 확인하기', exact: true }).first().click();
  await page.getByRole('heading', { name: '검수할 항목을 확정해 주세요.' }).waitFor();
  assert.equal(await page.getByRole('button', { name: '검수 시작하기', exact: true }).isDisabled(), true);
  await page.getByRole('button', { name: '선택한 기준 확정하기', exact: true }).click();
  await page.getByRole('button', { name: '검수 시작하기', exact: true }).click();
  await page.getByRole('heading', { name: '항목별 검수 결과를 확인하세요.' }).waitFor();
  await page.getByRole('button', { name: '위치 보기 · 1곳' }).click();
  await page.getByLabel('근거 영역: 테스트 축제', { exact: true }).waitFor();
  assert.equal(state.requests.filter(request => request.path === '/api/reviews' && request.method === 'POST').length, 1);
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: '결과 JSON 저장', exact: true }).click();
  const download = await downloadEvent;
  assert.match(download.suggestedFilename(), /\.json$/);
  await page.getByRole('button', { name: '서비스 메인으로', exact: true }).click();
  await page.waitForURL(`${baseURL}/`);
  await page.getByRole('button', { name: '검수 시작하기', exact: true }).first().click();
  await page.getByRole('heading', { name: '항목별 검수 결과를 확인하세요.' }).waitFor();
  assert.equal(state.requests.filter(request => request.path === '/api/reviews' && request.method === 'POST').length, 1);
});
