import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { after, before, test } from 'node:test';
import { createServer } from 'vite';

// 별도 설치된 Playwright 또는 NODE_PATH의 도구를 사용하며 실행 중 다운로드하지 않는다.
const { chromium } = createRequire(import.meta.url)('playwright');
let baseURL = process.env.E2E_BASE_URL;
let origin;
const user = { id: 'test-user', username: 'tester', display_name: '검증 사용자' };

// 업로드가 받는 범위(1,000만 픽셀·한 변 8,192px 이하)의 아이콘·세로·정사각·가로 배너·극단 띠 모양.
const SHAPES = [[32, 32], [446, 627], [720, 1018], [1122, 1402], [2480, 3503], [3000, 3000],
  [1920, 600], [600, 3000], [8192, 1000], [1000, 8192], [5000, 200]];
// 브라우저가 clientWidth/Height를 반올림하는 소수점 칸 크기. 예전엔 여기서 스크롤바가 매 프레임 켜졌다 꺼졌다.
const STAGE_WIDTHS = [380.4, 455.75, 512.6, 586.6, 640.5, 777.9, 903.2, 1010.65];
const VIEWPORT_HEIGHTS = [440, 600.6];
// 반올림 방향은 화면 배율에 따라 달라진다(알파테스트 떨림은 Windows 125% 배율에서 발견). Mac 기본 200%도 함께 본다.
const SCALES = (process.env.E2E_SCALES || '1,1.25,1.5,2').split(',').map(Number);
let browser, vite;

before(async () => {
  if (!baseURL) {
    vite = await createServer({ server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
    await vite.listen();
    baseURL = `http://127.0.0.1:${vite.httpServer.address().port}`;
  }
  origin = new URL(baseURL).origin;
  // 개인 브라우저 세션 대신 격리된 임시 프로필을 실행한다.
  browser = await chromium.launch({
    headless: true,
    ...(process.env.E2E_CHROME_PATH
      ? { executablePath: process.env.E2E_CHROME_PATH }
      : { channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' }),
  });
});

after(async () => { await browser?.close(); await vite?.close(); });

// 인증·검수 응답을 브라우저 안에서만 대체해 실제 계정·OCR 없이 이미지 칸만 검증한다.
async function fixture(t, scale) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: scale });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const state = { review: null };
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await context.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) return route.abort();
    const path = url.pathname;
    if (!path.startsWith('/api/')) return route.continue();
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/auth/me') return json({ authenticated: true, user });
    if (path === '/api/v1/auth/csrf') return json({ csrf_token: 'test-csrf-token' });
    if (path === '/api/health') return json({ ready: true, limits: {} });
    if (path === '/api/framework/v1/health') return json({ poster_review_ready: true });
    if (path === '/api/framework/v1/images/test-poster/content' && state.review) return route.fulfill({ contentType: 'image/jpeg', body: state.review.body });
    if (path === '/api/framework/v1/runs/test-review' && state.review) return json({
      context: { run_id: 'test-review', project_id: 'test-project', actor_id: user.id, asset_version_id: 'test-poster' },
      baseline: { criteria_version_id: 'test-criteria' }, execution_status: 'waiting_review', machine_verdict: 'fail',
      criteria: { criteria: [] },
      validated: { items: state.review.results.map(item => ({ criterion_id: item.criterion_id, criterion_text: item.criterion,
        kind: 'text', verdict: item.status, observed: item.observed_text, required: true, issues: [],
        image: item.evidence.length ? { asset_version_id: 'test-poster',
          bbox: item.evidence[0].bbox_xyxy.map((v, i) => v / (i % 2 ? state.review.height : state.review.width)) } : null,
      })) },
      report: { summary: '합성 이미지의 칸 안정성 검증', change_requests: [] },
    });
    // PDF 프로젝트 목록 등 이 시험과 무관한 요청은 서버로 보내지 않고 없는 것으로 답한다.
    return json({ error: { code: 'not_found', message: '시험에서 다루지 않는 요청' } }, 404);
  });
  t.after(async () => {
    await context.close();
    assert.deepEqual(pageErrors, [], '브라우저에서 처리되지 않은 오류가 없어야 한다.');
  });
  return { page, state };
}

