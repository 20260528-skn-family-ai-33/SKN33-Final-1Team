// Started by the disposable PostgreSQL test; every browser request goes to the real API.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { chromium } from 'playwright';
import { preview } from 'vite';

const port = Number(process.argv[2]);
assert.ok(Number.isInteger(port) && port > 0 && port < 65536);
const base = 'http://127.0.0.1:5189';
process.env.FRAMEWORK_API_PROXY_TARGET = process.env.API_PROXY_TARGET = 'http://127.0.0.1:' + port;
const vite = await preview({ preview: { host: '127.0.0.1', port: 5189, strictPort: true }, logLevel: 'error' });
let browser, page;
try {
  // WSL localhost forwarding can become ready after the Linux listener.
  let ready = false;
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      const health = await fetch(process.env.API_PROXY_TARGET + '/api/framework/v1/health');
      ready = health.ok && (await health.json()).poster_reviews === true;
    } catch {}
    if (ready) break;
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  assert.ok(ready, 'The poster API must be reachable from the browser host');
  browser = await chromium.launch({ channel: process.argv[3], headless: true });
  page = await browser.newPage();
  page.setDefaultTimeout(20000);
  const errors = [], legacy = [], results = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', req => { if (new URL(req.url()).pathname === '/api/reviews') legacy.push(req.method()); });
  page.on('response', res => {
    if (res.url().includes('/api/framework/') && res.status() >= 400) console.error(res.status(), new URL(res.url()).pathname);
  });
  const button = name => page.getByRole('button', { name, exact: true });
  const csrf = async path => (await (await page.request.get(base + path)).json()).csrf_token;
  async function post(path, csrfPath, data) {
    const response = await page.request.post(base + path, { headers: { Origin: base, 'X-CSRFToken': await csrf(csrfPath) }, data });
    assert.ok(response.ok(), path + ' returned ' + response.status());
  }
  const email = randomUUID() + '@example.com', password = randomUUID() + '1!';
  await post('/api/v1/auth/signup', '/api/v1/auth/csrf', { email, password, display_name: '통합 시험' });
  await post('/api/v1/auth/login', '/api/v1/auth/csrf', { email, password });
  await post('/api/framework/v1/projects', '/api/framework/v1/csrf', { name: '포스터 브라우저 검증' });
  await page.goto(base + '/review/new');
  await page.waitForFunction(() => {
    const input = document.querySelector('input[aria-label="요구사항 PDF 선택"]');
    return input && !input.disabled;
  });
  await page.getByLabel('요구사항 PDF 선택', { exact: true }).setInputFiles({
    name: 'poster-brief.pdf', mimeType: 'application/pdf', buffer: Buffer.from(process.argv[4], 'base64') });
  await button('PDF 검수 기준 확인하기').click();
  await button('검토한 기준 버전 저장').click();
  await page.getByText(/승인 전 보완/).waitFor();
  assert.equal(await button('이 버전의 전체 기준 승인').count(), 0);
  await page.getByRole('listitem').filter({ hasText: '기대값 미정' }).getByRole('checkbox').uncheck();
  await page.getByLabel('제외 사유').fill('이번 합성 시험은 전시명만 확인합니다.');
  await button('검토한 기준 버전 저장').click();
  await button('이 버전의 전체 기준 승인').click();

  async function image(label, color) {
    const raw = await page.evaluate(color => {
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 32;
      const ctx = canvas.getContext('2d'); ctx.fillStyle = color; ctx.fillRect(0, 0, 32, 32);
      ctx.fillStyle = 'black'; ctx.font = '7px sans-serif'; ctx.fillText(color === 'lime' ? 'SPRING' : 'WINTER', 1, 16);
      return canvas.toDataURL().split(',')[1];
    }, color);
    await page.getByLabel(label, { exact: true }).setInputFiles({ name: color + '.png', mimeType: 'image/png', buffer: Buffer.from(raw, 'base64') });
  }
  async function review(expected) {
    await page.waitForFunction(() => document.querySelector('img[alt="현재 버전의 검수 포스터"]')?.naturalWidth === 32);
    await button('이 포스터와 기준을 확인하고 검수 시작').click();
    await button('보고서·담당자 승인').click();
    await page.getByText('기계 판정 · ' + expected, { exact: true }).waitFor();
    const id = new URL(page.url()).searchParams.get('framework');
    const run = await (await page.request.get(base + '/api/framework/v1/runs/' + id)).json();
    assert.deepEqual(run.step_records.map(s => [s.role, s.status]), [
      ['A02', 'reused'], ['A04_A05', 'completed'], ['A06', 'completed'], ['A07', 'completed']]);
    assert.equal(run.report.verdict, run.machine_verdict);
    results.push(run);
  }
  await image('검수 포스터', 'red');
  await button('승인 기준에 포스터 등록').click();
  await review('불충족');
  await page.getByLabel('수정 요청 사유').fill('전시명을 기준에 맞춰 수정해 주세요.');
  await button('수정 요청 승인').click();
  await page.getByLabel('수정 포스터', { exact: true }).waitFor();
  await page.reload();
  await button('보고서·담당자 승인').click();
  await image('수정 포스터', 'lime');
  await button('수정본 재검수 접수').click();
  await review('충족');
  assert.equal(results[1].context.parent_run_id, results[0].context.run_id);
  assert.notEqual(results[1].context.asset_version_id, results[0].context.asset_version_id);
  assert.deepEqual(results[1].baseline, results[0].baseline);
  await button('최종 승인').click();
  const approved = page.locator('.rv-result-summary').getByText('담당자 승인 완료', { exact: true });
  await approved.waitFor();
  await page.reload();
  await approved.waitFor();
  await button('보고서 보기').click();
  await page.getByRole('heading', { name: '검수 보고서', level: 1 }).waitFor();
  assert.equal(await button('최종 승인').count(), 0);
  assert.deepEqual(errors, []); assert.deepEqual(legacy, []);
  console.log('PDF approved, first poster failed, revision passed and final approval survived reload.');
} catch (error) {
  if (page && !page.isClosed()) console.error((await page.locator('body').innerText()).slice(-1800));
  throw error;
} finally {
  await browser?.close();
  await vite.close();
}
