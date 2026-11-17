import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const { chromium } = createRequire(import.meta.url)('playwright');
const root = fileURLToPath(new URL('../../', import.meta.url));
const base = process.env.E2E_REQUIREMENTS_URL || 'http://127.0.0.1:5177';

// 2026-11-17 베타 피드백: 보고서 아래 '담당자 승인과 수정본 재검수'(직접 확인 양식·최종 승인·수정 요청·재검수 접수)를 없애고,
// 확인 필요 항목마다 담당자 코멘트 한 칸만 둔다. 코멘트는 이 브라우저에 실행별로 저장하고 서버로 보내지 않는다.
test('보고서는 승인 구역 없이 확인 필요 항목에만 담당자 코멘트를 받는다', { timeout: 60000 }, async t => {
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
  const posted = [], context = { run_id: 'human-fixture', project_id: 'project-fixture', actor_id: 'user-fixture', asset_version_id: 'poster-v1' };
  const snapshot = { context, execution_status: 'waiting_review', human_decision: 'unreviewed', machine_verdict: 'review',
    baseline: { criteria_version_id: 'criteria-v1' }, evidence: { status: 'complete' }, approvals: [],
    validated: { items: [
      { criterion_id: 'date', criterion_text: '행사 날짜', required: true, expected: '11월 12일', observed: '11월 12일',
        observation_status: 'observed', verdict: 'review', issues: ['date_comparison_review_required'] },
      { criterion_id: 'title', criterion_text: '행사 이름', required: true, expected: '봄 축제', observed: '봄 축제',
        observation_status: 'observed', verdict: 'pass', issues: [] }] },
    report: { summary: '날짜 조건을 직접 확인해 주세요.', change_requests: [] } };
  await page.route(base + '/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (request.method() === 'POST') posted.push(path);
    let body;
    if (path === '/api/v1/auth/me') body = { authenticated: true, user: { id: context.actor_id, display_name: '개발 검증' } };
    else if (path === '/api/health') body = { status: 'ok', ready: true };
    else if (path === '/api/framework/v1/csrf') body = { csrf_token: 'human-browser-token' };
    else if (path === '/api/framework/v1/runs/' + context.run_id) body = snapshot;
    else if (path.endsWith('/content')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><text y="40">11월 12일</text></svg>' });
    else return route.fulfill({ status: 404, json: { detail: 'unexpected test route' } });
    return route.fulfill({ json: body });
  });
  await page.goto(base + '/review/new?framework=' + context.run_id);
  const button = name => page.getByRole('button', { name, exact: true });
  await button('보고서 보기').waitFor();
  // 결과 화면에도 수정본 재검수·담당자 승인 단추를 두지 않는다
  assert.equal(await button('수정본 재검수').count(), 0);
  assert.equal(await button('보고서·담당자 승인').count(), 0);
  await button('보고서 보기').click();
  const report = page.getByRole('article', { name: '검수 보고서' });
  await report.getByText('기계 판정 · 확인 필요', { exact: true }).waitFor();
  for (const name of ['담당자 승인과 수정본 재검수', '담당자 직접 확인', '검수 진행 기록'])
    assert.equal(await page.getByRole('heading', { name }).count(), 0, name);
  for (const name of ['최종 승인', '사람 확인 후 최종 승인', '수정 요청 승인', '수정본 재검수 접수', '예외 승인 기록'])
    assert.equal(await button(name).count(), 0, name);
  assert.equal(await page.getByLabel('수정 요청 사유', { exact: true }).count(), 0);
  assert.equal(await page.getByText('실행·기준 이력', { exact: true }).count(), 0);

  // 코멘트 칸은 확인 필요 항목에만 하나씩, 위치 정보·상태 칩 아래(오른쪽)에 있다
  const comments = report.getByRole('textbox', { name: /담당자 코멘트$/ });
  assert.equal(await comments.count(), 1);
  const reviewItem = report.locator('.rv-report-result').filter({ has: page.getByRole('textbox', { name: /담당자 코멘트$/ }) });
  await reviewItem.locator('.rv-report-chip').getByText('확인 필요', { exact: true }).waitFor();
  const [side, box] = await Promise.all([reviewItem.locator('.rv-report-result-side').boundingBox(), comments.boundingBox()]);
  assert.ok(box.y >= side.y + side.height - 1, '코멘트 칸은 상태 칩 아래에 있다');
  assert.ok(box.x + box.width >= side.x + side.width - 2 && box.x > side.x - 200, '코멘트 칸은 오른쪽 칸에 있다');
  const note = '포스터 하단 날짜를 원문과 대조했고 같습니다.';
  await comments.fill(note);
  assert.deepEqual(JSON.parse(await page.evaluate(() => localStorage.getItem('poster-report-comments:human-fixture'))), { date: note });

  // 새로고침해도 이 브라우저에는 남는다. 인쇄에서는 입력 칸 대신 글로 나온다
  await page.reload();
  await button('보고서 보기').click();
  await report.getByRole('textbox', { name: /담당자 코멘트$/ }).waitFor();
  assert.equal(await report.getByRole('textbox', { name: /담당자 코멘트$/ }).inputValue(), note);
  await page.emulateMedia({ media: 'print' });
  assert.equal(await report.locator('.rv-report-comment').isVisible(), false);
  assert.equal((await report.locator('.rv-report-comment-print').innerText()).replace(/\s+/g, ' '), '담당자 코멘트 ' + note);
  // 인쇄에는 보고서만 남는다. 보고서 밖 내용이 길어도 그 자리만큼 빈 쪽이 생기지 않는다(2026-11-17 피드백: 1쪽 보고서가 3쪽으로 인쇄됨)
  await page.evaluate(() => { const block = document.createElement('section'); block.id = 'print-filler'; block.style.height = '3000px';
    block.textContent = '보고서 밖 내용'; document.querySelector('.rv-report-print').parentElement.append(block); });
  const pdf = (await page.pdf({ format: 'A4' })).toString('latin1');
  assert.equal((pdf.match(/\/Type\s*\/Page[^s]/g) || []).length, 1, 'one-page report prints on one page');
  await page.evaluate(() => document.getElementById('print-filler').remove());
  await page.emulateMedia({ media: 'screen' });
  assert.equal(await report.locator('.rv-report-comment-print').isVisible(), false);

  // 비우면 저장 항목도 지운다. 코멘트는 서버로 보내지 않는다
  await report.getByRole('textbox', { name: /담당자 코멘트$/ }).fill('');
  assert.equal(await page.evaluate(() => localStorage.getItem('poster-report-comments:human-fixture')), null);
  assert.deepEqual(posted.filter(path => !path.endsWith('/csrf')), []);
  assert.deepEqual(errors, []);
});
