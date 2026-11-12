// 관계 조회 안내 두 가지: 기준 확인 화면의 '이전 기준과 달라진 항목', 검수 결과 화면의 '수정본 검수 이력'.
// API는 모두 브라우저 라우트로 흉내 낸다(실제 PostgreSQL·Neo4j 응답 검증이 아니다).
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const { chromium } = createRequire(import.meta.url)('playwright');
const root = fileURLToPath(new URL('../../', import.meta.url));
const base = process.env.E2E_REQUIREMENTS_URL || 'http://127.0.0.1:5177';
const shots = process.env.E2E_SCREENSHOT_DIR;

async function open(t, routes) {
  if (!process.env.E2E_REQUIREMENTS_URL) {
    const child = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5177', '--strictPort'], {
      cwd: root, stdio: 'pipe', env: { ...process.env, VITE_API_PROFILE: 'legacy' },
    });
    let output = ''; child.stdout.on('data', data => { output += data; }); child.stderr.on('data', data => { output += data; });
    t.after(() => child.exitCode === null ? new Promise(resolve => { child.once('exit', resolve); child.kill(); }) : undefined);
    let ready = false;
    for (let i = 0; i < 80; i++) {
      if (child.exitCode !== null) throw new Error(output);
      try { if ((await fetch(base)).ok) { ready = true; break; } } catch {}
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.ok(ready, output);
  }
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'msedge' });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(10000);
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const requested = [];
  await page.route(base + '/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    requested.push(path);
    if (path === '/api/v1/auth/me') return route.fulfill({ json: { authenticated: true, user: { id: 'user-fixture', display_name: '개발 검증' } } });
    if (path === '/api/health') return route.fulfill({ json: { status: 'ok', ready: true } });
    if (path.endsWith('/content')) return route.fulfill({ contentType: 'image/svg+xml',
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="140"><rect width="100" height="140" fill="#dde"/><text y="40">POSTER</text></svg>' });
    const body = routes[path.replace('/api/framework/v1', '')];
    return body === undefined ? route.fulfill({ status: 404, json: { detail: 'unexpected test route' } }) : route.fulfill({ json: body });
  });
  return { page, errors, requested };
}

const criterion = (id, text, expected, field, label) => ({
  criterion_id: id, text, kind: 'text', expected, operator: 'contains', unit: null, required: true, human_added: false,
  source: { document_version_id: 'doc-v2', page: 1, block_id: 'b1', quote: text, source_hash: 'a'.repeat(64) },
  details: { target: 'poster_text', field, presentation: { label, value: expected } },
});

