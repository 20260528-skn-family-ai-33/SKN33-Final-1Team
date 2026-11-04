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

test('PDF 분석 문단 전체를 3. 검수 요구사항에 추가하고 재선택으로 중복 접수하지 않는다', { timeout: 60000 }, async t => {
  if (!process.env.E2E_REQUIREMENTS_URL) {
    const child = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5177', '--strictPort'], {
      cwd: root, stdio: 'pipe', env: { ...process.env, VITE_API_PROFILE: 'legacy' },
    });
    let output = ''; child.stdout.on('data', data => { output += data; }); child.stderr.on('data', data => { output += data; });
    t.after(() => { if (child.exitCode === null) child.kill(); });
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
  const items = recorded?.conditions || fixtureItems;
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
    } else if (path === '/api/framework/v1/runs/run-fixture') body = { execution_status: 'waiting_criteria' };
    else if (path.endsWith('/run-fixture/requirements')) body = { ...recorded, conditions: items, page_count: 2, warnings: recorded?.warnings || [],
      framework: { run_id: 'run-fixture', document_version_id: 'document-fixture' } };
    else { oldRequests++; return route.fulfill({ status: 404, json: { detail: 'unexpected test route' } }); }
    return route.fulfill({ json: body });
  });
  await page.goto(base + '/review/new');
  try { await page.getByRole('heading', { name: '어떤 광고물을 검수할까요?' }).waitFor(); }
  catch (error) { throw new Error(`${error.message}\n${page.url()}\n${(await page.locator('body').innerText()).slice(0, 1800)}\n${errors.join('\n')}`); }
  await page.locator('#requirements').fill('직접 입력: 보존');
  await page.waitForFunction(() => !document.querySelector('input[aria-label="요구사항 PDF 선택"]').disabled);
  const upload = page.getByLabel('요구사항 PDF 선택', { exact: true });
  await upload.setInputFiles({ name: 'brief.pdf', mimeType: 'application/pdf', buffer: data });
  const expectedTexts = [...new Set(items.map(item => item.text))];
  const expected = ['직접 입력: 보존', ...expectedTexts].join('\n\n');
  await page.waitForFunction(value => document.querySelector('#requirements').value === value, expected);
  assert.equal(await page.locator('#requirements').inputValue(), expected);
  await page.getByText(new RegExp(`${expectedTexts.length}개 요구사항 문단을 추가했습니다`)).waitFor();
  if (process.env.E2E_REQUIREMENTS_SCREENSHOT) await page.screenshot({ path: process.env.E2E_REQUIREMENTS_SCREENSHOT, fullPage: true });
  await upload.setInputFiles({ name: 'brief.pdf', mimeType: 'application/pdf', buffer: data });
  await page.getByText(/0개 요구사항 문단을 추가했습니다/).waitFor();
  assert.equal(await page.locator('#requirements').inputValue(), expected);
  assert.equal(uploads, 1); assert.equal(admissions, 1); assert.equal(oldRequests, 0);
  assert.deepEqual(errors, []);
});