// 크기만 같은 시험용 포스터를 브라우저에서 그려 JPEG로 받는다(외부 이미지·실제 자료 없음).
async function poster(page, width, height) {
  const base64 = await page.evaluate(async ([w, h]) => {
    const canvas = document.createElement('canvas'); canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext('2d'); ctx.fillStyle = '#3f7d5a'; ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = '#f4ede0'; ctx.fillRect(w * .1, h * .1, w * .5, h * .1);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .6));
    const bytes = new Uint8Array(await blob.arrayBuffer());
    let text = '';
    for (let i = 0; i < bytes.length; i += 8192) text += String.fromCharCode(...bytes.subarray(i, i + 8192));
    return btoa(text);
  }, [width, height]);
  return { name: `poster-${width}x${height}.jpg`, mimeType: 'image/jpeg', buffer: Buffer.from(base64, 'base64') };
}

// 칸 폭·높이를 소수점 값으로 바꿔 가며 "화면에 맞춤" 상태에서 크기 변화와 넘침(스크롤바)을 센다.
async function unstableSizes(page) {
  return page.evaluate(async ({ widths, heights }) => {
    const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
    const stage = document.querySelector('.image-stage');
    const viewport = stage.querySelector('.image-stage-viewport');
    const problems = [];
    for (const width of widths) for (const height of heights) {
      stage.style.setProperty('width', `${width}px`); stage.style.setProperty('max-width', 'none'); stage.style.setProperty('flex', 'none');
      viewport.style.setProperty('height', `${height}px`); viewport.style.setProperty('flex', 'none');
      for (let i = 0; i < 6; i++) await frame();
      let changes = 0, overflow = 0, last = `${viewport.clientWidth}x${viewport.clientHeight}`;
      for (let i = 0; i < 10; i++) {
        await frame();
        const now = `${viewport.clientWidth}x${viewport.clientHeight}`;
        if (now !== last) changes++;
        last = now;
        if (viewport.scrollWidth > viewport.clientWidth || viewport.scrollHeight > viewport.clientHeight) overflow++;
      }
      if (changes || overflow) problems.push(`${width}x${height}: 크기 변화 ${changes}회, 넘침 ${overflow}/10`);
    }
    for (const name of ['width', 'max-width', 'flex']) stage.style.removeProperty(name);
    for (const name of ['height', 'flex']) viewport.style.removeProperty(name);
    return problems;
  }, { widths: STAGE_WIDTHS, heights: VIEWPORT_HEIGHTS });
}

for (const scale of SCALES) test(`입력 화면(배율 ${scale * 100}%): 어떤 포스터 모양·칸 크기에서도 이미지 칸이 떨리거나 넘치지 않는다`, async t => {
  const { page } = await fixture(t, scale);
  await page.goto(`${baseURL}/review/new`);
  // 이미지 칸은 보안 여부와 검수 유형을 고른 뒤에 나온다. 요구사항·문서·로고 칸이 모두 있는 종합 검수 화면에서 잰다.
  await page.getByRole('heading', { name: '보안이 필요한 자료인가요?', exact: true }).waitFor();
  await page.getByRole('button', { name: '일반 자료', exact: true }).click();
  await page.getByRole('heading', { name: '검수할 항목을 선택해 주세요', exact: true }).waitFor();
  await page.getByRole('button', { name: '종합 검수', exact: true }).click();
  await page.getByRole('heading', { name: '종합 검수 자료 입력', exact: true }).waitFor();
  const problems = [];
  for (const [width, height] of SHAPES) {
    await page.getByLabel('검수 이미지 선택', { exact: true }).setInputFiles(await poster(page, width, height));
    await page.waitForFunction(w => document.querySelector('.image-stage-surface img')?.naturalWidth === w, width);
    for (const problem of await unstableSizes(page)) problems.push(`${width}x${height} ${problem}`);
  }
  assert.deepEqual(problems, []);
});

// 결과 화면에서 항목을 누르면 판정 근거 창이 열린다. 뒤의 이미지 칸을 확인하려면 창을 닫고 이어 간다.
async function chooseRow(page, row) {
  await row.click();
  await page.locator('dialog.rv-evd[open]').waitFor();
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.querySelector('dialog.rv-evd'));
}