test('새 문서의 기준이 달라지면 달라진 항목과 이전 기준으로 한 검수를 알려 준다', { timeout: 60000 }, async t => {
  const context = { run_id: 'criteria-fixture', project_id: 'project-fixture', actor_id: 'user-fixture',
    document_version_id: 'doc-v2', criteria_version_id: 'draft' };
  const criteria = [criterion('c001', '행사일: 11월 20일', '11월 20일', 'event_date', '행사일'),
    criterion('c002', '행사 장소: 시민공원', '시민공원', 'venue', '행사 장소')];
  const { page, errors, requested } = await open(t, {
    '/runs/criteria-fixture': { context, execution_status: 'waiting_criteria', human_decision: 'unreviewed', approvals: [],
      criteria: { context, criteria, extraction: { rejected_candidates: [] },
        warnings: ['unapproved_draft', 'page_2_embedded_images_not_read'] } },
    '/runs/criteria-fixture/criteria-review': { source_draft_sha256: 'b'.repeat(64), latest_version: null,
      approved_criteria_version_id: null, synthetic: false, execution_status: 'waiting_criteria' },
    '/runs/criteria-fixture/criteria-change-impact': {
      run_id: 'criteria-fixture', answered_by: 'postgres_fallback', graph: { status: 'unavailable' }, warnings: ['GRAPH_NOT_CONFIGURED'],
      current: { document_version_id: 'doc-v2', criteria_version_id: null, version_no: null, criteria: 2 },
      previous: { basis: 'project_previous', criteria_version_id: 'criteria-v1', version_no: 1, source_run_id: 'run-a' },
      changes: [{ change: 'changed', target: 'poster_text', field: 'venue', label: '행사 장소',
        before: { criterion_id: 'c001', text: '행사 장소: 시청 광장', expected: '시청 광장', unit: null },
        after: { criterion_id: 'c002', text: '행사 장소: 시민공원', expected: '시민공원', unit: null } }],
      unchanged: 1, counts: { changed: 1 }, truncated: false,
      summary: { affected_runs: 3, approved: 2, by_status: { completed: 2, waiting_review: 1 } },
      affected_runs: [
        { run_id: 'poster-1', execution_status: 'completed', human_decision: 'final_approved', created_at: '2026-11-01T02:00:00+00:00' },
        { run_id: 'poster-2', execution_status: 'completed', human_decision: 'exception_approved', created_at: '2026-11-03T02:00:00+00:00' },
        { run_id: 'poster-3', execution_status: 'waiting_review', human_decision: 'unreviewed', created_at: '2026-11-05T02:00:00+00:00' }],
    },
  });
  await page.goto(base + '/review/new?framework=' + context.run_id);
  const notice = page.getByRole('region', { name: '이전 기준과 달라진 항목' });
  await notice.getByText('행사 장소가 변경됐어요.', { exact: true }).waitFor();
  await notice.getByText('이전 기준을 사용한 포스터 검수 3건 중 2건이 승인됐어요.', { exact: true }).waitFor();
  assert.match(await notice.locator('.rv-relation-changes li').innerText(), /변경\s*행사 장소\s*시청 광장\s*→\s*시민공원/);
  const runs = notice.locator('.rv-relation-runs');
  assert.equal(await runs.locator('li').first().isVisible(), false);
  await runs.getByText('영향받는 검수 3건 보기', { exact: true }).click();
  assert.deepEqual(await runs.locator('.rv-relation-status').allInnerTexts(), ['최종 승인', '예외 승인', '승인 대기']);
  assert.equal(await runs.getByRole('link').first().getAttribute('href'), '?framework=poster-1');
  // 이미지가 섞인 쪽은 글자층만 읽었다고 알린다
  await page.getByRole('status', { name: '읽지 않은 이미지 안내' })
    .getByText('2쪽의 이미지 안 내용은 읽지 않았어요.', { exact: true }).waitFor();
  // 안내는 기준 검토를 막지 않는다
  assert.ok(await page.getByRole('button', { name: '기준 확인하고 승인', exact: true }).isEnabled());
  assert.equal(requested.filter(path => path.endsWith('/revision-history')).length, 0);
  if (shots) await page.screenshot({ path: shots + '/change-impact.png' });
  assert.deepEqual(errors, []);
});

