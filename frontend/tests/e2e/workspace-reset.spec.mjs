import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { chromium } from 'playwright';
import { createServer } from 'vite';

// 합성 API로 화면 초기화·늦은 응답 차단만 검사한다. 실제 실행·승인 기록은 변경하지 않는다.
async function fixture(t, options = {}) {
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const vite = await createServer({ root, configFile: fileURLToPath(new URL('../../vite.config.js', import.meta.url)),
    server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
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
    else if (path === '/api/video-reviews/status' || path === '/api/video-logos/status') body = { ready: true, project_reviews: true };
    else if (['/api/video-reviews', '/api/video-logos', '/api/poster-logos'].includes(path)) body = { items: [] };
    else { unexpected.push(path); return route.fulfill({ status: 404, json: {} }); }
    await route.fulfill({ json: body });
  });
  await page.goto(base + '/review/new');
  // 저장할 프로젝트가 하나뿐이면 저절로 고르고, 고른 프로젝트 이름을 프로젝트 버튼에 보여 준다.
  await page.waitForFunction(() => document.querySelector('.rv-project-chip.is-selected .rv-project-chip-text')?.textContent === '검수');
  const authReads = calls.filter(call => call.path.endsWith('/auth/me')).length;
  t.after(() => { assert.deepEqual(errors, []); assert.deepEqual(unexpected, []); });
  return { page, base, calls, authReads };
}

// 포스터·동영상 검수 모두 자료 입력 전에 보안 여부와 검수 유형을 고른다.
const firstScreen = '보안이 필요한 자료인가요?';
const videoInput = '숏폼 동영상 문구 검수 자료 입력';
async function chooseReview(page, title = '문구 검수 자료 입력') {
  await page.getByRole('heading', { name: firstScreen, exact: true }).waitFor();
  await page.getByRole('button', { name: '일반 자료', exact: true }).click();
  await page.getByRole('heading', { name: '검수할 항목을 선택해 주세요', exact: true }).waitFor();
  await page.getByRole('button', { name: '문구 검수', exact: true }).click();
  await page.getByRole('heading', { name: title, exact: true }).waitFor();
}

// 새 입력은 보안 여부를 묻는 첫 화면부터 다시 시작한다. 입력 칸은 유형까지 고른 뒤에 확인한다.
async function checkBlank(page) {
  await page.getByRole('heading', { name: firstScreen, exact: true }).waitFor();
  assert.equal(await page.locator('#project-name').inputValue(), '');
  await chooseReview(page);
  assert.equal(await page.locator('#requirements').inputValue(), '');
  assert.equal(await page.getByLabel('검수 이미지 선택', { exact: true }).evaluate(input => input.files.length), 0);
  assert.equal(await page.getByRole('progressbar').count(), 0);
}

test('현재 메뉴 재클릭과 영상 도구 왕복은 입력·첨부를 초기화하고 로그인은 유지한다', { timeout: 30000 }, async t => {
  const { page, calls, authReads } = await fixture(t);
  const tools = page.getByRole('navigation', { name: '검수 도구', exact: true });
  const png = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 10;
    return canvas.toDataURL().split(',')[1];
  });
  await chooseReview(page);
  await page.locator('#project-name').fill('이전 포스터');
  await page.locator('#requirements').fill('행사명: 이전 축제');
  await page.getByLabel('검수 이미지 선택', { exact: true }).setInputFiles({ name: 'poster.png', mimeType: 'image/png', buffer: Buffer.from(png, 'base64') });
  await page.locator('strong').filter({ hasText: /^poster\.png$/ }).waitFor();
  // 새 탭 열기는 현재 화면을 초기화하지 않는다.
  // headless Chrome의 합성 클릭에서는 기본 주소 이동만 막고 React 메뉴 처리는 실행한다.
  await page.evaluate(() => document.addEventListener('click', event => event.preventDefault(), { once: true }));
  await tools.getByRole('link', { name: '동영상 검수', exact: true }).dispatchEvent('click', { button: 0, ctrlKey: true });
  assert.equal(await page.locator('#requirements').inputValue(), '행사명: 이전 축제');
  await tools.getByRole('link', { name: '포스터 검수', exact: true }).click();
  await checkBlank(page);
  await page.locator('#requirements').fill('새 입력');
  await tools.getByRole('link', { name: '동영상 검수', exact: true }).click();
  await chooseReview(page, videoInput);
  await page.getByLabel('검수 요구사항', { exact: true }).fill('이전 영상 기준');
  await tools.getByRole('link', { name: '포스터 검수', exact: true }).click();
  await checkBlank(page);
  await tools.getByRole('link', { name: '동영상 검수', exact: true }).click();
  // 동영상 검수도 첫 화면부터 다시 시작하고, 앞서 적은 기준은 남지 않는다.
  await chooseReview(page, videoInput);
  assert.equal(await page.getByLabel('검수 요구사항', { exact: true }).inputValue(), '');
  // 모델 테스트 화면은 없앴다. 메뉴에도 남지 않는다.
  assert.equal(await tools.getByRole('link', { name: '모델 테스트', exact: true }).count(), 0);
  assert.equal(calls.filter(call => call.path.endsWith('/auth/me')).length, authReads);
  assert.equal(calls.filter(call => call.method === 'DELETE' || call.path.endsWith('/auth/logout')).length, 0);
});

