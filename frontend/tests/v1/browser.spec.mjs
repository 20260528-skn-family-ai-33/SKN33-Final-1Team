import assert from 'node:assert/strict';
import { readFile, mkdir } from 'node:fs/promises';
import { after, before, test } from 'node:test';
import { chromium } from 'playwright';
import { fixtures, ids, uid, wrap, page as pageDto } from './fixtures.mjs';
const baseURL = process.env.E2E_V1_BASE_URL || 'http://127.0.0.1:8131'; const origin = new URL(baseURL).origin;
const png = await readFile(new URL('./poster.png', import.meta.url));
const projectPath = `/projects/${ids.project}`; const draftPath = `${projectPath}/drafts/${ids.draft}`; const criteriaPath = `${projectPath}/criteria/${ids.version}`; const runPath = `${projectPath}/reviews/${ids.run}`;
let browser;
before(async () => { browser = await chromium.launch({ headless: true, ...(process.env.E2E_CHROME_PATH ? { executablePath: process.env.E2E_CHROME_PATH } : { channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' }) }); });
after(async () => { await browser?.close(); });
async function setup(t, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, acceptDownloads: true }); const page = await context.newPage(); page.setDefaultTimeout(7000);
  const f = fixtures(); const state = { ...f, authenticated: true, developer: true, requests: [], unexpected: [], reads: 0, conflict: false, loseConfirm: false, confirmStatus: 200, runError: 0, meError: false, ...options }; const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const request = route.request(); const url = new URL(request.url()); if (url.origin !== origin) { state.unexpected.push(request.url()); return route.abort(); }
    const path = url.pathname, method = request.method(); if (!path.startsWith('/api/')) return route.continue();
    state.requests.push({ path, method, body: request.postData(), headers: request.headers() });
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(wrap(body)) });
    const error = (code, status) => json({ error: { code, message: 'test-only private details' } }, status);
    if (path === '/api/v1/auth/me') return state.meError ? route.abort() : json({ authenticated: state.authenticated, user: state.authenticated ? state.user : null });
    if (path === '/api/v1/auth/csrf') return json({ csrf_token: 'fixture-csrf', developer_login_available: state.developer });
    if (path === '/api/v1/auth/login') { state.authenticated = true; return json({ user: state.user }); }
    if (path === '/api/v1/auth/logout') { state.authenticated = false; return route.fulfill({ status: 204 }); }
    if (path === '/api/v1/health') return json({ status: 'degraded', checks: { db: { state: 'ready' }, model: { state: 'unknown' } }, limits: { sample_available: false } });
    if (path === '/api/v1/projects') return method === 'POST' ? json({ ...state.project, name: request.postDataJSON().name }, 201) : json(pageDto([state.project]));
    if (path === `/api/v1/projects/${ids.project}`) {
      if (state.projectError) return error('PROJECT_PERMISSION_DENIED', state.projectError);
      if (method === 'PATCH') { const value = request.postDataJSON(); state.project = { ...state.project, ...(value.name ? { name: value.name } : {}), ...(value.archived !== undefined ? { business_state: value.archived ? 'archived' : 'active' } : {}) }; }
      return json(state.project);
    }
    if (path === `/api/v1/images/${ids.image}/content`) return route.fulfill({ contentType: 'image/png', body: png });
    if (path === `/api/v1/images/${ids.image}`) return json(state.image);
    if (path === `/api/v1/projects/${ids.project}/images`) return method === 'POST' ? json(state.image, 201) : json(pageDto([state.image]));
    if (path === `/api/v1/projects/${ids.project}/criteria-sets`) return method === 'POST' ? json({ ...state.set, name: request.postDataJSON().name }, 201) : json(pageDto([state.set]));
    if (path === `/api/v1/projects/${ids.project}/criteria-drafts`) {
      if (method === 'POST') { state.draft = { ...state.draft, ...request.postDataJSON(), revision: '1' }; return json(state.draft, 201); }
      return json(pageDto([state.draft]));
    }
    if (path === `/api/v1/projects/${ids.project}/criteria-drafts/${ids.draft}`) {
      if (method === 'PUT') {
        if (state.conflict) { state.draft = { ...state.draft, raw_requirements: '다른 담당자의 최신 요구사항', revision: '9007199254740994' }; return error('DRAFT_REVISION_CONFLICT', 409); }
        const { expected_revision, ...content } = request.postDataJSON(); assert.equal(expected_revision, state.draft.revision); state.draft = { ...state.draft, ...content, revision: String(BigInt(expected_revision) + 1n) };
      }
      return json(state.draft);
    }
    if (path === `/api/v1/projects/${ids.project}/criteria-versions`) {
      if (method === 'GET') return json(pageDto([state.criteria]));
      if (state.confirmStatus !== 200) return error('PROJECT_PERMISSION_DENIED', state.confirmStatus);
      state.criteria = { ...state.criteria, source_draft_revision: request.postDataJSON().expected_revision };
      if (state.loseConfirm) { state.loseConfirm = false; return route.abort('connectionreset'); }
      return json(state.criteria);
    }
    if (path === `/api/v1/projects/${ids.project}/criteria-versions/${ids.version}`) return json(state.criteria);
    if (path === '/api/v1/reviews' && method === 'POST') { state.reads = 0; return json(state.receipt, 202); }
    if (path === `/api/v1/projects/${ids.project}/reviews`) { if (state.historyPages && !url.searchParams.has('cursor')) return json(pageDto([state.run], { has_more: true, next_cursor: 'opaque-history-next' })); return json(pageDto([{ ...state.run, ...(state.historyPages ? { id: ids.run2 } : {}) }])); }
    if (path === `/api/v1/reviews/${ids.run}` || path === `/api/v1/reviews/${ids.run2}`) {
      state.reads++; if (state.runError) return state.runError === 1 ? route.abort() : error('AUTHENTICATION_REQUIRED', state.runError);
      if (state.poll && state.reads === 1) return json({ ...state.run, status: 'running', stage: 'ocr', progress: 25, results: [], results_complete: false, overall_verdict: null, ocr: null });
      return json({ ...state.run, id: path.endsWith(ids.run2) ? ids.run2 : ids.run });
    }
    if (path === `/api/v1/reviews/${ids.run}/retry`) return json({ ...state.receipt, id: ids.run2, retry_of: ids.run }, 202);
    if (path === `/api/v1/reviews/${ids.run}/results/${ids.result}/evidence`) { if (state.evidencePages && !url.searchParams.has('cursor')) return json(pageDto([state.evidence], { availability: 'available', evidence_count: 2, has_more: true, next_cursor: 'opaque-evidence-next' })); return json(pageDto([{ ...state.evidence, ...(state.evidencePages ? { id: uid(80), text: '두 번째 근거' } : {}) }], { availability: 'available', evidence_count: state.evidencePages ? 2 : 1 })); }
    if (path === `/api/v1/reviews/${ids.run}/ocr-records`) return json(pageDto([state.token], { availability: 'available', record_count: 1 }));
    if (path === `/api/v1/reviews/${ids.run}/export`) return json({ ...state.run, contract_version: 'cc-review-export-v1', exported_at: '2026-10-18T02:00:00Z', server_only: true });
    state.unexpected.push(`${method} ${path}`); return route.abort();
  });
  t.after(async () => { await context.close(); assert.deepEqual(errors, [], 'Unhandled browser errors'); assert.deepEqual(state.unexpected, [], 'No legacy or external API requests'); });
  return { context, page, state };
}
const posts = (state, suffix) => state.requests.filter(row => row.method === 'POST' && row.path.endsWith(suffix));
async function login(page, developer = false) {
  if (developer) await page.getByRole('button', { name: '개발 계정으로 로그인', exact: true }).click();
  await page.getByLabel(developer ? '개발 계정 아이디' : '이메일', { exact: true }).fill(developer ? 'admin' : 'reviewer@example.test');
  await page.getByLabel('비밀번호', { exact: true }).fill(developer ? 'admin' : ' fixture-password '); await page.getByRole('button', { name: '로그인', exact: true }).click();
}
async function capture(page, name) { if (!process.env.E2E_SCREENSHOT_DIR) return; await mkdir(process.env.E2E_SCREENSHOT_DIR, { recursive: true }); await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: `${process.env.E2E_SCREENSHOT_DIR}/${name}.png`, fullPage: true, animations: 'disabled' }); }