test('원문 검증에서 제외된 후보는 안내 한 상자로 접어 두고, 펴면 사유별로 묶어 보여 준다', { timeout: 60000 }, async t => {
  const context = { run_id: 'criteria-rejected', project_id: 'project-fixture', actor_id: 'user-fixture',
    document_version_id: 'doc-v2', criteria_version_id: 'draft' };
  const criteria = [criterion('c001', '행사일: 11월 20일', '11월 20일', 'event_date', '행사일'),
    criterion('c002', '행사 장소: 시민공원', '시민공원', 'venue', '행사 장소')];
  const logoRule = '로고는 흰 배경에서만 사용하며, 주최 기관이 따로 승인한 경우에 한해 단색 배경에도 쓸 수 있다. 승인 내역은 담당자가 별도로 보관한다.';
  const rejected = (index, code, page, quote) => ({ candidate_index: index, code,
    source: quote ? { document_version_id: 'doc-v2', page, block_id: 'b' + page, quote, source_hash: 'a'.repeat(64) } : null });
  const rejections = [
    ...[1, 2, 3, 4, 5, 6, 7, 8, 9].map(index => rejected(index, 'A02_QUALIFIER_UNGROUNDED', 1, index < 8 ? logoRule : '행사명은 한 줄로 적는다.')),
    rejected(16, 'A02_PRESENTATION_UNGROUNDED', 2, '후원 기관은 가나다순으로 적는다.'),
    ...[22, 23, 24, 25].map(index => rejected(index, 'A02_RESPONSE_INVALID', 2, '후원 기관은 가나다순으로 적는다.')),
    rejected(26, 'A02_RESPONSE_INVALID')];
  const { page, errors } = await open(t, {
    '/runs/criteria-rejected': { context, execution_status: 'waiting_criteria', human_decision: 'unreviewed', approvals: [],
      criteria: { context, criteria, extraction: { rejected_candidates: rejections }, warnings: ['unapproved_draft'] } },
    '/runs/criteria-rejected/criteria-review': { source_draft_sha256: 'b'.repeat(64), latest_version: null,
      approved_criteria_version_id: null, synthetic: false, execution_status: 'waiting_criteria' },
  });
  await page.goto(base + '/review/new?framework=' + context.run_id);
  const notice = page.getByRole('region', { name: '원문 검증에서 제외된 후보' });
  await notice.getByText('원문 검증에서 후보 15개를 제외했어요.', { exact: true }).waitFor();
  // 접힌 상태에서는 안내 한 상자뿐이라 기준 목록을 밀어내지 않는다
  const list = notice.locator('.rv-rejection-list');
  assert.equal(await list.getAttribute('open'), null);
  assert.equal(await notice.locator('.rv-rejection-reason').first().isVisible(), false);
  assert.ok((await notice.boundingBox()).height < 140, 'collapsed notice stays compact');
  const checklist = page.getByRole('list', { name: '검수 항목 목록' });
  assert.equal(await checklist.getByRole('checkbox').count(), 2);
  assert.ok(await checklist.getByRole('checkbox').first().evaluate(element => element.getBoundingClientRect().top < innerHeight));
  if (shots) await page.screenshot({ path: shots + '/rejections-collapsed.png' });

  // 펴면 사유마다 한 묶음, 같은 원문은 한 줄
  await notice.getByText('제외한 후보 사유별로 보기', { exact: true }).click();
  assert.deepEqual((await notice.locator('.rv-rejection-reason').allInnerTexts()).map(text => text.replace(/\s+/g, ' ')), [
    '허용 조건이나 예외의 원문 근거가 일치하지 않습니다. 9개',
    '표시 문구가 원문과 일치하지 않습니다. 1개',
    '항목의 형식이나 비교 조건이 유효하지 않습니다. 5개']);
  // 사유 코드는 화면에 적지 않고 마우스를 올렸을 때만 보인다
  assert.equal(await notice.getByText(/A02_/).count(), 0);
  assert.deepEqual(await notice.locator('.rv-rejection-reason').evaluateAll(nodes => nodes.map(node => node.title)),
    ['A02_QUALIFIER_UNGROUNDED', 'A02_PRESENTATION_UNGROUNDED', 'A02_RESPONSE_INVALID']);
  const groups = list.locator('> ul > li');
  assert.deepEqual(await groups.nth(0).locator('.rv-rejection-candidates').allInnerTexts(), ['후보 1–7', '후보 8–9']);
  assert.deepEqual((await groups.nth(2).locator('.rv-rejection-source').allInnerTexts()).map(text => text.replace(/\s+/g, ' ')),
    ['2쪽 후원 기관은 가나다순으로 적는다. 후보 22–25', '원문 없음 확인 가능한 원문이 없습니다. 후보 26']);
  if (shots) await page.screenshot({ path: shots + '/rejections-open.png' });
  // 긴 원문은 한 줄로 줄여 두고, 누르면 전문이 펴진다
  const first = groups.nth(0).locator('details.rv-rejection-source').first();
  assert.ok(await first.locator('.rv-rejection-preview').evaluate(element => element.scrollWidth > element.clientWidth), 'long quote is clipped to one line');
  assert.ok((await first.boundingBox()).height < 40);
  assert.equal(await first.locator('> p').isVisible(), false);
  await first.locator('summary').click();
  assert.equal(await first.locator('> p').innerText(), logoRule);
  assert.equal(await first.locator('.rv-rejection-preview').isVisible(), false);
  // 제외된 원문은 기준 목록에 섞이지 않고, 기준 검토를 막지 않는다
  assert.equal(await checklist.getByText('후원 기관은 가나다순으로 적는다.').count(), 0);
  assert.ok(await page.getByRole('button', { name: '기준 확인하고 승인', exact: true }).isEnabled());
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  if (shots) await page.screenshot({ path: shots + '/rejections-quote.png' });
  assert.deepEqual(errors, []);
});

