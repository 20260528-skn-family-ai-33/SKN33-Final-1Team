import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const root = fileURLToPath(new URL('../../', import.meta.url));
const base = process.env.E2E_REQUIREMENTS_URL || 'http://127.0.0.1:5177';

for (const mode of ['resolved', 'pending_empty', 'pending_existing', 'pending_visual']) {
test(`PDF 문단·미확정 항목을 보존하고 같은 실행으로 연결한다 (${mode})`, { timeout: 60000 }, async t => {
  const pendingOnly = mode !== 'resolved';
  const visual = mode === 'pending_visual';
  const value = visual ? '가공원 로고' : '11월 12일';
  const reply = visual ? '가공원 로고를 넣어 주세요.' : '행사일은 11월 12일로 확정합니다.';
  const reason = visual ? '미확정 로고 보완' : '미확정 행사일 보완';
  const scope = visual ? { target: 'poster_visual', field: 'logo' } : { target: 'poster_text', field: 'event_date' };
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
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(10000);
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  let uploads = 0, admissions = 0, oldRequests = 0;
  const data = Buffer.from('%PDF-test');
  const fixtureItems = [
    ['행사명', '봄의 기록', 'information'], ['기간', '2026.05.01 ~ 05.04', 'information'],
    ['장소', '가공원', 'information'], ['장소', '나공원', 'information'], ['장소', '다공원', 'information'],
    ['잘림', '문구와 이미지가 잘리지 않아야 합니다.', 'visual'], ['필수 고지', '무료 관람', 'information'],
    ['파일 형식', 'JPEG', 'information'],
  ].map(([label, requirement, type], index) => ({ label, requirement, type, inspection: '', level: '필수',
    text: type === 'visual' ? requirement : `${label}: ${requirement}`, page: index < 5 ? 1 : 2, source: requirement }));
  const recorded = process.env.E2E_REQUIREMENTS_RESULT_FILE
    ? JSON.parse(readFileSync(process.env.E2E_REQUIREMENTS_RESULT_FILE, 'utf8')).presentation : null;
  const items = pendingOnly ? [] : recorded?.conditions || fixtureItems;
  const pending = pendingOnly ? [{ criterion_id: 'pending-1', page: 1, source: visual ? '로고는 확인 중입니다.' : '행사 날짜는 확인 중입니다.' }] : [];
  const criteria = pending.map(item => ({ criterion_id: item.criterion_id, text: item.source, expected: '',
    source: { page: item.page, quote: item.source }, kind: 'unsupported', operator: 'manual', unit: null, required: true,
    details: visual ? scope : { target: 'unspecified', field: 'unspecified' } }));
  let latest = null, approved = false;
  const saves = [];
  await page.route(base + '/api/**', async route => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    let body;
    if (path === '/api/v1/auth/me') body = { authenticated: true, user: { id: 'user-fixture', display_name: '개발 검증' } };
    else if (path === '/api/health') body = { status: 'ok', ready: true };
    else if (path === '/api/framework/v1/health') body = { requirements_presentation: true, pdf_extraction_mode: 'qwen' };
    else if (path === '/api/framework/v1/projects') body = { items: [{ id: 'project-fixture', name: 'PDF 검증', my_role: 'owner' }], next_cursor: null };
    else if (path === '/api/framework/v1/csrf') body = { csrf_token: 'framework-browser-token' };
    else if (path.endsWith('/documents')) {
      uploads++;
      assert.equal(request.headers()['x-csrftoken'], 'framework-browser-token');
      assert.deepEqual(request.postDataBuffer(), data);
      body = { document: { document_version_id: 'document-fixture', sha256: createHash('sha256').update(data).digest('hex') } };
    } else if (path.endsWith('/extractions')) {
      admissions++;
      assert.equal(request.postData(), null);
      body = { context: { run_id: 'run-fixture' }, execution_status: 'queued' };
    } else if (path === '/api/framework/v1/runs/run-fixture') body = { execution_status: approved ? 'waiting_asset' : 'waiting_criteria',
      context: { run_id: 'run-fixture' }, criteria: { criteria } };
    else if (path.endsWith('/run-fixture/criteria-review')) body = { latest_version: latest, source_draft_sha256: 'a'.repeat(64) };
    else if (path.endsWith('/run-fixture/criteria-versions')) {
      saves.push({ body: request.postDataJSON(), key: request.headers()['idempotency-key'] });
      if (mode === 'pending_existing' && saves.length === 1) return route.fulfill({ status: 503, json: { detail: '저장 응답 확인 필요' } });
      const edit = request.postDataJSON().edits[0];
      assert.equal(edit.action, 'amend');
      assert.deepEqual(edit.resolution, { ...scope, kind: visual ? 'visual' : 'text', operator: visual ? 'present' : 'contains',
        expected: value, unit: null, required: true });
      assert.equal(edit.source.quote, reply);
      assert.equal(edit.source.reference, '개발용 담당자 회신');
      assert.equal(edit.reason, reason);
      latest = { version: { criteria_version_id: 'version-1', version_no: 1, exclusions: [], criteria: [{ ...criteria[0],
        ...edit.resolution, details: { target: edit.resolution.target, field: edit.resolution.field }, value_source: edit.source }] },
        sha256: 'b'.repeat(64), approval_blockers: {} };
      body = latest;
    } else if (path.endsWith('/run-fixture/criteria-approvals')) {
      const approval = request.postDataJSON();
      assert.equal(approval.criteria_version_id, latest.version.criteria_version_id);
      assert.equal(approval.expected_sha256, latest.sha256);
      assert.equal(approval.reviewed_all_criteria, true);
      approved = true; body = {};
    }
    else if (path.endsWith('/run-fixture/requirements')) body = { ...recorded, conditions: items, pending, page_count: 2, warnings: recorded?.warnings || [],
      framework: { run_id: 'run-fixture', document_version_id: 'document-fixture' } };
    else { oldRequests++; return route.fulfill({ status: 404, json: { detail: 'unexpected test route' } }); }
    return route.fulfill({ json: body });
  });
  await page.goto(base + '/review/new');
  try { await page.getByRole('heading', { name: '어떤 광고물을 검수할까요?' }).waitFor(); }
  catch (error) { throw new Error(`${error.message}\n${page.url()}\n${(await page.locator('body').innerText()).slice(0, 1800)}\n${errors.join('\n')}`); }
  const current = mode === 'pending_empty' ? '' : '직접 입력: 보존';
  await page.locator('#requirements').fill(current);
  await page.waitForFunction(() => !document.querySelector('input[aria-label="요구사항 PDF 선택"]').disabled);
  const upload = page.getByLabel('요구사항 PDF 선택', { exact: true });
  await upload.setInputFiles({ name: 'brief.pdf', mimeType: 'application/pdf', buffer: data });
  const expectedTexts = [...new Set(items.map(item => item.text))];
  const expected = [current, ...expectedTexts].filter(Boolean).join('\n\n');
  await page.waitForFunction(value => document.querySelector('#requirements').value === value, expected);
  assert.equal(await page.locator('#requirements').inputValue(), expected);
  await page.getByText(pendingOnly ? /미확정 항목 1개/ : new RegExp(`${expectedTexts.length}개 요구사항 문단을 추가했습니다`)).waitFor();
  if (process.env.E2E_REQUIREMENTS_SCREENSHOT) await page.screenshot({ path: process.env.E2E_REQUIREMENTS_SCREENSHOT, fullPage: true });
  await upload.setInputFiles({ name: 'brief.pdf', mimeType: 'application/pdf', buffer: data });
  await page.getByText(pendingOnly ? /미확정 항목 1개/ : /0개 요구사항 문단을 추가했습니다/).waitFor();
  assert.equal(await page.locator('#requirements').inputValue(), expected);
  if (pendingOnly) {
    await page.getByRole('button', { name: mode === 'pending_empty' ? 'PDF 검수 기준 확인하기' : 'brief.pdf 원문 기준 승인하기' }).click();
    await page.getByRole('heading', { name: 'PDF 기준 포스터 검수' }).waitFor();
    await page.getByText('1쪽 · ' + pending[0].source, { exact: true }).waitFor();
    assert.equal(new URL(page.url()).searchParams.get('framework'), 'run-fixture');
    assert.equal(await page.getByRole('button', { name: '이 버전의 전체 기준 승인' }).count(), 0);
    await page.getByRole('button', { name: '기준 보완', exact: true }).click();
    const save = page.getByRole('button', { name: '검토한 기준 버전 저장' });
    await save.click();
    assert.equal(saves.length, 0); // Required classification and provenance must be filled before sending.
    if (visual) {
      assert.equal(await page.getByLabel('값 유형', { exact: true }).inputValue(), 'visual');
      assert.equal(await page.getByLabel('비교 방법', { exact: true }).inputValue(), 'present');
    } else {
      await page.getByLabel('검사 대상', { exact: true }).selectOption(scope.target);
      await page.getByLabel('검사 항목', { exact: true }).selectOption(scope.field);
    }
    await page.getByLabel('보완값', { exact: true }).fill(value);
    await page.getByLabel('보완 사유', { exact: true }).fill(reason);
    await page.getByLabel('회신 출처', { exact: true }).fill('개발용 담당자 회신');
    await page.getByLabel('회신 원문', { exact: true }).fill(reply);
    await save.click();
    if (mode === 'pending_existing') {
      await page.getByRole('button', { name: '서버 상태 다시 확인' }).waitFor();
      assert.equal(await page.getByLabel('보완값', { exact: true }).inputValue(), value);
      assert.equal(await page.getByRole('button', { name: '이 버전의 전체 기준 승인' }).count(), 0);
      await save.click();
    }
    const approve = page.getByRole('button', { name: '이 버전의 전체 기준 승인' });
    await approve.waitFor();
    await page.getByText('보완 회신: ' + reply + ' · 출처: 개발용 담당자 회신', { exact: true }).waitFor();
    assert.equal(saves.length, mode === 'pending_existing' ? 2 : 1);
    assert.ok(saves[0].key);
    if (mode === 'pending_existing') assert.deepEqual(saves[0], saves[1]);
    await page.getByRole('button', { name: '기준 보완', exact: true }).click();
    assert.equal(await page.getByLabel('검사 대상', { exact: true }).inputValue(), scope.target);
    assert.equal(await page.getByLabel('검사 항목', { exact: true }).inputValue(), scope.field);
    assert.equal(await approve.count(), 0); // Unsaved edits cannot approve the previous version.
    await page.getByRole('button', { name: '보완 취소' }).click();
    await approve.click();
    await page.getByLabel('검수 포스터', { exact: true }).waitFor();
    assert.equal(approved, true);
  }
  assert.equal(uploads, 1); assert.equal(admissions, 1); assert.equal(oldRequests, 0);
  assert.deepEqual(errors, []);
});
}