// Result details live in one inspector; select the intended row before assertions.
async function selectResult(page, criterion) {
  const row = page.getByRole('region', { name: '검수 항목 목록', exact: true }).getByRole('button').filter({ hasText: criterion });
  await row.click();
  assert.equal(await row.getAttribute('aria-pressed'), 'true');
  const inspector = page.getByRole('complementary', { name: '선택한 검수 항목', exact: true });
  await inspector.getByRole('heading', { name: criterion, exact: true }).waitFor();
  return inspector;
}

test('email login returns to exact UUID URL, keeps password private, logout clears pending', async t => {
  const { page, state } = await setup(t, { authenticated: false }); await page.goto(baseURL + draftPath); await page.waitForURL('**/login'); await login(page); await page.waitForURL('**' + draftPath); await page.getByLabel('요구사항 원문').waitFor();
  assert.deepEqual(JSON.parse(posts(state, '/login')[0].body), { email: 'reviewer@example.test', password: ' fixture-password ' }); assert.equal(posts(state, '/login')[0].headers['x-csrftoken'], 'fixture-csrf');
  assert.equal(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }).includes('fixture-password')), false);
  await page.getByRole('button', { name: '로그아웃', exact: true }).click(); await page.waitForURL('**/login'); assert.equal(state.authenticated, false); assert.equal(await page.evaluate(() => sessionStorage.getItem('cc.v1.pending.v1')), null);
});
test('admin/admin developer login remains explicit and can be disabled by the server', async t => {
  const { page, state } = await setup(t, { authenticated: false }); await page.goto(baseURL + '/login'); await login(page, true); await page.waitForURL('**/projects'); assert.deepEqual(JSON.parse(posts(state, '/login')[0].body), { username: 'admin', password: 'admin' });
  state.authenticated = false; state.developer = false; await page.goto(baseURL + '/login'); await page.getByLabel('이메일', { exact: true }).waitFor(); assert.equal(await page.getByRole('button', { name: '개발 계정으로 로그인' }).count(), 0);
});
test('project creation, multipart upload, draft save, confirmation and polling use server UUIDs', async t => {
  const { page, state } = await setup(t, { poll: true }); await page.goto(baseURL + '/projects'); await page.getByLabel('새 프로젝트 이름').fill('가을 포스터 검수'); await page.getByRole('button', { name: '프로젝트 만들기', exact: true }).click(); await page.waitForURL('**/review/new');
  await page.getByLabel('새 이미지 선택').setInputFiles(new URL('./poster.png', import.meta.url).pathname); await page.getByRole('button', { name: '이미지 등록', exact: true }).click(); await page.waitForURL('**?image_id=*'); await page.getByLabel('기준 묶음', { exact: true }).selectOption(ids.set); await page.getByLabel('요구사항 원문').fill('행사명을 표시해 주세요.'); await page.getByRole('button', { name: '초안 저장하고 검토', exact: true }).click(); await page.waitForURL('**' + draftPath);
  await page.getByRole('button', { name: '저장된 기준 확정', exact: true }).click(); await page.waitForURL('**' + criteriaPath); await page.getByText('서버 기준 v37', { exact: true }).waitFor(); await page.getByRole('button', { name: '이 기준으로 검수 시작', exact: true }).click(); await page.waitForURL('**' + runPath); await page.getByText('문자 인식 · 25%', { exact: true }).waitFor(); await page.getByText('결과 저장 완료 · 100%', { exact: true }).waitFor();
  const runPost = posts(state, '/api/v1/reviews'); assert.equal(runPost.length, 1); assert.deepEqual(JSON.parse(runPost[0].body), { project_id: ids.project, image_id: ids.image, criteria_version_id: ids.version }); assert.match(runPost[0].headers['idempotency-key'], /^[0-9a-f-]{36}$/); assert.equal(posts(state, '/images')[0].headers['content-type'].startsWith('multipart/form-data; boundary='), true); assert.equal(posts(state, '/criteria-drafts').length, 1); await capture(page, 'run-desktop');
});
test('draft dirty guard, revision save and 409 comparison preserve the editing buffer', async t => {
  const { page, state } = await setup(t); await page.goto(baseURL + draftPath); await page.getByLabel('검수 문구').fill('직접 편집한 행사명'); assert.equal(await page.getByRole('button', { name: '저장된 기준 확정', exact: true }).isDisabled(), true);
  await page.getByRole('link', { name: '검수 이력', exact: true }).click(); await page.getByRole('alertdialog').waitFor(); await page.getByRole('button', { name: '계속 편집', exact: true }).click(); assert.equal(await page.getByLabel('검수 문구').inputValue(), '직접 편집한 행사명');
  page.once('dialog', dialog => dialog.dismiss()); await page.getByRole('button', { name: '로그아웃', exact: true }).click(); assert.equal(posts(state, '/logout').length, 0);
  state.conflict = true; await page.getByRole('button', { name: '변경 내용 저장', exact: true }).click(); await page.getByRole('heading', { name: '서버 초안이 바뀌었습니다.' }).waitFor(); assert.equal(await page.getByLabel('검수 문구').inputValue(), '직접 편집한 행사명');
  await page.getByRole('button', { name: '내 편집을 버리고 최신본 불러오기', exact: true }).click(); assert.equal(await page.getByLabel('요구사항 원문').inputValue(), '다른 담당자의 최신 요구사항'); state.conflict = false;
  await page.getByLabel('검수 문구').fill('다시 검토한 문구'); await page.getByRole('button', { name: '변경 내용 저장', exact: true }).click(); await page.getByText('서버에 저장했습니다.', { exact: true }).waitFor(); assert.equal(state.draft.revision, '9007199254740995'); assert.equal(await page.getByRole('button', { name: '저장된 기준 확정', exact: true }).isEnabled(), true); await capture(page, 'draft-desktop');
});
test('lost confirmation response survives refresh and manual replay reuses exact key/body', async t => {
  const { page, state } = await setup(t, { loseConfirm: true }); await page.goto(baseURL + draftPath); await page.getByRole('button', { name: '저장된 기준 확정', exact: true }).click(); await page.getByRole('alert').waitFor(); await page.getByRole('button', { name: '같은 요청으로 결과 확인', exact: true }).waitFor(); const first = posts(state, '/criteria-versions')[0];
  await page.reload(); await page.getByRole('button', { name: '같은 요청으로 결과 확인', exact: true }).waitFor(); assert.equal(posts(state, '/criteria-versions').length, 1); await page.getByRole('button', { name: '같은 요청으로 결과 확인', exact: true }).click(); await page.waitForURL('**' + criteriaPath); const second = posts(state, '/criteria-versions')[1]; assert.equal(second.headers['idempotency-key'], first.headers['idempotency-key']); assert.equal(second.body, first.body); assert.equal(await page.evaluate(() => sessionStorage.getItem('cc.v1.pending.v1')), null);
});
test('run deep link and refresh are GET-only; selected evidence and OCR load lazily in display coordinates', async t => {
  const f = fixtures();
  const second = { ...f.run.results[0], result_id: uid(81), criteria_item_id: uid(82), criterion_id: 'requirement-2', criterion: '로고 배치 확인', type: 'visual', verdict: 'review', status: 'review', observed_text: '두 번째 항목의 관찰 값', observed_value: null, reason: '두 번째 항목은 사람이 확인해야 합니다.', evidence_count: 0 };
  f.run.results.push(second); f.run.criteria.push({ ...f.run.criteria[0], criteria_item_id: second.criteria_item_id, id: second.criterion_id, position: 2, text: second.criterion, type: second.type }); f.run.overall_verdict = 'review';
  const { page, state } = await setup(t, f); await page.goto(baseURL + runPath); await page.getByRole('heading', { name: '항목별 자동 판정', exact: true }).waitFor(); assert.equal(state.requests.some(row => row.path.endsWith('/evidence')), false);
  let inspector = await selectResult(page, f.run.results[0].criterion);
  assert.equal(await inspector.getByText(second.observed_text, { exact: true }).count(), 0);
  assert.equal(state.requests.some(row => row.path.endsWith('/evidence')), false, 'Selecting a result must not fetch evidence automatically');
  await inspector.getByRole('button', { name: '근거 1개 조회', exact: true }).click(); await inspector.getByText('신뢰도: 미등록', { exact: true }).waitFor(); await inspector.getByRole('button', { name: '이미지에서 위치 보기', exact: true }).click(); assert.equal(await page.getByTestId('evidence-box').evaluate(node => node.style.left), '10%'); assert.equal(await page.getByTestId('evidence-box').evaluate(node => node.style.width), '80%');
  inspector = await selectResult(page, second.criterion);
  await inspector.getByText(second.observed_text, { exact: true }).waitFor(); await inspector.getByText(second.reason, { exact: true }).waitFor(); assert.equal(await inspector.getByText('확인 필요', { exact: true }).count(), 1);
  assert.equal(await inspector.getByText('신뢰도: 미등록', { exact: true }).count(), 0, 'The previous result evidence must not remain in the selected inspector');
  assert.equal(await page.getByTestId('evidence-box').count(), 0, 'Changing the selected result clears its old image overlay');
  assert.equal(state.requests.filter(row => row.path.endsWith('/evidence')).length, 1, 'Changing selection must not fetch another result evidence');
  inspector = await selectResult(page, f.run.results[0].criterion); await inspector.getByRole('button', { name: '근거 1개 조회', exact: true }).click(); await inspector.getByRole('button', { name: '이미지에서 위치 보기', exact: true }).click(); await page.getByTestId('evidence-box').waitFor();
  await page.getByRole('button', { name: 'OCR 원문 조회', exact: true }).click(); const ocrInspector = page.getByRole('complementary', { name: '문자 인식 원문', exact: true }); await ocrInspector.getByText('신뢰도: 96.0%', { exact: true }).waitFor(); assert.equal(await page.getByTestId('evidence-box').count(), 0);
  assert.equal(await ocrInspector.getByRole('heading', { name: f.run.results[0].criterion, exact: true }).count(), 0);
  await page.reload(); await page.getByRole('heading', { name: '항목별 자동 판정', exact: true }).waitFor(); assert.equal(state.requests.filter(row => row.method !== 'GET').length, 0);
  await page.setViewportSize({ width: 390, height: 844 }); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true); await capture(page, 'run-mobile');
});

