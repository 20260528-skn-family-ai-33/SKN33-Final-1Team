import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:8123';

// 실제 로컬 인증 API와 격리 브라우저로 임시 admin 계정의 전체 진입 흐름을 확인한다.
test('admin 로그인 후 검수 이동·세션 복원·로그아웃', async t => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.E2E_CHROME_PATH
      ? { executablePath: process.env.E2E_CHROME_PATH }
      : { channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' }),
  });
  t.after(() => browser.close());
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));

  await page.goto(`${baseURL}/review/new`);
  await page.waitForURL('**/login');
  await page.getByLabel('아이디', { exact: true }).fill('admin');
  await page.getByLabel('비밀번호', { exact: true }).fill('wrong');
  await page.getByRole('button', { name: '로그인', exact: true }).click();
  await page.getByText('아이디 또는 비밀번호를 확인해 주세요.', { exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, '/login');

  await page.getByLabel('비밀번호', { exact: true }).fill('admin');
  await page.getByRole('button', { name: '로그인', exact: true }).click();
  await page.waitForURL('**/review/new');
  await page.getByRole('heading', { name: '어떤 광고물을 검수할까요?' }).waitFor();
  await page.getByText('관리자 (개발용)', { exact: true }).waitFor();
  assert.deepEqual(await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage) })), { local: [], session: [] });

  await page.reload();
  await page.getByRole('heading', { name: '어떤 광고물을 검수할까요?' }).waitFor();
  await page.getByRole('button', { name: '로그아웃', exact: true }).click();
  await page.waitForURL('**/login');
  await page.goto(`${baseURL}/review/new`);
  await page.waitForURL('**/login');
  assert.deepEqual(errors, []);
});
