import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { chromium } from 'playwright';
import { createServer } from 'vite';

// 합성 API로 화면 초기화·늦은 응답 차단만 검사한다. 실제 실행·승인 기록은 변경하지 않는다.
async function fixture(t, options = {}) {
  const vite = await createServer({ server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  t.after(() => vite.close()); await vite.listen();
  const base = `http://127.0.0.1:${vite.httpServer.address().port}`;
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  page.setDefaultTimeout(8000);
  const calls = [], errors = [], unexpected = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route(base + '/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    calls.push({ path, method: request.method() });
    if (await options.route?.(route, path)) return;
    let body;
    if (path.endsWith('/auth/me')) body = { authenticated: true, user: { id: 'user', display_name: '초기화 검사' } };
    else if (path.endsWith('/csrf')) body = { csrf_token: 'token' };
    else if (path.endsWith('/health')) body = { requirements_presentation: true, pdf_extraction_mode: 'qwen', poster_review_ready: true, free_text_registration: true };
    else if (path.endsWith('/projects')) body = { items: [{ id: 'project', name: '검수', my_role: 'owner' }] };
    else if (path === '/api/video-reviews/status' || path === '/api/video-logos/status') body = { ready: true };
    else if (path === '/api/video-reviews') body = { items: [] };
    else if (path === '/api/modeltest/ssh/models') body = { models: [], families: {} };
    else { unexpected.push(path); return route.fulfill({ status: 404, json: {} }); }
    await route.fulfill({ json: body });
  });
  await page.goto(base + '/review/new');
  await page.waitForFunction(() => document.querySelector('#document-project')?.value === 'project');
  const authReads = calls.filter(call => call.path.endsWith('/auth/me')).length;
  t.after(() => { assert.deepEqual(errors, []); assert.deepEqual(unexpected, []); });
  return { page, base, calls, authReads };
}

async function checkBlank(page) {
  await page.getByRole('heading', { name: '어떤 광고물을 검수할까요?' }).waitFor();
  assert.equal(await page.locator('#project-name').inputValue(), '');
  assert.equal(await page.locator('#requirements').inputValue(), '');
  assert.equal(await page.getByLabel('검수 이미지 선택', { exact: true }).evaluate(input => input.files.length), 0);
  assert.equal(await page.getByRole('progressbar').count(), 0);
}

test('현재 메뉴 재클릭과 영상·모델 도구 왕복은 입력·첨부를 초기화하고 로그인은 유지한다', { timeout: 30000 }, async t => {
  const { page, calls, authReads } = await fixture(t);
  const tools = page.getByRole('navigation', { name: '검수 도구', exact: true });
  const png = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 10;
    return canvas.toDataURL().split(',')[1];
  });
  await page.locator('#project-name').fill('이전 포스터');
  await page.locator('#requirements').fill('행사명: 이전 축제');
  await page.getByLabel('검수 이미지 선택', { exact: true }).setInputFiles({ name: 'poster.png', mimeType: 'image/png', buffer: Buffer.from(png, 'base64') });
  await page.locator('strong').filter({ hasText: /^poster\.png$/ }).waitFor();
  // 새 탭 열기는 현재 화면을 초기화하지 않는다.
  // headless Chrome의 합성 클릭에서는 기본 주소 이동만 막고 React 메뉴 처리는 실행한다.
  await page.evaluate(() => document.addEventListener('click', event => event.preventDefault(), { once: true }));
  await tools.getByRole('link', { name: '영상 검수', exact: true }).dispatchEvent('click', { button: 0, ctrlKey: true });
  assert.equal(await page.locator('#requirements').inputValue(), '행사명: 이전 축제');
  await tools.getByRole('link', { name: '포스터 검수', exact: true }).click();
  await checkBlank(page);
  await page.locator('#requirements').fill('새 입력');
  await tools.getByRole('link', { name: '영상 검수', exact: true }).click();
  await page.getByRole('heading', { name: '숏폼 동영상 검수', exact: true }).waitFor();
  await page.getByLabel('검수 기준 (한 줄에 하나)', { exact: false }).fill('이전 영상 기준');
  await tools.getByRole('link', { name: '포스터 검수', exact: true }).click();
  await checkBlank(page);
  await tools.getByRole('link', { name: '영상 검수', exact: true }).click();
  assert.equal(await page.getByLabel('검수 기준 (한 줄에 하나)', { exact: false }).inputValue(), '');
  await tools.getByRole('link', { name: '모델 테스트', exact: true }).click();
  await page.locator('#mt-requirements').waitFor({ state: 'attached' });
  assert.equal(await page.locator('#mt-requirements').inputValue(), '');
  // 기존 자동 저장 초안·실행을 다시 읽어 화면을 채우지 않는다.
  await page.locator('summary').filter({ hasText: '조건 직접 입력' }).click();
  await page.locator('#mt-requirements').fill('이전 모델 조건');
  await page.getByText(/첨부·모델 설정 저장 완료/).waitFor();
  await tools.getByRole('link', { name: '포스터 검수', exact: true }).click();
  await tools.getByRole('link', { name: '모델 테스트', exact: true }).click();
  await page.locator('#mt-requirements').waitFor({ state: 'attached' });
  assert.equal(await page.locator('#mt-requirements').inputValue(), '');
  assert.equal(calls.filter(call => call.path.endsWith('/auth/me')).length, authReads);
  assert.equal(calls.filter(call => call.method === 'DELETE' || call.path.endsWith('/auth/logout')).length, 0);
});