test('wrong-frame evidence is text-only and imported records never get a retry or invented pass', async t => {
  const f = fixtures(); f.evidence.frame_id = uid(90); f.run.status = 'imported'; f.run.origin_kind = 'external_import'; f.run.overall_verdict = null; f.run.results_complete = false; f.run.results[0].processing_status = 'imported'; f.run.results[0].verdict = null;
  const { page } = await setup(t, f); await page.goto(baseURL + runPath); await page.getByText('가져온 과거 이력입니다.', { exact: false }).waitFor(); assert.equal(await page.getByRole('button', { name: '같은 기준으로 재검수', exact: true }).isDisabled(), true); const inspector = await selectResult(page, f.run.results[0].criterion); await inspector.getByRole('button', { name: '근거 1개 조회', exact: true }).click(); assert.equal(await inspector.getByRole('button', { name: '이미지에서 위치 보기', exact: true }).isDisabled(), true); assert.equal(await page.getByTestId('evidence-box').count(), 0); assert.equal(await page.getByText('서버 자동 요약: 판정 없음', { exact: false }).count(), 1);
});
test('network errors retain last running state and manual retry is a GET, never a new execution', async t => {
  const { page, state } = await setup(t, { poll: true }); await page.goto(baseURL + runPath); await page.getByText('문자 인식 · 25%', { exact: true }).waitFor(); state.runError = 1; await page.getByText('실행 상태를 다시 확인해야 합니다.', { exact: true }).waitFor(); assert.equal(await page.getByText('문자 인식 · 25%', { exact: true }).count(), 1); state.runError = 0; await page.getByRole('button', { name: '실행 상태 다시 조회', exact: true }).click(); await page.getByText('결과 저장 완료 · 100%', { exact: true }).waitFor(); assert.equal(posts(state, '/api/v1/reviews').length, 0);
});
test('viewer and archived project disable writes, stale criteria disables admission', async t => {
  const f = fixtures(); f.project.my_role = 'viewer'; f.project.business_state = 'archived'; f.criteria.draft_is_current = false;
  const { page, state } = await setup(t, f); await page.goto(baseURL + draftPath); assert.equal(await page.getByLabel('검수 문구').isDisabled(), true); await page.goto(baseURL + criteriaPath); assert.equal(await page.getByRole('button', { name: '이 기준으로 검수 시작', exact: true }).isDisabled(), true); await page.goto(baseURL + runPath); assert.equal(await page.getByRole('button', { name: '같은 기준으로 재검수', exact: true }).isDisabled(), true); assert.equal(state.requests.filter(row => row.method !== 'GET').length, 0);
});
test('403 preserves session, 401 reauth retains same actor pending, different actor purges it', async t => {
  const { page, state } = await setup(t, { loseConfirm: true }); await page.goto(baseURL + draftPath); await page.getByRole('button', { name: '저장된 기준 확정', exact: true }).click(); await page.getByRole('alert').waitFor(); await page.getByRole('button', { name: '같은 요청으로 결과 확인', exact: true }).waitFor(); state.confirmStatus = 403; await page.getByRole('button', { name: '같은 요청으로 결과 확인', exact: true }).click(); await page.getByRole('alert').waitFor(); assert.equal(new URL(page.url()).pathname, draftPath);
  state.runError = 401; state.authenticated = false; await page.goto(baseURL + runPath); await page.waitForURL('**/login'); state.runError = 0; await login(page); await page.waitForURL('**' + runPath); await page.getByRole('button', { name: '같은 요청으로 결과 확인', exact: true }).waitFor();
  state.authenticated = false; state.runError = 401; await page.reload(); await page.waitForURL('**/login'); state.user = { ...state.user, id: uid(90) }; state.runError = 0; await login(page); await page.waitForURL('**' + runPath); await page.getByRole('heading', { name: '항목별 자동 판정', exact: true }).waitFor(); assert.equal(await page.getByRole('button', { name: '같은 요청으로 결과 확인', exact: true }).count(), 0);
});
test('server export and explicit retry keep download/source identities', async t => {
  const { page, state } = await setup(t); await page.goto(baseURL + runPath); const download = page.waitForEvent('download'); await page.getByRole('button', { name: '서버 결과 JSON 다운로드', exact: true }).click(); const file = await download; const saved = JSON.parse(await readFile(await file.path(), 'utf8')); assert.equal(saved.server_only, true); assert.equal(saved.id, ids.run);
  await page.getByRole('button', { name: '같은 기준으로 재검수', exact: true }).click(); await page.waitForURL('**/reviews/' + ids.run2); assert.equal(posts(state, '/retry').length, 1); assert.deepEqual(JSON.parse(posts(state, '/retry')[0].body), {}); assert.equal(posts(state, '/api/v1/reviews').length, 0);
});
test('invalid route and unavailable session cannot silently become demo data', async t => {
  const { page, state } = await setup(t); await page.goto(baseURL + '/projects/not-a-uuid/reviews/legacy-id'); await page.getByRole('heading', { name: '페이지를 찾을 수 없습니다.' }).waitFor(); assert.equal(state.requests.some(row => row.path.includes('not-a-uuid')), false);
  state.meError = true; await page.goto(baseURL + '/projects'); await page.getByRole('heading', { name: '로그인 상태를 확인할 수 없어요.' }).waitFor(); assert.equal(await page.getByText('샘플').count(), 0);
});