test('검수 결과 화면에서 수정본 검수 이력을 펼쳐 본다', { timeout: 60000 }, async t => {
  const context = { run_id: 'poster-2', project_id: 'project-fixture', actor_id: 'user-fixture', asset_version_id: 'asset-2',
    parent_run_id: 'poster-1' };
  const record = (id, decision, reason) => ({ approval_id: id, decision, reason, actor_id: 'user-fixture', approved_at: '2026-11-05T03:00:00+00:00' });
  const entry = (number, state, records, extra = {}) => ({ run_id: 'poster-' + number, revision_no: number, parent_run_id: number ? 'poster-' + (number - 1) : null,
    current: number === 2, asset_version_id: 'asset-' + number, criteria_version_id: 'criteria-v1', criteria_version_no: 1,
    execution_status: 'completed', machine_verdict: 'pass', human_decision: 'unreviewed', state,
    created_at: `2026-11-0${number + 1}T02:00:00+00:00`, records, ...extra });
  const { page, errors, requested } = await open(t, {
    '/runs/poster-2': { context, execution_status: 'completed', human_decision: 'final_approved', machine_verdict: 'pass',
      baseline: { criteria_version_id: 'criteria-v1' }, evidence: { status: 'complete' }, approvals: [],
      validated: { items: [{ criterion_id: 'date', criterion_text: '행사 날짜', required: true, expected: '11월 20일', observed: '11월 20일',
        verdict: 'pass', issues: [] }] },
      report: { summary: '모든 조건을 충족했습니다.', change_requests: [] } },
    '/runs/poster-2/revision-history': {
      run_id: 'poster-2', answered_by: 'neo4j', graph: { status: 'complete', graph_revision: 9, postgres_revision: 9 }, warnings: [], truncated: false,
      summary: { reviews: 3, rereviews: 2, final_approved: { run_id: 'poster-2', revision_no: 2 }, awaiting_decision: false },
      entries: [
        entry(0, 'changes_requested', [record('a1', 'approve_criteria', ''), record('a2', 'approve_changes', '행사 날짜 표기 수정을 요청했습니다.')]),
        entry(1, 'changes_requested', [record('a3', 'approve_changes', '날짜를 확인했고, 주최 로고 위치 수정을 요청했습니다.')]),
        entry(2, 'final_approved', [record('a4', 'approve_final', '수정 사항을 확인하고 2차 수정본을 최종 승인했습니다.')])],
    },
  });
  await page.goto(base + '/review/new?framework=' + context.run_id);
  const history = page.locator('.rv-revision-history');
  await history.getByText('수정본 검수 이력', { exact: true }).waitFor();
  // 펼치지 않아도 재검수 횟수와 최종 승인된 수정본이 보인다
  assert.match(await history.locator('> summary').innerText(), /재검수\s*2회[\s\S]*최종 승인된 수정본\s*2차 수정본/);
  assert.equal(await history.locator('.rv-revision-rows').isVisible(), false);
  await history.locator('> summary').click();
  const rows = history.locator('.rv-revision-rows > li');
  assert.deepEqual(await rows.locator('.rv-revision-title strong').allInnerTexts(), ['원본 검수', '1차 수정본 검수', '2차 수정본 검수']);
  assert.deepEqual(await rows.locator('.rv-relation-status').allInnerTexts(), ['수정 요청', '수정 요청', '최종 승인']);
  // 지금 보고 있는 검수는 펼쳐 두고, 다른 줄은 눌러서 연다
  await rows.nth(2).getByText('수정 사항을 확인하고 2차 수정본을 최종 승인했습니다.', { exact: false }).waitFor();
  assert.equal(await rows.nth(0).locator('.rv-revision-detail').isVisible(), false);
  await rows.nth(0).locator('summary').click();
  const first = rows.nth(0).locator('.rv-revision-detail');
  assert.match(await first.innerText(), /검수한 파일[\s\S]*적용 기준\s*검수 기준 v1[\s\S]*담당자 기록\s*수정 요청 · 행사 날짜 표기 수정을 요청했습니다\./);
  assert.equal(await first.getByText('검수 시작', { exact: false }).count(), 0);
  assert.equal(await first.getByRole('link', { name: '이 검수 결과 열기' }).getAttribute('href'), '?framework=poster-0');
  assert.equal(await first.getByRole('img').getAttribute('src'), '/api/framework/v1/images/asset-0/content');
  assert.equal(requested.filter(path => path.endsWith('/criteria-change-impact')).length, 0);
  if (shots) await page.screenshot({ path: shots + '/revision-history.png' });
  assert.deepEqual(errors, []);
});

