import assert from 'node:assert/strict';
import test from 'node:test';
import { chromium } from 'playwright';
import { createServer } from 'vite';

test('로컬 미리보기는 로그인·파일·입력 없이 이동하며 업무 API를 호출하지 않는다', { timeout: 30000 }, async t => {
  const vite = await createServer({ server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  t.after(() => vite.close()); await vite.listen();
  const base = `http://127.0.0.1:${vite.httpServer.address().port}`;
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const calls = [], errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route(base + '/api/**', async route => {
    calls.push({ method: route.request().method(), path: new URL(route.request().url()).pathname });
    return route.fulfill({ status: 503, json: { error: { code: 'preview_backend_offline' } } });
  });
  await page.goto(base + '/review/preview');
  const toolbar = page.getByRole('complementary', { name: '로컬 화면 미리보기' });
  await toolbar.waitFor();
  assert.equal(await page.locator('#requirements').inputValue(), '');
  assert.equal(await page.getByRole('button', { name: '요구사항 PDF 선택', exact: true }).count(), 0);
  await page.getByLabel('원본 로고 이미지 추가', { exact: true }).setInputFiles({
    name: '주최 로고.svg', mimeType: 'image/svg+xml',
    buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><circle cx="16" cy="16" r="12"/></svg>'),
  });
  const logo = page.getByRole('img', { name: '원본 로고 주최 로고.svg', exact: true });
  await logo.waitFor();
  await page.getByRole('button', { name: '주최 로고.svg 삭제', exact: true }).click();
  assert.equal(await logo.count(), 0);
  await page.getByRole('button', { name: '검수 항목 확인하기', exact: true }).click();
  await page.getByRole('heading', { name: '검수할 항목을 확정해 주세요.' }).waitFor();
  await toolbar.getByRole('button', { name: '검수 진행', exact: true }).click();
  await page.getByRole('heading', { name: '검수 진행 화면 미리보기' }).waitFor();
  await page.getByRole('progressbar').waitFor();
  const magnifiedPoster = page.locator('.rv-poster-scan-zoom img');
  await magnifiedPoster.waitFor();
  assert.equal(await magnifiedPoster.evaluate(img => new DOMMatrix(getComputedStyle(img).transform).a), 2);
  await toolbar.getByRole('button', { name: '결과 확인', exact: true }).click();
  await page.getByRole('heading', { name: '선택 항목 상세', exact: true }).waitFor();
  await page.getByText('화면 미리보기 · 예시 판정', { exact: true }).waitFor();
  await toolbar.getByRole('button', { name: '자료 입력', exact: true }).click();
  await page.getByRole('heading', { name: '어떤 광고물을 검수할까요?' }).waitFor();
  assert.deepEqual(calls.filter(call => call.path.startsWith('/api/framework') || call.method !== 'GET'), []);
  await page.getByRole('link', { name: '실제 검수로 돌아가기' }).click();
  await page.getByRole('heading', { name: '로그인 상태를 확인할 수 없어요.' }).waitFor();
  assert.deepEqual(errors, []);
});

test('UI 전용 모드는 기존 검수 URL에서도 인증·Qwen·상태 조회 요청을 전혀 보내지 않는다', { timeout: 30000 }, async t => {
  const vite = await createServer({ mode: 'ui', server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  t.after(() => vite.close()); await vite.listen();
  const base = `http://127.0.0.1:${vite.httpServer.address().port}`;
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const requests = [], errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route(base + '/api/**', route => { requests.push(route.request().url()); return route.abort(); });
  await page.goto(base + '/review/new?framework=old-failed-run');
  await page.getByText('UI 전용 · 서버 연결 없음', { exact: true }).waitFor();
  const toolbar = page.getByRole('complementary', { name: '로컬 화면 미리보기' });
  await toolbar.getByRole('button', { name: '다음 화면으로 →', exact: true }).click();
  await page.getByRole('heading', { name: '검수할 항목을 확정해 주세요.' }).waitFor();
  await toolbar.getByRole('button', { name: '다음 화면으로 →', exact: true }).click();
  await page.getByRole('heading', { name: '검수 진행 화면 미리보기' }).waitFor();
  await toolbar.getByRole('button', { name: '다음 화면으로 →', exact: true }).click();
  await page.getByRole('heading', { name: '선택 항목 상세', exact: true }).waitFor();
  await page.getByRole('button', { name: '크리에이티브체커 홈', exact: true }).click();
  await page.getByRole('heading', { name: '어떤 광고물을 검수할까요?' }).waitFor();
  assert.equal(await page.locator('#project-name').inputValue(), '');
  assert.equal(await page.locator('#requirements').inputValue(), '');
  assert.equal(await page.getByRole('progressbar').count(), 0);
  assert.deepEqual(requests, []);
  assert.deepEqual(errors, []);
});