test('PDF 분석 중 자료 입력을 유지하고 카드에서 진행을 보여주며 취소 후 이동을 허용한다', { timeout: 30000 }, async t => {
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
  await chooseReview(page);
  await page.getByLabel('요구사항 문서 선택', { exact: true }).setInputFiles({ name: 'brief.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-test') });
  await pending;
  const documentCard = page.getByRole('region', { name: '요구사항 문서 첨부', exact: true });
  await documentCard.getByRole('progressbar', { name: '문서 분석 단계' }).waitFor();
  assert.equal(await page.locator('.rv-progress-page').count(), 0);
  assert.ok(await page.locator('#requirements').isVisible());
  const tools = page.getByRole('navigation', { name: '검수 도구', exact: true });
  for (const label of ['포스터 검수', '동영상 검수', '내 작업']) {
    const link = tools.getByRole('link', { name: label, exact: true });
    assert.equal(await link.getAttribute('aria-disabled'), 'true');
    assert.equal(await link.getAttribute('href'), null, 'locked tools cannot open another route or tab');
    await link.dispatchEvent('click');
  }
  // 프로젝트 줄의 '서비스 메인으로' 버튼은 없어졌다. 서비스 메인으로 가는 길은 머리글의 로고(홈)뿐이다.
  for (const name of ['크리에이티브체커 홈', '로그아웃']) {
    assert.equal(await page.getByRole('button', { name, exact: true }).isDisabled(), true);
  }
  assert.equal(await page.getByRole('button', { name: /자료 입력/ }).isDisabled(), true);
  assert.equal(new URL(page.url()).pathname, '/review/new');
  await documentCard.getByText('요구사항 분석을 접수하고 있습니다.', { exact: true }).waitFor();
  assert.ok(await documentCard.locator('.rv-progress-spinner').isVisible());
  await documentCard.getByRole('timer', { name: '문서 분석 경과 시간', exact: true }).filter({ hasText: /총 경과 00:0[1-9]/ }).waitFor();
  await page.getByRole('button', { name: '입력 반영 취소', exact: true }).click();
  await tools.getByRole('link', { name: '동영상 검수', exact: true }).click();
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
  await chooseReview(page);
  await page.locator('#project-name').fill('이전 작업');
  await page.locator('#requirements').fill('홈으로 이동하기 전 기준');
  // 프로젝트 줄의 '서비스 메인으로' 버튼은 없어졌다. 서비스 메인으로는 머리글의 로고(홈)로 간다.
  await page.getByRole('button', { name: '크리에이티브체커 홈', exact: true }).click();
  await page.waitForURL(base + '/');
  await page.getByRole('button', { name: /^검수 시작하기/ }).first().click();
  await checkBlank(page);
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
  assert.equal(await page.locator('.rv-scan-lens').isVisible(), true);
  assert.match(await page.getByRole('list', { name: '처리 단계', exact: true }).textContent(), /요구사항 추출/);
  assert.equal(await page.getByRole('timer', { name: '경과 시간' }).count(), 1);
  status = 'failed';
  await page.waitForFunction(() => document.querySelector('.cc-header-link')?.getAttribute('aria-disabled') !== 'true');
  await posterLink.click();
  await checkBlank(page);
  const reads = calls.filter(call => call.path.endsWith('/runs/live-run')).length;
  await page.waitForTimeout(1200);
  assert.equal(new URL(page.url()).search, '');
  assert.equal(calls.filter(call => call.path.endsWith('/runs/live-run')).length, reads);
});

test('영상 업로드 중에는 메뉴 이동을 잠그고 접수 뒤 이동을 허용한다', { timeout: 30000 }, async t => {
  let release, entered;
  const gate = new Promise(resolve => { release = resolve; });
  const pending = new Promise(resolve => { entered = resolve; });
  t.after(() => release());
  const { page } = await fixture(t, { route: async (route, path) => {
    if (path === '/api/video-reviews/old-video') {
      await route.fulfill({ json: { id: 'old-video', status: 'queued' } });
      return true;
    }
    if (path !== '/api/video-reviews' || route.request().method() !== 'POST') return false;
    entered(); await gate;
    await route.fulfill({ json: { id: 'old-video' } }).catch(() => {});
    return true;
  } });
  const tools = page.getByRole('navigation', { name: '검수 도구', exact: true });
  await tools.getByRole('link', { name: '동영상 검수', exact: true }).click();
  await chooseReview(page, videoInput);
  await page.locator('#video-file').setInputFiles({ name: 'video.mp4', mimeType: 'video/mp4', buffer: Buffer.from('synthetic-video') });
  await page.getByRole('button', { name: '검수 시작하기', exact: true }).click();
  await pending;
  assert.equal(await tools.getByRole('link', { name: '포스터 검수', exact: true }).getAttribute('aria-disabled'), 'true');
  release();
  await page.waitForURL('**/video?id=old-video');
  await tools.getByRole('link', { name: '포스터 검수', exact: true }).click();
  await checkBlank(page);
  await page.waitForTimeout(300);
  assert.equal(new URL(page.url()).pathname, '/review/new');
  assert.equal(new URL(page.url()).search, '');
});