for (const scale of SCALES) test(`결과 화면(배율 ${scale * 100}%): 가장자리 근거 상자의 긴 이름표가 있어도 이미지 칸이 떨리거나 넘치지 않는다`, async t => {
  const { page, state } = await fixture(t, scale);
  const problems = [];
  for (const [index, [width, height]] of SHAPES.entries()) {
    const file = await poster(page, width, height);
    const box = (x1, y1, x2, y2) => [x1 * width, y1 * height, x2 * width, y2 * height].map(Math.round);
    // 이름표는 기준 문장이라 길 수 있다. 오른쪽 끝·오른쪽 위 모서리·왼쪽 아래에 근거를 둔다.
    state.review = { width, height, body: file.buffer, results: [
      { criterion_id: 'right-edge', criterion: '기간: 2026.10.23(금) ~ 10.25(일) 오전 10시부터 오후 6시까지', type: 'information',
        status: 'pass', observed_text: '기간', reason: '시험용', evidence: [{ text: '오른쪽 끝', bbox_xyxy: box(.86, .4, .97, .46) }] },
      { criterion_id: 'top-right', criterion: '장소: 직지문화공원 잔디광장 및 김천시 문화예술회관 일대', type: 'information',
        status: 'fail', observed_text: '장소', reason: '시험용', evidence: [{ text: '오른쪽 위', bbox_xyxy: box(.7, .01, .99, .05) }] },
      { criterion_id: 'bottom-left', criterion: '주최: 김천시 · 주관: 김천문화재단 · 후원: 경상북도', type: 'information',
        status: 'review', observed_text: '주최', reason: '시험용', evidence: [{ text: '왼쪽 아래', bbox_xyxy: box(.02, .9, .3, .97) }] },
      { criterion_id: 'no-location', criterion: '파일 형식: JPEG', type: 'information',
        status: 'pass', observed_text: 'JPEG', reason: '시험용', evidence: [] },
    ] };
    // 공통 실행의 저장된 결과를 표시한다. 접수·승인 흐름은 framework-entry가 별도로 검증한다.
    await page.goto(`${baseURL}/review/new?framework=test-review`);
    await page.locator('.rv-result-workbench').waitFor();
    await page.waitForFunction(w => document.querySelector('.image-stage-surface img')?.naturalWidth === w, width);
    await page.getByRole('button', { name: '모든 근거 표시', exact: true }).click();
    const list = page.getByRole('list', { name: '검수 항목 목록', exact: true });
    // 세 위치를 이미지 모양마다 번갈아 선택한다. 스크린샷용 이미지는 오른쪽 끝 항목을 사용한다.
    const selected = width === 2480 ? 0 : index % 3;
    await chooseRow(page, list.locator('.rv-result-item-button').nth(selected));
    await page.getByRole('button', { name: `근거 영역: ${state.review.results[selected].observed_text}`, exact: true }).waitFor();
    assert.equal(await page.getByTestId('evidence-box').count(), 1);
    if (process.env.E2E_SCREENSHOT_DIR && width === 2480 && scale === SCALES[0]) {
      // 요청된 경우에만 오른쪽 근거 이름표가 안쪽으로 펼쳐진 모습을 검토용으로 저장한다.
      await mkdir(process.env.E2E_SCREENSHOT_DIR, { recursive: true });
      await page.locator('.image-stage').screenshot({ path: join(process.env.E2E_SCREENSHOT_DIR, 'image-stage-right-label.png') });
    }
    for (const problem of await unstableSizes(page)) problems.push(`${width}x${height} ${problem}`);
    if (width === 2480) {
      await chooseRow(page, list.getByRole('button', { name: /파일 형식: JPEG/ }));
      await page.getByText('이 항목에는 이미지 위치 근거가 제공되지 않았습니다.', { exact: true }).waitFor();
      assert.equal(await page.getByTestId('evidence-box').count(), 0);
      assert.equal(await page.getByRole('button', { name: '선택 근거 표시', exact: true }).isEnabled(), true);
    }
  }
  assert.deepEqual(problems, []);
});