test('PDF 분석 중 이동을 잠그고 회전 표시·경과시간을 보여주며 취소 후 이동을 허용한다', { timeout: 30000 }, async t => {
  let release, entered;
  const gate = new Promise(resolve => { release = resolve; });
  const pending = new Promise(resolve => { entered = resolve; });
  t.after(() => release());
  const { page, calls } = await fixture(t, { route: async (route, path) => {
    const request = route.request();
    if (path.endsWith('/documents')) {
      await route.fulfill({ json: { document: { document_version_id: 'pdf', sha256: createHash('sha256').update(request.postDataBuffer()).digest('hex') } } });
    } else if (path.endsWith('/extractions')) {
      await route.fulfill({ json: { context: { run_id: 'old-draft' } } });
    } else if (path.endsWith('/runs/old-draft')) {
      entered(); await gate;
      await route.fulfill({ json: { execution_status: 'waiting_criteria' } }).catch(() => {});
    } else return false;
    return true;
  } });
  await page.getByLabel('요구사항 PDF 선택', { exact: true }).setInputFiles({ name: 'brief.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-test') });
  await pending;
  await page.getByRole('heading', { name: '검수 요구사항을 정리하고 있어요.' }).waitFor();
  const tools = page.getByRole('navigation', { name: '검수 도구', exact: true });
  for (const label of ['포스터 검수', '영상 검수', '모델 테스트']) {
    const link = tools.getByRole('link', { name: label, exact: true });
    assert.equal(await link.getAttribute('aria-disabled'), 'true');
    assert.equal(await link.getAttribute('href'), null, 'locked tools cannot open another route or tab');
    await link.dispatchEvent('click');
  }
  for (const name of ['크리에이티브체커 홈', '서비스 메인으로', '로그아웃']) {
    assert.equal(await page.getByRole('button', { name, exact: true }).isDisabled(), true);
  }
  assert.equal(await page.getByRole('button', { name: /자료 입력/ }).isDisabled(), true);
  assert.equal(new URL(page.url()).pathname, '/review/new');
  assert.equal(await page.locator('.rv-progress-spinner').isVisible(), true);
  assert.equal(await page.locator('.rv-progress-spinner').evaluate(el => getComputedStyle(el).animationName), 'rv-progress-spin');
  assert.equal(await page.locator('.rv-progress-spinner').evaluate(el => getComputedStyle(el).borderRadius), '38px');
  await page.waitForFunction(() => document.querySelector('[role="timer"] time')?.textContent !== '0:00');
  assert.match(await page.getByRole('timer', { name: '경과 시간' }).textContent(), /\d+:\d{2}/);
  await page.getByRole('button', { name: '입력 반영 취소', exact: true }).click();
  await tools.getByRole('link', { name: '영상 검수', exact: true }).click();
  await tools.getByRole('link', { name: '포스터 검수', exact: true }).click();
  await checkBlank(page);
  await page.locator('#requirements').fill('현재 작성 중인 조건');
  release();
  // 원래 1.5초 조회 주기를 지나도 이전 실행을 재조회·복원하지 않아야 한다.
  await page.waitForTimeout(1800);
  assert.equal(await page.locator('#requirements').inputValue(), '현재 작성 중인 조건');
  assert.equal(new URL(page.url()).search, '');
  assert.equal(calls.filter(call => call.path.endsWith('/runs/old-draft')).length, 1);
  assert.equal(calls.filter(call => call.path.endsWith('/requirements')).length, 0);
});