test('history keyset pagination and filters survive reload/back without mutations', async t => {
  const { page, state } = await setup(t, { historyPages: true }); await page.goto(baseURL + projectPath + '/reviews'); await page.getByRole('link', { name: '다음 페이지', exact: true }).click(); await page.waitForURL('**?cursor=opaque-history-next'); await page.reload(); await page.getByText(ids.run2, { exact: false }).waitFor(); assert.equal(new URL(state.requests.filter(row => row.path.endsWith('/reviews')).at(-1).path, baseURL).pathname, '/api/v1/projects/' + ids.project + '/reviews');
  await page.getByLabel('검수 상태 필터').selectOption('failed'); await page.waitForURL('**?status=failed'); await page.goBack(); await page.waitForURL('**?cursor=opaque-history-next'); assert.equal(state.requests.filter(row => row.method !== 'GET').length, 0);
});
test('evidence pagination completes lazily and mobile actions keep readable widths', async t => {
  const f = fixtures(); f.run.results[0].evidence_count = 2; const { page, state } = await setup(t, { ...f, evidencePages: true }); await page.goto(baseURL + runPath); const inspector = await selectResult(page, f.run.results[0].criterion); await inspector.getByRole('button', { name: '근거 2개 조회', exact: true }).click(); await inspector.getByText('1 / 2개 조회 · 일부 조회됨', { exact: true }).waitFor(); await inspector.getByRole('button', { name: '근거 더 불러오기', exact: true }).click(); await inspector.getByText('2 / 2개 조회 · 전체 조회됨', { exact: true }).waitFor(); assert.equal(state.requests.filter(row => row.path.endsWith('/evidence')).length, 2);
  await capture(page, 'evidence-desktop'); await page.setViewportSize({ width: 390, height: 844 }); const button = page.getByRole('button', { name: '서버 결과 JSON 다운로드', exact: true }); const bounds = await button.boundingBox(); assert.ok(bounds.width > 250 && bounds.height < 80, 'Mobile action must not wrap into a vertical column of letters'); await capture(page, 'evidence-mobile');
});