test('검수 항목을 누르면 포스터의 읽은 위치와 근거 관계도가 창으로 열린다', { timeout: 60000 }, async t => {
  const context = number => ({ run_id: 'poster-' + number, project_id: 'project-fixture', actor_id: 'user-fixture', document_version_id: 'doc-v2',
    criteria_version_id: 'criteria-v1', asset_version_id: 'asset-' + number, parent_run_id: number ? 'poster-' + (number - 1) : null });
  const criteria = [criterion('date', '행사일: 11월 20일', '11월 20일', 'event_date', '행사일'),
    criterion('venue', '행사 장소: 시민공원', '시민공원', 'venue', '행사 장소'),
    { ...criterion('logo', '주최 기관 로고를 넣는다', '주최 기관 로고', 'logo', '로고'), kind: 'visual', operator: 'present',
      details: { target: 'poster_visual', field: 'logo', presentation: { label: '로고', value: '주최 기관 로고' } } }];
  const item = (number, id, verdict, observed, extra = {}) => {
    const source = criteria.find(entry => entry.criterion_id === id);
    return { criterion_id: id, criterion_text: source.text, kind: source.kind, operator: source.operator, required: true, expected: source.expected,
      observed, observation_status: 'observed', verdict, issues: [], ...extra };
  };
  const box = (number, bbox) => ({ image: { asset_version_id: 'asset-' + number, asset_hash: 'h'.repeat(64), bbox } });
  const run = (number, items, extra = {}) => ({ context: context(number), execution_status: 'waiting_review', human_decision: 'unreviewed', machine_verdict: 'fail',
    baseline: { criteria_version_id: 'criteria-v1' }, evidence: { status: 'complete', context_id: 'rag-fixture' }, approvals: [],
    criteria: { context: context(number), criteria }, validated: { verdict: 'fail', items },
    step_records: [{ role: 'A06', finished_at: '2026-11-12T01:20:00+00:00' }], ...extra });
  const entry = number => ({ run_id: 'poster-' + number, revision_no: number, parent_run_id: number ? 'poster-' + (number - 1) : null, current: number === 1,
    asset_version_id: 'asset-' + number, criteria_version_id: 'criteria-v1', criteria_version_no: 1, execution_status: 'waiting_review',
    machine_verdict: 'fail', human_decision: 'unreviewed', state: number ? 'awaiting_decision' : 'changes_requested',
    created_at: `2026-11-0${number + 1}T02:00:00+00:00`, records: [] });
  const { page, errors, requested } = await open(t, {
    '/runs/poster-1': run(1, [
      item(1, 'date', 'pass', '11월 20일', box(1, [.1, .08, .7, .2])),
      item(1, 'venue', 'fail', '시청광장', { issues: ['value_mismatch'], ...box(1, [.1, .7, .6, .9]) }),
      item(1, 'logo', 'review', '', { observation_status: 'unreadable', issues: ['model_review'] })]),
    '/runs/poster-0': run(0, [
      item(0, 'date', 'fail', '11월 2일', { issues: ['value_mismatch'], ...box(0, [.1, .08, .7, .2]) }),
      item(0, 'venue', 'fail', '시청 앞 광장', { issues: ['value_mismatch'], ...box(0, [.3, .5, .9, .64]) }),
      item(0, 'logo', 'review', '', { observation_status: 'unreadable', issues: ['model_review'] })]),
    '/runs/poster-1/evidence-context': { evidence: {}, sha256: 'c'.repeat(64), retrieval: { policy: 'af-approved-sources-v1', backend: 'neo4j',
      fallback_reason: null, required_revision: 9, observed_graph_revision: 9,
      source_keys: criteria.map(entry => ({ criterion_id: entry.criterion_id, source_key: `source:criteria-v1:${entry.criterion_id}:document`, source_hash: 'a'.repeat(64) })),
      relations: { run_ids: ['poster-0', 'poster-1'], approval_ids: [], truncated: false } } },
    '/runs/poster-1/revision-history': { run_id: 'poster-1', answered_by: 'neo4j', graph: { status: 'complete' }, warnings: [], truncated: false,
      summary: { reviews: 2, rereviews: 1, final_approved: null, awaiting_decision: true }, entries: [entry(0), entry(1)] },
  });
  await page.goto(base + '/review/new?framework=poster-1');
  const rows = page.locator('.rv-result-item-button');
  await rows.nth(2).waitFor();
  await page.getByText('항목을 누르면 판정 근거가 창으로 열립니다.', { exact: true }).waitFor();
  assert.equal(await page.getByRole('dialog').count(), 0);
  // 상세는 창에서 보여 주므로 목록 안에서는 펼치지 않는다
  assert.equal(await page.locator('.rv-result-inspector').count(), 0);
  assert.equal(await rows.first().getAttribute('aria-expanded'), null);
  assert.deepEqual((await rows.allInnerTexts()).map(text => text.replace(/\s+/g, ' ')),
    ['01 행사일: 11월 20일 충족', '02 행사 장소: 시민공원 위반', '03 주최 기관 로고를 넣는다 확인 필요']);
  if (shots) await page.screenshot({ path: shots + '/evidence-list.png' });
  // 다른 검수 결과는 창을 열기 전에는 읽지 않는다
  assert.equal(requested.filter(path => path.endsWith('/runs/poster-0')).length, 0);

  await rows.filter({ hasText: '행사 장소: 시민공원' }).click();
  const dialog = page.getByRole('dialog', { name: '02 행사 장소 판정 근거' });
  await dialog.waitFor();
  assert.equal(await dialog.locator('.rv-evd-head .rv-evd-chip').innerText(), '불충족');
  // 왼쪽 포스터에는 읽은 위치를 상자로 표시한다
  const poster = dialog.getByRole('region', { name: '검수 포스터' });
  const readBox = poster.getByTestId('evidence-read-box');
  await readBox.waitFor();
  assert.equal(await poster.getByRole('img').getAttribute('src'), '/api/framework/v1/images/asset-1/content');
  assert.deepEqual(await readBox.evaluate(element => { const frame = element.parentElement.getBoundingClientRect(), rect = element.getBoundingClientRect();
    return [(rect.left - frame.left) / frame.width, (rect.top - frame.top) / frame.height, rect.width / frame.width, rect.height / frame.height].map(value => Math.round(value * 100)); }),
  [10, 70, 50, 20]);
  await poster.getByText('포스터 아래쪽', { exact: true }).waitFor();
  // 상자 확대를 누르면 읽은 위치가 틀 가운데로 와서 틀 너비의 대부분을 채운다
  const boxInFrame = () => page.evaluate(() => { const box = document.querySelector('[data-testid="evidence-read-box"]').getBoundingClientRect();
    const frame = document.querySelector('.rv-evd-frame').getBoundingClientRect();
    return { width: box.width / frame.width, offset: Math.abs(box.left + box.width / 2 - frame.left - frame.width / 2) }; });
  const fitted = await boxInFrame();
  await poster.getByRole('button', { name: '상자 확대', exact: true }).click();
  await page.waitForFunction(width => document.querySelector('[data-testid="evidence-read-box"]').getBoundingClientRect().width
    / document.querySelector('.rv-evd-frame').getBoundingClientRect().width > width * 1.5, fitted.width);
  await page.waitForTimeout(400);
  const zoomed = await boxInFrame();
  assert.ok(zoomed.width > .68 && zoomed.width < .86 && zoomed.offset < 3, JSON.stringify(zoomed));
  if (shots) await page.screenshot({ path: shots + '/evidence-dialog-zoom.png' });
  await poster.getByRole('button', { name: '전체 보기', exact: true }).click();
  await page.waitForFunction(width => Math.abs(document.querySelector('[data-testid="evidence-read-box"]').getBoundingClientRect().width
    / document.querySelector('.rv-evd-frame').getBoundingClientRect().width - width) < .005, fitted.width);
  // 오른쪽에는 기준 요약과 근거 관계도
  const main = dialog.getByRole('region', { name: '판정 근거' });
  assert.match(await main.locator('.rv-evd-criterion').innerText(), /행사 장소: 시민공원[\s\S]*기대값\s*‘시민공원’[\s\S]*읽은 값\s*‘시청광장’[\s\S]*값이 기준과 다름/);
  const graph = main.getByRole('group', { name: '행사 장소 근거 관계도' });
  await graph.getByRole('button', { name: /이전 검수 원본 · 불충족/ }).waitFor();
  assert.equal(requested.filter(path => path.endsWith('/runs/poster-0')).length, 1);
  assert.deepEqual(await graph.locator('.rv-evg-node.is-chain .rv-evg-title').allInnerTexts(),
    ['1차 수정본 포스터', '포스터 아래쪽', '‘시청광장’', '불충족', '행사 장소', '요구사항 문서 1쪽']);
  assert.deepEqual(await graph.locator('.rv-evg-edge.is-chain > span').allInnerTexts(), ['영역', '읽음', '근거', '기준', '출처']);
  await main.getByText('노드 9 · 관계 10', { exact: true }).waitFor();
  // 노드가 선과 겹치지 않고 서로도 겹치지 않는다
  assert.ok(await graph.evaluate(element => { const boxes = [...element.querySelectorAll('.rv-evg-node')].map(node => node.getBoundingClientRect());
    return boxes.every((a, i) => boxes.every((b, j) => i === j || a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top)); }));
  // 처음에는 판정 노드가 선택돼 있고, 다른 노드를 누르면 그 노드의 근거가 보인다
  const detail = main.getByRole('region', { name: '선택한 노드' });
  assert.match(await detail.innerText(), /판정[\s\S]*불충족 · 필수 조건[\s\S]*기준 ‘시민공원’ ↔ 읽은 값 ‘시청광장’[\s\S]*값이 기준과 다름/);
  if (shots) await page.screenshot({ path: shots + '/evidence-dialog-fail.png' });
  await graph.getByRole('button', { name: /요구사항 원문/ }).click();
  assert.match(await detail.innerText(), /요구사항 문서 1쪽[\s\S]*행사 장소: 시민공원[\s\S]*그래프 노드\s*source/);
  await main.getByRole('button', { name: '전체 관계', exact: true }).click();
  assert.equal(await graph.getAttribute('class'), 'rv-evg is-all');
  assert.equal(await main.getByRole('button', { name: /조회 정보/ }).count(), 0);
  await main.getByText('기준과 원문의 관계는 Neo4j 그래프에서 조회했고, 읽은 값·위치·판정은 이 검수의 실행 기록에서 읽어 연결했습니다.', { exact: true }).waitFor();
  // 버전별 판정에서 이전 검수본을 고르면 그 포스터와 읽은 위치를 보여 준다
  const trail = poster.getByRole('group', { name: '버전별 판정' });
  assert.deepEqual((await trail.getByRole('button').allInnerTexts()).map(text => text.replace(/\s+/g, ' ')), ['원본 불충족', '1차 수정본 불충족 지금']);
  await trail.getByRole('button', { name: /원본/ }).click();
  await poster.getByText('이전 검수본', { exact: true }).waitFor();
  assert.equal(await poster.getByRole('img').getAttribute('src'), '/api/framework/v1/images/asset-0/content');
  assert.match(await poster.locator('.rv-evd-poster-caption').innerText(), /원본에서 읽은 값 ‘시청 앞 광장’ · 불충족/);
  if (shots) await page.screenshot({ path: shots + '/evidence-dialog-previous.png' });

  // 다음 항목으로 넘기면 같은 창에서 내용이 바뀐다. 위치가 없는 확인 필요 항목은 점선과 담당자 확인으로 보여 준다
  await dialog.getByRole('button', { name: '다음 항목', exact: true }).click();
  const next = page.getByRole('dialog', { name: '03 로고 판정 근거' });
  await next.waitFor();
  assert.equal(await next.locator('.rv-evd-head .rv-evd-chip').innerText(), '확인 필요');
  assert.equal(await next.getByTestId('evidence-read-box').count(), 0);
  assert.equal(await next.getByRole('img').getAttribute('src'), '/api/framework/v1/images/asset-1/content');
  await next.getByText('이 항목은 위치 상자가 없어 포스터 전체를 보여 줍니다.', { exact: true }).waitFor();
  const nextGraph = next.getByRole('group', { name: '로고 근거 관계도' });
  assert.deepEqual(await nextGraph.locator('.rv-evg-node.is-dashed .rv-evg-title').allInnerTexts(), ['위치 정보 없음', '읽을 수 없음', '원본 · 확인 필요', '결정 대기']);
  assert.ok(await next.getByRole('button', { name: '다음 항목', exact: true }).isDisabled());
  if (shots) await page.screenshot({ path: shots + '/evidence-dialog-review.png' });
  // 노트북 화면 크기에서도 창이 화면 안에 들어온다
  await page.setViewportSize({ width: 1366, height: 720 });
  assert.ok(await next.evaluate(element => { const rect = element.getBoundingClientRect();
    return rect.top >= 0 && rect.left >= 0 && rect.bottom <= innerHeight && rect.right <= innerWidth; }));
  if (shots) await page.screenshot({ path: shots + '/evidence-dialog-small.png' });
  // Esc로 닫으면 목록에서 보고 있던 항목으로 초점이 돌아간다
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.querySelector('dialog'));   // 닫힘 이벤트까지 처리된 뒤
  assert.match(await page.evaluate(() => document.activeElement.className + ' ' + document.activeElement.textContent), /rv-result-item-button is-selected[\s\S]*주최 기관 로고를 넣는다/);
  assert.deepEqual(errors, []);
});
