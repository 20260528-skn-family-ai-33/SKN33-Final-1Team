import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const root = fileURLToPath(new URL('../../', import.meta.url));
const base = 'http://127.0.0.1:5178';

test('로그인 화면에서 가입 검증·중복 안내·가입 후 로그인을 진행한다', { timeout: 60000 }, async t => {
  const child = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5178', '--strictPort'], {
    cwd: root, stdio: 'pipe', env: { ...process.env, VITE_API_PROFILE: 'legacy' },
  });
  let output = ''; child.stdout.on('data', data => { output += data; }); child.stderr.on('data', data => { output += data; });
  t.after(() => { if (child.exitCode === null) child.kill(); });
  let ready = false;
  for (let i = 0; i < 80; i++) {
    if (child.exitCode !== null) throw new Error(output);
    try { if ((await fetch(base)).ok) { ready = true; break; } } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(ready, output);
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  page.setDefaultTimeout(10000);
  let authenticated = false, signupCalls = 0;
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const user = { id: 'signup-fixture', email: 'member@example.com', display_name: '개발 조원' };
  await page.route(base + '/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/auth/me') return json({ authenticated, user: authenticated ? user : null });
    if (path === '/api/v1/auth/csrf') return json({ csrf_token: 'signup-fixture-token' });
    if (path === '/api/v1/auth/signup') {
      signupCalls++;
      assert.equal(request.headers()['x-csrftoken'], 'signup-fixture-token');
      assert.deepEqual(request.postDataJSON(), { email: signupCalls === 1 ? 'existing@example.com' : user.email, display_name: user.display_name, password: 'synthetic-password-123' });
      return signupCalls === 1 ? json({ error: { code: 'EMAIL_ALREADY_EXISTS', message: 'duplicate' } }, 409) : json({ user }, 201);
    }
    if (path === '/api/v1/auth/login') {
      assert.deepEqual(request.postDataJSON(), { email: user.email, password: 'synthetic-password-123' });
      authenticated = true;
      return json({ user });
    }
    if (path === '/api/health') return json({ ready: true, limits: {} });
    if (path === '/api/framework/v1/health') return json({ pdf_extraction_mode: 'qwen', requirements_presentation: true });
    if (path === '/api/framework/v1/projects') return json({ items: [], next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto(base + '/login');
  await page.getByRole('button', { name: '회원가입', exact: true }).click();
  await page.getByLabel('이름', { exact: true }).fill(user.display_name);
  await page.getByLabel('이메일', { exact: true }).fill('existing@example.com');
  await page.getByLabel('비밀번호', { exact: true }).fill('short');
  await page.getByRole('button', { name: '가입하기', exact: true }).click();
  await page.getByText('비밀번호는 8~128자로 입력하고 숫자와 특수문자를 각각 하나 이상 포함해 주세요.', { exact: true }).waitFor();
  assert.equal(signupCalls, 0);
  await page.getByLabel('비밀번호', { exact: true }).fill('synthetic-password-123');
  await page.getByLabel('비밀번호 확인', { exact: true }).fill('different');
  await page.getByRole('button', { name: '가입하기', exact: true }).click();
  await page.getByText('비밀번호 확인이 일치하지 않습니다.', { exact: true }).waitFor();
  assert.equal(signupCalls, 0);
  await page.getByLabel('비밀번호 확인', { exact: true }).fill('synthetic-password-123');
  await page.screenshot({ path: join(root, '../.runtime/signup-form-desktop.png') });
  await page.getByRole('button', { name: '가입하기', exact: true }).click();
  await page.getByText('이미 가입된 이메일입니다. 로그인해 주세요.', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('비밀번호', { exact: true }).inputValue(), '');
  await page.getByLabel('이메일', { exact: true }).fill(user.email);
  await page.getByLabel('비밀번호', { exact: true }).fill('synthetic-password-123');
  await page.getByLabel('비밀번호 확인', { exact: true }).fill('synthetic-password-123');
  await page.getByRole('button', { name: '가입하기', exact: true }).click();
  await page.getByText('회원가입이 완료됐습니다. 이메일과 비밀번호로 로그인해 주세요.', { exact: true }).waitFor();
  assert.equal(signupCalls, 2);
  assert.equal(authenticated, false);
  assert.equal(await page.getByLabel('아이디', { exact: true }).inputValue(), user.email);
  assert.equal(await page.getByLabel('비밀번호', { exact: true }).inputValue(), '');
  await page.getByLabel('비밀번호', { exact: true }).fill('synthetic-password-123');
  await page.getByRole('button', { name: '로그인', exact: true }).click();
  await page.waitForURL(base + '/review/new');
  assert.deepEqual(errors, []);
});