test('홈 버튼 왕복도 새 입력으로 초기화한다', { timeout: 30000 }, async t => {
  const { page, base } = await fixture(t);
  for (const home of ['크리에이티브체커 홈', '서비스 메인으로']) {
    await page.locator('#project-name').fill('이전 작업');
    await page.locator('#requirements').fill('홈으로 이동하기 전 기준');
    await page.getByRole('button', { name: home, exact: true }).click();
    await page.waitForURL(base + '/');
    await page.getByRole('button', { name: /^검수 시작하기/ }).first().click();
    await checkBlank(page);
  }
});

test('포스터 실행 중 이동을 잠그고 실행이 끝나면 메뉴 초기화를 허용한다', { timeout: 30000 }, async t => {
  let status = 'reviewing';
  const { page, base, calls } = await fixture(t, { route: async (route, path) => {
    if (!path.endsWith('/runs/live-run')) return false;
    await route.fulfill({ json: { execution_status: status, context: { run_id: 'live-run', project_id: 'project' }, criteria: { criteria: [] } } });
    return true;
  } });
  await page.goto(base + '/review/new?framework=live-run');
  await page.getByRole('progressbar', { name: '검수 처리 진행 상태', exact: true }).waitFor();
  const posterLink = page.getByRole('navigation', { name: '검수 도구', exact: true }).getByRole('link', { name: '포스터 검수', exact: true });
  assert.equal(await posterLink.getAttribute('aria-disabled'), 'true');
  assert.equal(await page.getByRole('button', { name: '크리에이티브체커 홈', exact: true }).isDisabled(), true);
  assert.equal(await page.locator('.rv-progress-spinner').isVisible(), true);
  assert.equal(await page.getByRole('timer', { name: '경과 시간' }).count(), 1);
  status = 'failed';
  await page.waitForFunction(() => document.querySelector('.rv-tool-link')?.getAttribute('aria-disabled') !== 'true');
  await posterLink.click();
  await checkBlank(page);
  const reads = calls.filter(call => call.path.endsWith('/runs/live-run')).length;
  await page.waitForTimeout(1200);
  assert.equal(new URL(page.url()).search, '');
  assert.equal(calls.filter(call => call.path.endsWith('/runs/live-run')).length, reads);
});

test('영상 업로드 중 나가면 늦은 접수 응답으로 이전 영상 화면을 다시 열지 않는다', { timeout: 30000 }, async t => {
  let release, entered;
  const gate = new Promise(resolve => { release = resolve; });
  const pending = new Promise(resolve => { entered = resolve; });
  t.after(() => release());
  const { page } = await fixture(t, { route: async (route, path) => {
    if (path !== '/api/video-reviews' || route.request().method() !== 'POST') return false;
    entered(); await gate;
    await route.fulfill({ json: { id: 'old-video' } }).catch(() => {});
    return true;
  } });
  const tools = page.getByRole('navigation', { name: '검수 도구', exact: true });
  await tools.getByRole('link', { name: '영상 검수', exact: true }).click();
  await page.locator('#video-file').setInputFiles({ name: 'video.mp4', mimeType: 'video/mp4', buffer: Buffer.from('synthetic-video') });
  await page.getByRole('button', { name: '동영상 검수 시작', exact: true }).click();
  await pending;
  await tools.getByRole('link', { name: '포스터 검수', exact: true }).click();
  await checkBlank(page);
  release();
  await page.waitForTimeout(300);
  assert.equal(new URL(page.url()).pathname, '/review/new');
  assert.equal(new URL(page.url()).search, '');
});
