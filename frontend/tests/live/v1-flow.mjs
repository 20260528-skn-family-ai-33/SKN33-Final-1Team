// C12: actual UI/API/PostgreSQL. No API fixtures; optional single response-loss fault.
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, chmodSync, mkdirSync } from 'node:fs';
import { chromium } from 'playwright';
const config = JSON.parse(readFileSync(0, 'utf8'));
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, ignoreHTTPSErrors: !!config.https, ...(config.resume ? { storageState: config.stateFile } : {}) });
const page = await context.newPage(); page.setDefaultTimeout(15000);
const errors = []; const requests = []; page.on('pageerror', error => errors.push(error.message));
page.on('request', request => { const url = new URL(request.url()); assert.equal(url.origin, new URL(config.url).origin, 'No external browser requests'); requests.push({ method: request.method(), path: url.pathname }); });
const result = { browser: browser.version(), api_mocked: false, response_loss_injected: false };
async function signIn(email = config.email, password = config.password) {
  await page.getByLabel('이메일', { exact: true }).fill(email); await page.getByLabel('비밀번호', { exact: true }).fill(password); await page.getByRole('button', { name: '로그인', exact: true }).click();
}
try {
  if (config.resume) {
    await page.goto(config.url + config.runPath); await page.getByRole('heading', { name: '항목별 자동 판정', exact: true }).waitFor();
    assert.equal(requests.filter(row => row.method === 'POST').length, 0); result.session_survived_restart = true;
    await page.getByRole('button', { name: '로그아웃', exact: true }).click(); await page.waitForURL('**/login'); result.logout_verified = true;
  } else if (config.viewer) {
    await page.goto(config.url + config.runPath); await page.waitForURL('**/login'); await signIn(); await page.waitForURL('**' + config.runPath);
    assert.equal(await page.getByRole('button', { name: '같은 기준으로 재검수', exact: true }).isDisabled(), true); result.viewer_read_only = true;
  } else {
    await page.goto(config.url + '/projects'); await page.waitForURL('**/login');
    if (config.developer) {
      await page.getByRole('button', { name: '개발 계정으로 로그인', exact: true }).click(); await page.getByLabel('개발 계정 아이디', { exact: true }).fill('admin'); await page.getByLabel('비밀번호', { exact: true }).fill('admin'); await page.getByRole('button', { name: '로그인', exact: true }).click(); await page.waitForURL('**/projects'); result.developer_login_verified = true;
    } else await signIn();
    await page.getByLabel('새 프로젝트 이름').fill('C12 실제 연계 검수'); await page.getByRole('button', { name: '프로젝트 만들기', exact: true }).click(); await page.waitForURL('**/review/new');
    result.project_id = new URL(page.url()).pathname.split('/')[2];
    await page.getByLabel('새 이미지 선택').setInputFiles(config.image); await page.getByRole('button', { name: '이미지 등록', exact: true }).click(); await page.waitForURL('**?image_id=*');
    result.image_id = new URL(page.url()).searchParams.get('image_id'); await page.reload(); await page.getByAltText('서버에 등록된 검수 이미지').waitFor();
    await page.getByLabel('새 묶음 이름').fill('C12 기준'); await page.getByRole('button', { name: '묶음 만들기', exact: true }).click(); await page.waitForURL('**criteria_set_id=*');
    await page.getByLabel('요구사항 원문').fill(config.requirement || '행사명: 예시 축제'); await page.getByRole('button', { name: '초안 저장하고 검토', exact: true }).click(); await page.waitForURL('**/drafts/*');
    result.draft_id = new URL(page.url()).pathname.split('/').at(-1); await page.getByLabel('검수 문구').fill(config.requirement || '행사명: 예시 축제');
    // A harmless item edit is saved before confirmation; server revision is read back.
    await page.getByLabel('출처 문구').fill('C12 명시적 요구사항'); await page.getByRole('button', { name: '변경 내용 저장', exact: true }).click(); await page.getByText('서버에 저장했습니다.', { exact: true }).waitFor();
    if (config.loseConfirm) {
      let injected = false;
      await context.route('**/api/v1/projects/*/criteria-versions', async route => {
        if (route.request().method() !== 'POST' || injected) return route.continue();
        injected = true; const response = await route.fetch(); assert.equal(response.status(), 201); await route.abort('connectionreset'); result.response_loss_injected = true;
      });
    }
    await page.getByRole('button', { name: '저장된 기준 확정', exact: true }).click();
    if (config.loseConfirm) {
      await page.getByRole('alert').waitFor(); await page.reload(); await page.getByRole('button', { name: '같은 요청으로 결과 확인', exact: true }).click();
    }
    await page.waitForURL('**/criteria/*'); result.criteria_version_id = new URL(page.url()).pathname.split('/').at(-1);
    await page.getByRole('button', { name: '이 기준으로 검수 시작', exact: true }).click();
    if (config.disabled) {
      await page.getByText('검수 실행 준비가 필요합니다.', { exact: false }).waitFor(); result.disabled_runtime_rejected = true;
      assert.equal(new URL(page.url()).pathname.includes('/criteria/'), true);
    } else {
      await page.waitForURL('**/reviews/*'); result.runPath = new URL(page.url()).pathname; result.run_id = result.runPath.split('/').at(-1);
      await Promise.race([page.getByText('결과 저장 완료 · 100%', { exact: true }).waitFor({ timeout: 180000 }), page.getByText('실행이 완료되지 않았습니다.', { exact: false }).waitFor({ timeout: 180000 }).then(() => { throw new Error('Server reported terminal execution failure; inspect private OCR log.'); })]);
      const beforeReload = requests.filter(row => row.method === 'POST').length; await page.reload(); await page.getByRole('heading', { name: '항목별 자동 판정', exact: true }).waitFor(); assert.equal(requests.filter(row => row.method === 'POST').length, beforeReload);
      await page.getByRole('button', { name: /^근거 [1-9][0-9]*개 조회$/ }).first().click(); await page.getByRole('button', { name: '이미지에서 위치 보기', exact: true }).first().click(); await page.getByTestId('evidence-box').waitFor();
      await page.getByRole('button', { name: 'OCR 원문 조회', exact: true }).click(); await page.getByText(/개 조회 · 전체 조회됨/).waitFor();
      const downloading = page.waitForEvent('download'); await page.getByRole('button', { name: '서버 결과 JSON 다운로드', exact: true }).click(); const file = await downloading;
      const exported = JSON.parse(readFileSync(await file.path(), 'utf8')); assert.equal(exported.id, result.run_id); assert.equal(exported.project_id, result.project_id); assert.equal(exported.results_complete, true); assert.ok(exported.ocr_records.length > 0); assert.equal(exported.runtime.device, 'cpu');
      result.ocr_records = exported.ocr_records.length; result.results = exported.results.length; result.runtime = exported.runtime; result.input_manifest_hash = exported.input_manifest_hash; result.result_manifest_hash = exported.result_manifest_hash;
      result.model_config = exported.execution.config; result.export_contract = exported.contract_version;
      if (config.stateFile) { await context.storageState({ path: config.stateFile }); chmodSync(config.stateFile, 0o600); }
    }
    const cookies = await context.cookies();
    if (config.https) {
      const session = cookies.find(row => row.name === '__Host-baro_session'); assert.ok(session?.secure && session.httpOnly && session.sameSite === 'Strict' && session.path === '/'); assert.ok(!cookies.some(row => row.name === 'baro_session')); result.secure_cookie_verified = true;
      assert.equal(await page.getByRole('button', { name: '개발 계정으로 로그인', exact: true }).count(), 0);
    }
    if (config.screenshotDir) { mkdirSync(config.screenshotDir, { recursive: true }); await page.evaluate(() => scrollTo(0, 0)); await page.screenshot({ path: config.screenshotDir + '/desktop.png', fullPage: true, animations: 'disabled' }); await page.setViewportSize({ width: 390, height: 844 }); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true); await page.screenshot({ path: config.screenshotDir + '/mobile.png', fullPage: true, animations: 'disabled' }); }
  }
  assert.deepEqual(errors, []); result.post_count = requests.filter(row => row.method === 'POST').length; result.status = 'passed';
  process.stdout.write(JSON.stringify(result) + '\n');
} finally { await context.close(); await browser.close(); }
