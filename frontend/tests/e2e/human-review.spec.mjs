import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const { chromium } = createRequire(import.meta.url)('playwright');
const root = fileURLToPath(new URL('../../', import.meta.url));
const base = process.env.E2E_REQUIREMENTS_URL || 'http://127.0.0.1:5177';

for (const verdict of ['pass', 'fail']) {
test(`담당자 ${verdict} 확인 근거를 저장하고 기계 판정은 보존한다`, { timeout: 60000 }, async t => {
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
  const sent = [], context = { run_id: 'human-fixture', project_id: 'project-fixture', actor_id: 'user-fixture', asset_version_id: 'poster-v1' };
  const snapshot = { context, execution_status: 'waiting_review', machine_verdict: 'review',
    baseline: { criteria_version_id: 'criteria-v1' }, evidence: { status: 'complete' }, approvals: [],
    validated: { items: [{ criterion_id: 'date', criterion_text: '행사 날짜', required: true, expected: '11월 12일', observed: '11월 12일',
      verdict: 'review', issues: ['date_comparison_review_required'] }] },
    report: { summary: '날짜 조건을 직접 확인해 주세요.', change_requests: [] } };
  await page.route(base + '/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let body;
    if (path === '/api/v1/auth/me') body = { authenticated: true, user: { id: context.actor_id, display_name: '개발 검증' } };
    else if (path === '/api/health') body = { status: 'ok', ready: true };
    else if (path === '/api/framework/v1/csrf') body = { csrf_token: 'human-browser-token' };
    else if (path === '/api/framework/v1/runs/' + context.run_id) body = snapshot;
    else if (path.endsWith('/approvals')) {
      assert.equal(request.headers()['x-csrftoken'], 'human-browser-token');
      const approval = request.postDataJSON(); sent.push(approval);
      snapshot.approvals.push(approval);
      snapshot.execution_status = verdict === 'pass' ? 'completed' : 'waiting_revision';
      body = snapshot;
    } else if (path.endsWith('/content')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><text y="40">11월 12일</text></svg>' });
    else return route.fulfill({ status: 404, json: { detail: 'unexpected test route' } });
    return route.fulfill({ json: body });
  });
  await page.goto(base + '/review/new?framework=' + context.run_id);
  await page.getByRole('button', { name: '보고서·담당자 승인', exact: true }).click();
  await page.getByRole('heading', { name: '담당자 직접 확인' }).waitFor();
  const final = page.getByRole('button', { name: '사람 확인 후 최종 승인' });
  assert.ok(await final.isDisabled());
  await page.getByLabel('담당자 판단', { exact: true }).selectOption(verdict);
  await page.getByLabel('수정 요청 사유', { exact: true }).fill('직접 확인한 날짜의 수정 요청');
  const submit = verdict === 'pass' ? final : page.getByRole('button', { name: '수정 요청 승인', exact: true });
  await submit.click();
  assert.equal(sent.length, 0);
  await page.getByLabel('직접 확인한 내용', { exact: true }).fill('포스터 날짜를 원문과 대조했습니다.');
  await page.getByLabel('확인 근거·위치', { exact: true }).fill('현재 포스터 상단 날짜');
  await page.getByLabel('판단 사유', { exact: true }).fill('합성 데이터로 직접 확인 흐름 검증');
  await submit.click();
  assert.equal(sent.length, 0);
  await page.getByLabel('현재 포스터를 직접 확인했습니다', { exact: true }).check();
  if (verdict === 'fail') assert.ok(await final.isDisabled());
  await submit.click();
  await page.getByRole('heading', { name: `담당자 확인 · date · ${verdict === 'pass' ? '충족' : '불충족'}` }).waitFor();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].decision, verdict === 'pass' ? 'approve_final' : 'approve_changes');
  assert.deepEqual(sent[0].context, context);
  assert.deepEqual(sent[0].human_reviews, [{ criterion_id: 'date', verdict, observed: '포스터 날짜를 원문과 대조했습니다.',
    reference: '현재 포스터 상단 날짜', reason: '합성 데이터로 직접 확인 흐름 검증', checked_asset: true }]);
  await page.reload();
  await page.getByRole('button', { name: '보고서·담당자 승인', exact: true }).click();
  await page.getByText('기계 판정 · 확인 필요', { exact: true }).waitFor();
  await page.getByRole('heading', { name: `담당자 확인 · date · ${verdict === 'pass' ? '충족' : '불충족'}` }).waitFor();
  if (verdict === 'fail') await page.getByLabel('수정 포스터', { exact: true }).waitFor();
  else await page.getByText('담당자 승인 완료', { exact: true }).waitFor();
  assert.deepEqual(errors, []);
});
}
