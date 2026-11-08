import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import test from 'node:test';
import { chromium } from 'playwright';
import { createServer } from 'vite';

// API replies are scripted; this checks the real input/approval/report UI, not model quality.
test('직접 입력·PDF·편집 입력을 승인과 보고로 연결하고 미설정은 접수 전에 막는다', { timeout: 60000 }, async t => {
  const vite = await createServer({ server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  t.after(() => vite.close()); await vite.listen();
  const base = `http://127.0.0.1:${vite.httpServer.address().port}`;
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' });
  t.after(() => browser.close());
  for (const mode of ['text', 'pdf', 'edited_pdf', 'not_ready']) {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } }); page.setDefaultTimeout(8000);
    const errors = [], legacy = [], textInputs = [];
    let latest = null, approved = false, reviewed = false, extractedText = false, uploads = 0;
    let allowDocumentResult = mode !== 'pdf', allowReviewResult = false;
    let configured = mode !== 'not_ready';
    const context = { run_id: 'draft', project_id: 'project', actor_id: 'user' };
    const criterion = { criterion_id: 'title', text: '행사명은 봄', kind: 'text', operator: 'contains',
      expected: '봄', required: true, source: { page: 1, quote: '행사명은 봄' } };
    const baseline = { criteria_version_id: 'version', criteria_sha256: 'b'.repeat(64) };
    let png;
    page.on('pageerror', error => errors.push(error.message));
    await page.route(base + '/api/**', async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      let body;
      if (path === '/api/reviews') { legacy.push(request.method()); return route.fulfill({ status: 500 }); }
      if (path === '/api/v1/auth/me') body = { authenticated: true, user: { id: 'user', display_name: '회귀 검사' } };
      else if (path === '/api/health') body = { status: 'ok', ready: true };
      else if (path.endsWith('/health')) body = { requirements_presentation: true, pdf_extraction_mode: 'qwen',
        poster_review_ready: configured, free_text_registration: true };
      else if (path.endsWith('/csrf')) body = { csrf_token: 'token' };
      else if (path.endsWith('/projects')) body = { items: [{ id: 'project', name: '검수', my_role: 'owner' }] };
      else if (path.endsWith('/documents')) body = { document: { document_version_id: 'pdf-document',
        sha256: createHash('sha256').update(request.postDataBuffer()).digest('hex') } };
      else if (path.endsWith('/text-documents')) {
        const input = request.postDataJSON(); textInputs.push(input); extractedText = true;
        body = { document: { document_version_id: 'text-document', sha256: createHash('sha256').update(input.text).digest('hex') } };
      } else if (path.endsWith('/extractions')) body = { context };
      else if (path.endsWith('/requirements')) body = { conditions: [{ text: '행사명은 봄', type: 'information', page: 1, source: '행사명은 봄' }],
        pending: [], page_count: 1, framework: { run_id: 'draft', document_version_id: extractedText ? 'text-document' : 'pdf-document' } };
      else if (path.endsWith('/criteria-review')) body = { latest_version: latest, source_draft_sha256: 'a'.repeat(64) };
      else if (path.endsWith('/criteria-versions')) {
        assert.equal(request.postDataJSON().purpose, 'human_review');
        latest = { version: { ...baseline, version_no: 1, criteria: [criterion], exclusions: [] }, sha256: baseline.criteria_sha256, approval_blockers: {} };
        body = latest;
      } else if (path.endsWith('/criteria-approvals')) {
        assert.equal(request.postDataJSON().reviewed_all_criteria, true); approved = true; body = {};
      } else if (path.endsWith('/images')) {
        assert.ok(approved, 'image registration follows criteria approval'); uploads++; body = { id: 'image' };
      } else if (path.endsWith('/poster-reviews')) {
        assert.equal(request.postDataJSON().asset_version_id, 'image');
        assert.equal(request.postDataJSON().criteria_version_id, baseline.criteria_version_id);
        assert.equal(request.postDataJSON().expected_criteria_sha256, baseline.criteria_sha256);
        body = { context: { ...context, run_id: 'poster', asset_version_id: 'image' } };
      } else if (path.endsWith('/approvals')) {
        assert.equal(request.postDataJSON().decision, 'approve_criteria'); reviewed = true;
        body = { execution_status: 'reviewing', context: { ...context, run_id: 'poster', asset_version_id: 'image' }, baseline };
      } else if (path.endsWith('/images/image/content')) return route.fulfill({ contentType: 'image/png', body: png });
      else if (path.endsWith('/runs/draft')) body = { context, execution_status: !allowDocumentResult ? 'extracting' : approved ? 'waiting_asset' : 'waiting_criteria', criteria: { criteria: [criterion] } };
      else if (path.endsWith('/runs/poster')) body = { context: { ...context, run_id: 'poster', asset_version_id: 'image' }, baseline,
        execution_status: reviewed ? allowReviewResult ? 'waiting_review' : 'reviewing' : 'waiting_criteria', criteria: { criteria: [criterion] },
        ...(reviewed ? { machine_verdict: 'fail', validated: { items: [{ ...criterion, criterion_text: criterion.text,
          observed: '여름', verdict: 'fail', issues: ['value_mismatch'], image: { asset_version_id: 'image', bbox: [.1, .1, .8, .3] } }] },
          step_records: [{ role: 'A04_A05', status: 'completed' }, { role: 'A06', status: 'completed' },
            { role: 'A07', status: allowReviewResult ? 'completed' : 'running' }],
          ...(allowReviewResult ? { report: { summary: 'A07 보고: 행사명이 기준과 다릅니다.', change_requests: ['행사명을 봄으로 수정해 주세요.'] } } : {}) } : {}) };
      else throw new Error('unexpected test route: ' + path);
      await route.fulfill({ json: body });
    });
    await page.goto(base + '/review/new');
    await page.getByRole('heading', { name: '어떤 광고물을 검수할까요?' }).waitFor();
    await page.waitForFunction(() => document.querySelector('#document-project')?.value === 'project');
    await page.getByRole('button', { name: mode === 'not_ready' ? '검수 구성 확인 필요' : '검수 구성 준비됨', exact: true }).waitFor();
    png = Buffer.from(await page.evaluate(() => {
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 10;
      return canvas.toDataURL().split(',')[1];
    }), 'base64');
    await page.getByLabel('검수 이미지 선택', { exact: true }).setInputFiles({ name: 'poster.png', mimeType: 'image/png', buffer: png });
    if (mode === 'pdf' || mode === 'edited_pdf') {
      await page.getByLabel('요구사항 PDF 선택', { exact: true }).setInputFiles({ name: 'brief.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-test') });
      if (mode === 'pdf') {
        await page.getByRole('heading', { name: '검수 요구사항을 정리하고 있어요.' }).waitFor();
        await page.getByRole('progressbar', { name: '검수 처리 진행 상태' }).waitFor();
        await page.waitForFunction(() => {
          const bar = document.querySelector('.rv-progress-track');
          return bar && Number(bar.getAttribute('aria-valuenow')) === 60
            && Math.abs(bar.firstElementChild.getBoundingClientRect().width - bar.clientWidth * .6) < 2;
        });
        if (process.env.E2E_PROGRESS_SCREENSHOT_DIR) {
          mkdirSync(process.env.E2E_PROGRESS_SCREENSHOT_DIR, { recursive: true });
          await page.screenshot({ path: process.env.E2E_PROGRESS_SCREENSHOT_DIR + '/pdf-progress.png', fullPage: true });
        }
        allowDocumentResult = true;
      }
      await page.waitForFunction(() => document.querySelector('#requirements').value === '행사명은 봄');
      await page.getByRole('button', { name: '추출된 검수 기준 확인', exact: true }).waitFor();
    }
    if (mode !== 'pdf') await page.locator('#requirements').fill(mode === 'edited_pdf' ? '행사명은 여름' : '행사명은 봄');
    if (mode === 'pdf') {
      await page.locator('#document-project').selectOption('');
      await page.getByRole('button', { name: 'PDF 검수 기준 확인하기', exact: true }).click();
      await page.getByText(/이 원문을 저장한 프로젝트를 선택/).waitFor();
      assert.equal(textInputs.length, 0, 'a project change must not silently convert PDF criteria to a new text source');
      await page.locator('#document-project').selectOption('project');
      configured = false; // Draft review must work even if poster models are not ready yet.
    }
    if (mode === 'edited_pdf') await page.getByRole('note').filter({ hasText: '별도 원문' }).waitFor();
    await page.getByRole('button', { name: mode === 'pdf' ? 'PDF 검수 기준 확인하기' : mode === 'edited_pdf' ? '입력문으로 새 기준 분석' : '검수 항목 확인하기', exact: true }).click();
    if (mode === 'not_ready') {
      await page.getByText(/포스터 검사·보고 구성이 준비되지/).waitFor();
      assert.equal(textInputs.length, 0);
    } else {
      await page.getByRole('heading', { name: '검수할 항목을 확정해 주세요.' }).waitFor();
      configured = true;
      assert.ok(await page.locator('.rv-criteria-canvas-column').isVisible());
      assert.ok(await page.getByRole('complementary', { name: '검수 기준 검토' }).isVisible());
      assert.deepEqual(textInputs, mode === 'pdf' ? [] : [{ text: mode === 'edited_pdf' ? '행사명은 여름' : '행사명은 봄' }]);
      assert.equal(reviewed, false);
      await page.getByRole('button', { name: '검토한 기준 버전 저장', exact: true }).click();
      await page.getByRole('button', { name: '이 버전의 전체 기준 승인', exact: true }).click();
      const nextStep = page.getByRole('region', { name: '검수 다음 단계' });
      const registerPoster = nextStep.getByRole('button', { name: '승인 기준에 포스터 등록', exact: true });
      const startReview = nextStep.getByRole('button', { name: '이 포스터와 기준을 확인하고 검수 시작', exact: true });
      if (mode === 'text') {
        configured = false;
        await registerPoster.click();
        await page.getByText(/포스터 검사·보고 구성이 준비되지/).waitFor();
        assert.equal(uploads, 0);
        configured = true;
      }
      await registerPoster.click();
      if (mode === 'text') {
        configured = false;
        await startReview.click();
        await page.getByText(/포스터 검사·보고 구성이 준비되지/).waitFor();
        assert.equal(reviewed, false);
        configured = true;
      }
      await startReview.click();
      await page.getByRole('heading', { name: '확정한 기준으로 포스터를 검수하고 있어요.' }).waitFor();
      const progress = page.getByRole('progressbar', { name: '검수 처리 진행 상태' });
      await progress.waitFor();
      await page.waitForFunction(() => document.querySelector('.rv-progress-track')?.getAttribute('aria-valuenow') === '67');
      await page.waitForFunction(() => {
        const bar = document.querySelector('.rv-progress-track');
        return bar && Math.abs(bar.firstElementChild.getBoundingClientRect().width - bar.clientWidth * .67) < 2;
      });
      assert.equal(await progress.locator('.rv-progress-shine').count(), 1);
      assert.equal(await progress.locator('.rv-progress-fill').evaluate(element => getComputedStyle(element).backgroundColor), 'rgb(23, 23, 23)');
      assert.equal(await page.getByRole('heading', { name: '선택 항목 상세', exact: true }).count(), 0, 'partial validation must not hide the running report stage');
      if (mode === 'pdf' && process.env.E2E_PROGRESS_SCREENSHOT_DIR) {
        await page.screenshot({ path: process.env.E2E_PROGRESS_SCREENSHOT_DIR + '/poster-progress.png', fullPage: true });
      }
      allowReviewResult = true;
      await page.getByRole('heading', { name: '선택 항목 상세', exact: true }).waitFor();
      const columns = await page.locator('.rv-result-workbench').evaluate(element =>
        Array.from(element.children, child => { const rect = child.getBoundingClientRect(); return { left: rect.left, right: rect.right, top: rect.top, height: rect.height }; }));
      assert.equal(columns.length, 3);
      assert.ok(columns.every(column => column.height > 200 && Math.abs(column.top - columns[0].top) < 2));
      assert.ok(columns[0].right <= columns[1].left && columns[1].right <= columns[2].left, 'list, poster and evidence must form three columns');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.getByRole('button', { name: '보고서·담당자 승인', exact: true }).click();
      await page.getByText('보고서 원문 보기', { exact: true }).click();
      await page.getByText('A07 보고: 행사명이 기준과 다릅니다.', { exact: true }).waitFor();
      await page.getByText('행사명을 봄으로 수정해 주세요.', { exact: true }).waitFor();
      await page.getByRole('heading', { name: '조건별 결과·근거', exact: true }).waitFor();
      assert.equal(await page.getByText('여름', { exact: true }).count(), 1);
      await page.getByRole('button', { name: '인쇄 / PDF 저장', exact: true }).waitFor();
      await page.getByText('기계 판정 · 불충족', { exact: true }).waitFor();
      await page.getByRole('button', { name: '검수 결과로 돌아가기', exact: true }).click();
      await page.getByRole('button', { name: '기준 다시 확인하기', exact: true }).click();
      await page.getByRole('heading', { name: '검수할 항목을 확정해 주세요.' }).waitFor();
      assert.equal(await page.getByRole('button', { name: '기준 보완', exact: true }).count(), 0, 'approved criteria stay read-only');
      await page.reload();
      await page.getByRole('button', { name: '보고서·담당자 승인', exact: true }).click();
      await page.getByText('보고서 원문 보기', { exact: true }).click();
      await page.getByText('A07 보고: 행사명이 기준과 다릅니다.', { exact: true }).waitFor();
    }
    assert.deepEqual(legacy, [], mode); assert.deepEqual(errors, [], mode);
    await page.close();
  }
});
