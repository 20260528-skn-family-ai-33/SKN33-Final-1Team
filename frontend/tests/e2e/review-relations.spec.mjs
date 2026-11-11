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
      criteria: { context, criteria, extraction: { rejected_candidates: [] } } },
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
  // 안내는 기준 검토를 막지 않는다
  assert.ok(await page.getByRole('button', { name: '기준 확인하고 승인', exact: true }).isEnabled());
  assert.equal(requested.filter(path => path.endsWith('/revision-history')).length, 0);
  if (shots) await page.screenshot({ path: shots + '/change-impact.png' });
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