test('영상 결과에서 광고 프레임을 열고 수정 사유와 부모 실행을 유지한다', { timeout: 30000 }, async t => {
  const path = '/api/video-reviews/checked';
  const frame = { frame_id: 't001000', time_ms: 1000, width: 640, height: 360, lines: [] };
  const job = { id: 'checked', status: 'completed', project_name: '시연', criteria_version: 1, criteria: [],
    media: { duration_ms: 2000, sha256: 'a'.repeat(64) }, coverage: { duration_ms: 2000, inspected_ms: [1000] },
    frames: [frame], disclosure: { applicable: true, status: 'pass', rules: {} },
    workflow: { project_id: 'project', asset_version: 1, human_decision: 'unreviewed', decisions: [] },
    approval_options: { blocked: false, human_check_ids: [] } };
  const { page, base } = await fixture(t, { route: async (route, requested) => {
    if (requested === path) await route.fulfill({ json: job });
    else if (requested === path + '/evidence-graph') {
      await route.fulfill({ json: { nodes: [{ ...frame, id: 'frame', kind: 'frame', label: '프레임 0:01' }],
        verdicts: [{ id: 'rule-a', rule: 'A', title: '광고 표시', status: 'pass', chain: ['frame'] }] } });
    } else if (requested === path + '/frames/' + frame.frame_id) {
      await route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"/>' });
    } else if (requested === path + '/video') {
      await route.fulfill({ path: fileURLToPath(new URL('../../src/assets/sample-shortform.mp4', import.meta.url)) });
    } else if (requested === path + '/approvals') {
      const decision = route.request().postDataJSON();
      assert.equal(decision.decision, 'approve_changes');
      assert.equal(decision.asset_sha256, job.media.sha256);
      assert.equal(decision.reason, '광고 위치 수정');
      job.workflow.human_decision = 'changes_approved';
      job.workflow.decisions.push({ ...decision, approved_at: '2026-11-11T00:00:00Z' });
      await route.fulfill({ json: job });
    } else if (requested === path + '/revisions') {
      await route.fulfill({ json: { items: [{ ...job.workflow, id: job.id, status: job.status, criteria_version: 1 }] } });
    } else if (requested === '/api/video-reviews' && route.request().method() === 'POST') {
      const request = route.request();
      const form = await new Response(request.postDataBuffer(), {
        headers: { 'Content-Type': request.headers()['content-type'] },
      }).formData();
      assert.equal(form.get('parent_run_id'), job.id);
      assert.equal(form.get('project_id'), job.workflow.project_id);
      await route.fulfill({ status: 202, json: { id: 'child' } });
    } else if (requested === '/api/video-reviews/child') {
      await route.fulfill({ json: { id: 'child', status: 'queued' } });
    } else return false;
    return true;
  } });
  await page.goto(base + '/video?id=checked');
  await page.getByText('광고·협찬 근거 관계', { exact: true }).click();
  await page.getByRole('button', { name: '프레임 0:01', exact: true }).click();
  await page.getByRole('img', { name: /프레임과 읽은 글자 영역/ }).waitFor();
  await page.getByLabel('수정 요청 사유', { exact: true }).fill('광고 위치 수정');
  await page.getByRole('button', { name: '수정 요청 승인', exact: true }).click();
  await page.getByText('영상·기준·승인 이력', { exact: true }).click();
  await page.getByText(/광고 위치 수정 · 2026-11-11/).waitFor();
  await page.getByRole('button', { name: '수정본 등록', exact: true }).click();
  // 보안 여부 기록이 없는 검수라 수정본을 올릴 때 보안 여부를 다시 묻는다. 유형은 문구 검수로 이어진다.
  await page.getByRole('heading', { name: firstScreen, exact: true }).waitFor();
  await page.getByRole('button', { name: '일반 자료', exact: true }).click();
  await page.getByRole('heading', { name: videoInput, exact: true }).waitFor();
  await page.getByText(/수정본을 등록/).waitFor();
  await page.getByLabel('검수 동영상 선택', { exact: true }).setInputFiles({
    name: 'revised.mp4', mimeType: 'video/mp4', buffer: Buffer.from('synthetic-video'),
  });
  await page.getByRole('button', { name: '검수 시작하기', exact: true }).click();
  await page.waitForURL('**/video?id=child');
});
