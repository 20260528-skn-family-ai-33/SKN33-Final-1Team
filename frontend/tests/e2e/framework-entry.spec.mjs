import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import test from 'node:test';
import { chromium } from 'playwright';
import { createServer } from 'vite';

// 자료 입력 전에 보안 여부와 검수 유형을 고른다. 요구사항만으로 접수하는 흐름이라 문구 검수로 연다.
async function chooseReview(page) {
  await page.getByRole('heading', { name: '보안이 필요한 자료인가요?', exact: true }).waitFor();
  await page.getByRole('button', { name: '일반 자료', exact: true }).click();
  await page.getByRole('heading', { name: '검수할 항목을 선택해 주세요', exact: true }).waitFor();
  await page.getByRole('button', { name: '문구 검수', exact: true }).click();
  await page.getByRole('heading', { name: '문구 검수 자료 입력', exact: true }).waitFor();
}

// 저장할 프로젝트 버튼에는 고른 프로젝트 이름이 보인다. 고르기 패널의 목록에서 프로젝트를 바꾼다.
const projectChip = page => page.locator('.rv-project-chip');
async function pickProject(page, name) {
  await projectChip(page).click();
  await page.getByRole('dialog', { name: '저장할 프로젝트 고르기', exact: true })
    .getByRole('option', { name }).click();
  await page.waitForFunction(expected => document.querySelector('.rv-project-chip.is-selected .rv-project-chip-text')?.textContent === expected, name);
}

// API replies are scripted; this checks the real input/approval/report UI, not model quality.
// 자료 입력은 한 화면이다: 문서를 첨부하면 같은 화면에 검수 항목이 나타나고, '검수 시작하기'가 기준 승인과 검수 접수까지 한다.
async function verifyFrameworkEntry(t, modes, { checkRejectedCandidates = false } = {}) {
  const vite = await createServer({ server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  t.after(() => vite.close()); await vite.listen();
  const base = `http://127.0.0.1:${vite.httpServer.address().port}`;
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' });
  t.after(() => browser.close());
  for (const mode of modes) {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } }); page.setDefaultTimeout(8000);
    const errors = [], legacy = [], textInputs = [];
    const subset = mode === 'pdf_subset';
    const noNew = mode === 'pdf_no_addition';
    const failedAddition = mode === 'pdf_addition_failed';
    const adding = mode === 'pdf_addition' || noNew || failedAddition;
    const selectsSubset = subset || adding;
    let extractionRequests = 0;
    let additionRequests = 0;
    // 직접 입력 분석을 '대기'로 붙잡아 두고, 그동안 문서 첨부 카드가 어떻게 보이는지 확인한 뒤 풀어 준다.
    let additionReady = false;
    let latest = null, approved = false, reviewed = false, extractedText = false, uploads = 0;
    const posterApproval = Promise.withResolvers();
    // 바로 시작(direct) 흐름에서 잠금 상태를 확인하는 동안 기준 승인 응답을 붙잡아 둔다.
    // 응답이 먼저 끝나면 시작 버튼이 사라져 확인 단계가 실행 속도에 따라 실패했다.
    const criteriaApproval = Promise.withResolvers();
    const textAdmission = Promise.withResolvers();
    let posterApprovalRequests = 0;
    let allowDocumentResult = mode !== 'pdf', allowReviewResult = false, allowReviewStages = false;
    let configured = mode !== 'not_ready';
    const context = { run_id: 'draft', project_id: 'project', actor_id: 'user' };
    const criterion = { criterion_id: 'title', text: '행사명은 봄', kind: 'text', operator: 'contains',
      expected: '봄', required: true, source: { page: 1, quote: '행사명은 봄' } };
    const pending = { ...criterion, criterion_id: 'pending', text: '관람시간 확인 중', expected: '' };
    const subtitle = { ...criterion, criterion_id: 'subtitle', text: '보조 제목은 전시', expected: '전시' };
    const venue = { ...criterion, criterion_id: 'venue', text: '장소는 전시장', expected: '전시장' };
    const selectedPdfCriteria = [criterion, subtitle];
    const directInput = noNew ? '검수해줘' : failedAddition ? '행사 날짜는 미정입니다.' : '장소를 아카이브 홀로 표기합니다.';
    const added = { ...criterion, criterion_id: 'input-job-c001', text: '장소를 아카이브 홀로 표기', expected: '아카이브 홀',
      human_added: true, source: { kind: 'direct_input', page: 1, quote: directInput },
      value_source: { reference: '담당자 직접 입력', quote: '아카이브 홀' } };
    const rejectedCandidate = { candidate_index: 2, code: 'A02_EXPECTED_UNGROUNDED',
      source: { page: 2, quote: '행사 장소는 전시장 A입니다.' } };
    const failedCandidate = { candidate_index: 1, code: 'A02_UNRESOLVED_VALUE_INVENTED', fields: ['presentation', 'operator'],
      source: { page: 1, block_id: 'input-text', quote: directInput } };
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
      // PDF 흐름은 저장 프로젝트를 바꿔 보므로 프로젝트를 두 개 둔다(둘 이상이면 저절로 고르지 않는다).
      else if (path.endsWith('/projects')) body = { items: [{ id: 'project', name: '검수', my_role: 'owner' },
        ...(mode === 'pdf' ? [{ id: 'other', name: '다른 프로젝트', my_role: 'owner' }] : [])] };
      // 실행 화면은 연결된 포스터 로고 작업도 읽는다. 이 시험에는 로고 작업이 없다.
      else if (path === '/api/poster-logos') body = { items: [] };
      else if (path.endsWith('/documents')) body = { document: { document_version_id: 'pdf-document',
        sha256: createHash('sha256').update(request.postDataBuffer()).digest('hex') } };
      else if (path.endsWith('/text-documents')) {
        const input = request.postDataJSON(); textInputs.push(input); extractedText = true;
        body = { document: { document_version_id: 'text-document', sha256: createHash('sha256').update(input.text).digest('hex') } };
      } else if (path.endsWith('/extractions')) {
        if (mode === 'direct') await textAdmission.promise;
        extractionRequests++; body = { context };
      }
      else if (path.endsWith('/requirements')) body = { conditions: [
        { text: '행사명은 봄', type: 'information', page: 1, source: '행사명은 봄', criterion_ids: selectsSubset ? ['title', 'subtitle'] : ['title'] },
        ...(selectsSubset ? [{ text: '장소는 전시장', type: 'information', page: 1, source: '장소는 전시장', criterion_ids: ['venue'] }] : []),
      ],
        pending: [], page_count: 1, framework: { run_id: 'draft', document_version_id: extractedText ? 'text-document' : 'pdf-document' } };
      else if (path.endsWith('/criteria-review')) body = { latest_version: latest, source_draft_sha256: 'a'.repeat(64) };
      else if (path.endsWith('/runs/draft/criteria-additions') && adding) {
        additionRequests++;
        assert.deepEqual(request.postDataJSON(), { parent_version_id: null, expected_base_sha256: 'a'.repeat(64),
          criterion_ids: ['title', 'subtitle'], text: directInput });
        assert.ok(request.headers()['idempotency-key']);
        return route.fulfill({ status: 202, json: { context: { run_id: 'input-job' }, execution_status: 'queued' } });
      } else if (path.endsWith('/runs/input-job') && adding) {
        if (failedAddition && additionReady) return route.fulfill({ json: { context: { ...context, run_id: 'input-job' }, execution_status: 'failed',
          error: { code: failedCandidate.code, message: 'private provider model response', extraction_rejections: [failedCandidate], response_sha256: 'c'.repeat(64) } } });
        if (additionReady) latest = { version: { ...baseline, version_no: 1, criteria: noNew ? selectedPdfCriteria : [...selectedPdfCriteria, added],
          exclusions: [{ criterion_id: 'venue' }, { criterion_id: 'pending' }],
          addition_run_ids: ['input-job'] }, sha256: baseline.criteria_sha256, approval_blockers: {} };
        body = { execution_status: additionReady ? 'waiting_criteria' : 'queued',
          criteria: { criteria: noNew ? [] : [added], extraction: { outcome: noNew ? 'no_new_requirements' : 'requirements' } } };
      }
      else if (path.endsWith('/criteria-versions')) {
        const input = request.postDataJSON();
        assert.equal(input.purpose, 'human_review');
        if (mode === 'skip' && latest) {
          assert.equal(input.parent_version_id, baseline.criteria_version_id);
          assert.deepEqual(input.edits.map(edit => [edit.action, edit.criterion_id]), [['exclude', 'pending']]);
          assert.match(input.edits[0].reason, /담당자.*제외/);
        }
        if (subset) {
          assert.equal(input.parent_version_id, null);
          assert.deepEqual(input.edits.map(edit => [edit.action, edit.criterion_id]), [['exclude', 'venue'], ['exclude', 'pending']]);
        }
        const skipped = mode === 'skip' && input.edits.some(edit => edit.criterion_id === 'pending');
        latest = { version: { ...baseline, version_no: skipped ? 2 : 1,
          criteria: subset ? selectedPdfCriteria : mode === 'skip' && !skipped ? [criterion, pending] : [criterion],
          exclusions: subset || skipped ? input.edits : [] }, sha256: baseline.criteria_sha256,
          approval_blockers: mode === 'blocker' ? { title: ['missing_expected_value'] }
            : mode === 'skip' && !skipped ? { pending: ['missing_expected_value'] } : {} };
        body = latest;
      } else if (path.endsWith('/criteria-approvals')) {
        assert.equal(request.postDataJSON().reviewed_all_criteria, true);
        if (mode === 'direct' || noNew) await criteriaApproval.promise;
        approved = true; body = {};
      } else if (path.endsWith('/images')) {
        assert.ok(approved, 'image registration follows criteria approval'); uploads++; body = { id: 'image' };
      } else if (path.endsWith('/poster-reviews')) {
        assert.equal(request.postDataJSON().asset_version_id, 'image');
        assert.equal(request.postDataJSON().criteria_version_id, baseline.criteria_version_id);
        assert.equal(request.postDataJSON().expected_criteria_sha256, baseline.criteria_sha256);
        body = { context: { ...context, run_id: 'poster', asset_version_id: 'image' } };
      } else if (path.endsWith('/approvals')) {
        assert.equal(request.postDataJSON().decision, 'approve_criteria');
        posterApprovalRequests++;
        if (mode === 'direct') await posterApproval.promise;
        reviewed = true;
        body = { execution_status: 'reviewing', context: { ...context, run_id: 'poster', asset_version_id: 'image' }, baseline };
      } else if (path.endsWith('/images/image/content')) return route.fulfill({ contentType: 'image/png', body: png });
      else if (path.endsWith('/runs/draft')) body = { context, execution_status: !allowDocumentResult ? 'extracting' : approved ? 'waiting_asset' : 'waiting_criteria',
        criteria: { criteria: selectsSubset ? [...selectedPdfCriteria, venue, pending] : mode === 'skip' ? [criterion, pending] : [criterion],
          ...(checkRejectedCandidates ? { extraction: { rejected_candidates: [rejectedCandidate] } } : {}) } };
      else if (path.endsWith('/runs/poster')) body = { context: { ...context, run_id: 'poster', asset_version_id: 'image' }, baseline,
        execution_status: reviewed ? allowReviewResult ? 'waiting_review' : 'reviewing' : 'waiting_criteria', criteria: { criteria: [criterion] },
        ...(reviewed ? { machine_verdict: 'fail', validated: { items: [{ ...criterion, criterion_text: criterion.text,
          observed: '여름', observation_status: 'observed', verdict: 'fail', issues: ['value_mismatch'], image: { asset_version_id: 'image', bbox: [.1, .1, .8, .3] } }] },
          step_records: [{ role: 'A04_A05', status: allowReviewStages ? 'completed' : 'running' },
            { role: 'A06', status: allowReviewStages ? 'completed' : 'pending' },
            { role: 'A07', status: allowReviewResult ? 'completed' : allowReviewStages ? 'running' : 'pending' }],
          ...(allowReviewResult ? { report: { summary: 'A07 보고: 행사명이 기준과 다릅니다.', change_requests: ['행사명을 봄으로 수정해 주세요.'] } } : {}) } : {}) };
      // 관계 조회(이전 기준 대비 변경 · 수정본 이력)와 문구 수정 가능 여부 조회는 부가 안내라 이 시험에서는 자료 없음으로 답한다.
      else if (/\/(criteria-change-impact|revision-history|poster-text-edits)$/.test(path)) return route.fulfill({ status: 404, json: { detail: 'no relation data' } });
      else throw new Error('unexpected test route: ' + path);
      await route.fulfill({ json: body });
    });
    await page.goto(base + '/review/new');
    await chooseReview(page);
    // 문서를 첨부하는 흐름인가(아니면 직접 입력만으로 시작한다).
    const documentMode = mode === 'pdf' || selectsSubset;
    if (mode === 'pdf') {
      // 프로젝트를 고르기 전에는 필수 표시가 보이고 문서를 첨부할 수 없다.
      await page.locator('.rv-project-chip.is-required').filter({ hasText: '프로젝트 선택' }).getByText('필수', { exact: true }).waitFor();
      assert.ok(await page.getByLabel('요구사항 문서 선택', { exact: true }).isDisabled());
      await pickProject(page, '검수');
    }
    await page.waitForFunction(() => document.querySelector('.rv-project-chip.is-selected .rv-project-chip-text')?.textContent === '검수');
    png = Buffer.from(await page.evaluate(() => {
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 10;
      return canvas.toDataURL().split(',')[1];
    }), 'base64');
    await page.getByLabel('검수 이미지 선택', { exact: true }).setInputFiles({ name: 'poster.png', mimeType: 'image/png', buffer: png });
    const overlay = page.getByRole('dialog', { name: '검수 준비 중', exact: true });
    const start = page.getByRole('button', { name: '검수 시작하기', exact: true });
    const checklist = page.getByRole('list', { name: '검수 항목 목록' });
    const documentCard = page.getByRole('region', { name: '요구사항 문서 첨부', exact: true });
    // 자료 입력은 한 화면이다. 기준만 따로 확인하는 화면('검수할 항목을 확정해 주세요.')으로 넘어가지 않는다.
    const criteriaOnlyPage = page.getByRole('heading', { name: '검수할 항목을 확정해 주세요.' });
    if (documentMode) {
      // 직접 입력란에 쓰던 글과 초점은 문서 추출이 끝나 검수 항목이 나타나도 그대로 남아야 한다.
      await page.locator('#requirements').fill(adding ? directInput : '');
      await page.getByLabel('요구사항 문서 선택', { exact: true }).setInputFiles({ name: 'brief.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-test') });
      if (mode === 'pdf') {
        await documentCard.getByRole('progressbar', { name: '문서 분석 단계' }).waitFor();
        await page.waitForFunction(() => document.querySelector('.rv-document-card progress')?.value === 60);
        assert.ok(await page.locator('#requirements').isVisible());
        assert.equal(await page.locator('.rv-progress-page').count(), 0);
        // 문서를 추출하는 동안에는 검수 항목 상자가 아직 없다(직접 입력란 바로 아래가 문서 첨부다).
        assert.equal(await page.getByRole('region', { name: '검수 항목', exact: true }).count(), 0);
        await page.locator('#requirements').focus();
        await page.locator('#requirements').evaluate(field => { field.dataset.kept = 'yes'; });
        if (process.env.E2E_PROGRESS_SCREENSHOT_DIR) {
          mkdirSync(process.env.E2E_PROGRESS_SCREENSHOT_DIR, { recursive: true });
          await page.screenshot({ path: process.env.E2E_PROGRESS_SCREENSHOT_DIR + '/pdf-progress.png', fullPage: true });
        }
        allowDocumentResult = true;
      }
      // 문서 추출이 끝나면 같은 화면에 검수 항목이 나타난다. 주소에는 초안의 실행 번호가 붙는다.
      await checklist.getByRole('checkbox', { name: '검수에 포함: 행사명은 봄' }).waitFor();
      await page.waitForURL('**/review/new?framework=draft');
      assert.equal(await criteriaOnlyPage.count(), 0);
      assert.equal(await page.locator('#requirements').inputValue(), adding ? directInput : '');
      assert.equal(await documentCard.locator('.rv-upload-name').textContent(), 'brief.pdf');
      assert.equal(await documentCard.locator('.rv-document-summary').textContent(), '추출 완료 · 1페이지');
      assert.equal(extractionRequests, 1);
      // 검수 항목 상자는 직접 입력란과 문서 첨부 사이에 놓인다.
      const order = await page.evaluate(() => ['#requirements', '.rv-criteria-box', '.rv-document-card']
        .map(selector => Math.round(document.querySelector(selector).getBoundingClientRect().top)));
      assert.ok(order[0] < order[1] && order[1] < order[2], 'input, criteria, document from top to bottom: ' + order);
      if (mode === 'pdf') {
        // 같은 입력란이 그대로 남아 있고(다시 그려지지 않았다) 초점도 그 칸에 있다.
        assert.deepEqual(await page.locator('#requirements').evaluate(field => [field.dataset.kept, document.activeElement === field]), ['yes', true]);
        // 초안이 생긴 뒤에는 그 실행의 프로젝트에 묶인다. 저장할 프로젝트를 바꿀 수 없다.
        assert.ok(await projectChip(page).isDisabled());
      }
    }
    if (checkRejectedCandidates) {
      // 기준 확인 화면에는 원문 검증에서 제외된 후보 안내를 두지 않는다. 제외된 후보는 기준 목록에도 넣지 않는다.
      await checklist.waitFor();
      assert.equal(await page.getByRole('region', { name: '원문 검증에서 제외된 후보' }).count(), 0);
      assert.equal(await checklist.getByRole('checkbox').count(), 1);
      assert.equal(await checklist.getByText(rejectedCandidate.source.quote).count(), 0);
      assert.equal(approved, false);
      assert.deepEqual(errors, []);
      await page.close();
      continue;
    }
    if (selectsSubset) {
      // 문서에서 나온 기준은 처음부터 모두 검수에 포함되어 있다. 뺄 항목만 체크를 푼다.
      assert.deepEqual(await checklist.getByRole('checkbox').evaluateAll(rows => rows.map(row => [row.getAttribute('aria-label'), row.checked])),
        [['검수에 포함: 행사명은 봄', true], ['검수에 포함: 보조 제목은 전시', true], ['검수에 포함: 장소는 전시장', true], ['검수에 포함: 관람시간 확인 중', true]]);
      await checklist.getByRole('checkbox', { name: '검수에 포함: 장소는 전시장' }).uncheck();
      await checklist.getByRole('checkbox', { name: '검수에 포함: 관람시간 확인 중' }).uncheck();
      await page.getByText('2개 포함', { exact: true }).waitFor();
      assert.equal(latest, null, 'unchecking stays on screen until the review starts');
    }
    if (!documentMode) await page.locator('#requirements').fill('행사명은 봄');
    if (mode === 'pdf') {
      // 포스터 검사 구성이 준비되지 않았으면 기준을 승인하기 전에 멈춘다.
      configured = false;
      await start.click();
      await page.getByText(/포스터 검사·보고 구성이 준비되지/).waitFor();
      assert.equal(latest, null); assert.equal(approved, false); assert.equal(uploads, 0);
      configured = true;
    }
    // '검수 시작하기' 하나로 (직접 입력 분석 →) 기준 승인 → 포스터 등록 → 검수 접수까지 간다.
    await start.click();
    if (mode === 'direct') {
      try {
        await overlay.waitFor();
        const spinner = overlay.locator('.rv-start-spinner');
        assert.equal(await spinner.evaluate(node => getComputedStyle(node).animationName), 'rv-button-spin');
        assert.equal(await overlay.evaluate(node => getComputedStyle(node, '::backdrop').backgroundColor), 'rgba(0, 0, 0, 0.48)');
        await page.keyboard.press('Escape');
        await page.keyboard.press('Tab');
        assert.ok(await overlay.evaluate(node => node.matches(':modal') &&
          (document.activeElement === document.body || node.contains(document.activeElement))));
        const chat = page.locator('.cc-header-help').getByRole('button', { name: '사용 안내', exact: true, includeHidden: true });
        const box = await chat.boundingBox();
        await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
        assert.equal(await chat.getAttribute('aria-expanded'), 'false');
        assert.ok(await overlay.isVisible());
        if (process.env.E2E_PROGRESS_SCREENSHOT_DIR) {
          mkdirSync(process.env.E2E_PROGRESS_SCREENSHOT_DIR, { recursive: true });
          await page.screenshot({ path: process.env.E2E_PROGRESS_SCREENSHOT_DIR + '/review-start-overlay.png' });
        }
      } finally { textAdmission.resolve(); }
    }
    if (adding) {
      // 직접 입력을 분석하는 동안 진행은 준비 창에만 보인다. 문서 첨부 카드는 추출을 마친 모습 그대로여서
      // 문서를 한 번 더 추출하는 것처럼 보이지 않는다(서버도 직접 입력만 분석한다).
      try {
        await overlay.getByText('입력한 요구사항의 분석 차례를 기다리고 있습니다.', { exact: true }).waitFor();
        assert.equal(await documentCard.getByRole('progressbar').count(), 0);
        assert.equal(await documentCard.locator('.rv-document-art').count(), 0);
        assert.equal(await documentCard.getAttribute('aria-busy'), null);
        assert.equal(await documentCard.locator('.rv-upload-name').textContent(), 'brief.pdf');
        assert.equal(await documentCard.locator('.rv-document-summary').textContent(), '추출 완료 · 1페이지');
        assert.equal(extractionRequests, 1);
      } finally { additionReady = true; }
    }
    if (failedAddition) {
      // 분석 직후와 실패 실행을 다시 연 화면에서 같은 항목·원인·원문을 확인할 수 있다.
      await overlay.waitFor({ state: 'hidden' });
      const failure = page.getByRole('region', { name: '분석하지 못한 항목과 원인', exact: true });
      await failure.getByText('항목 1', { exact: true }).waitFor();
      await failure.getByText(/미확정 항목에 값·표시 문구 또는 자동 비교 방식/).waitFor();
      await failure.getByText('확인할 내용: 화면 표시 문구 · 판정 방식', { exact: true }).waitFor();
      await failure.getByText(directInput, { exact: true }).waitFor();
      assert.equal(await page.locator('#requirements').inputValue(), directInput);
      assert.equal(approved, false); assert.equal(uploads, 0);
      assert.equal(latest, null, 'failed additions must not replace PDF conditions');
      await failure.getByRole('link', { name: '이 분석의 실패 기록 보기', exact: true }).click();
      await page.waitForURL('**/review/new?framework=input-job');
      await failure.getByText(directInput, { exact: true }).waitFor();
      await page.reload();
      await failure.getByText('직접 입력 원문', { exact: true }).waitFor();
      await failure.getByText('확인할 내용: 화면 표시 문구 · 판정 방식', { exact: true }).waitFor();
      assert.equal(await page.getByText('private provider model response', { exact: false }).count(), 0);
      assert.equal(additionRequests, 1);
      assert.deepEqual(errors, []);
      await page.close();
      continue;
    }
    if (noNew) {
      try {
        await overlay.getByText('추가할 조건이 없어 기존 문서 기준으로 진행합니다.', { exact: true }).waitFor();
        assert.deepEqual(latest.version.criteria.map(item => item.criterion_id), ['title', 'subtitle']);
        assert.equal(await page.getByRole('button', { name: '확인 필요 항목 1개 제외하고 바로 검수', exact: true }).count(), 0);
      } finally { criteriaApproval.resolve(); }
    }
    if (mode === 'not_ready') {
      await page.getByText(/포스터 검사·보고 구성이 준비되지/).waitFor();
      assert.equal(textInputs.length, 0);
      await overlay.waitFor({ state: 'hidden' });
      assert.ok(await start.isEnabled());
    } else {
      if (mode === 'blocker' || mode === 'skip') {
        // 값이 정해지지 않은 기준이 있으면 승인하지 않고 같은 화면에서 멈춘다. 그 항목은 목록의 번호로 짚어 준다.
        const skip = page.getByRole('button', { name: '확인 필요 항목 1개 제외하고 바로 검수', exact: true });
        await skip.waitFor();
        await overlay.waitFor({ state: 'hidden' });
        await page.waitForURL('**/review/new?framework=draft');
        assert.equal(await criteriaOnlyPage.count(), 0);
        assert.ok(await start.isDisabled());
        assert.equal(approved, false); assert.equal(uploads, 0); assert.equal(reviewed, false);
        if (mode === 'blocker') {
          await page.getByRole('button', { name: '01번 항목 확인하기', exact: true }).click();
          // 남는 기준이 하나도 없으면 빼고 시작할 수 없다.
          assert.ok(await skip.isDisabled());
          assert.deepEqual(errors, []);
          await page.close();
          continue;
        }
        await page.getByRole('button', { name: '02번 항목 확인하기', exact: true }).waitFor();
        await skip.click();
      }
      const steps = page.getByRole('navigation', { name: '검수 단계' });
      const stageStatus = page.locator('.rv-step-status');
      if (mode === 'direct') {
        // 직접 입력만으로 시작하면 분석이 끝난 뒤 묻지 않고 기준 승인으로 이어 간다. 그동안 화면은 잠긴다.
        try {
          await page.waitForFunction(() => document.querySelector('.rv-step-status')?.textContent === '기준을 승인하고 있어요.');
          await overlay.waitFor();
          assert.ok(await overlay.getByText('기준을 승인하고 있어요.').isVisible());
          const backgroundSubmit = page.locator('button').filter({ hasText: /^검수 시작하기$/ });
          assert.ok(await backgroundSubmit.isDisabled());
          assert.equal(await page.locator('.rv-step-nav button:disabled').count(), 4);
          assert.ok(await page.getByRole('checkbox', { name: '검수에 포함: 행사명은 봄', includeHidden: true }).isDisabled());
          await backgroundSubmit.evaluate(button => button.click());
        } finally {
          criteriaApproval.resolve();
          posterApproval.resolve();
        }
      }
      await page.getByRole('heading', { name: '포스터의 문구와 시각 조건을 확인하고 있어요.' }).waitFor();
      await overlay.waitFor({ state: 'hidden' });
      assert.equal(posterApprovalRequests, 1);
      assert.equal(uploads, 1);
      assert.deepEqual(textInputs, documentMode ? [] : [{ text: '행사명은 봄' }]);
      if (adding) {
        assert.equal(extractionRequests, 1);
        assert.equal(additionRequests, 1);
        // 접수한 뒤 그 초안을 다시 열면 승인된 기준에 직접 입력으로 더한 항목과 그 원문이 남아 있다.
        await page.goto(base + '/review/new?framework=draft');
        if (noNew) {
          await checklist.getByRole('checkbox', { name: '검수에 포함: 행사명은 봄', exact: true }).waitFor();
          assert.deepEqual(await checklist.getByRole('checkbox').evaluateAll(rows => rows.map(row => row.getAttribute('aria-label'))),
            ['검수에 포함: 행사명은 봄', '검수에 포함: 보조 제목은 전시']);
          assert.equal(await checklist.getByText('검수해줘', { exact: true }).count(), 0);
          assert.deepEqual(errors, []);
          await page.close();
          continue;
        }
        await checklist.getByRole('button').filter({ hasText: '직접 입력' }).click();
        assert.deepEqual(await checklist.getByRole('checkbox').evaluateAll(rows => rows.map(row => row.getAttribute('aria-label'))),
          ['검수에 포함: 행사명은 봄', '검수에 포함: 보조 제목은 전시', '검수에 포함: 장소를 아카이브 홀로 표기']);
        const details = page.locator('#rv-active-criterion');
        await details.getByText('직접 입력 원문', { exact: true }).waitFor();
        await details.getByText(directInput, { exact: true }).waitFor();
        assert.deepEqual(errors, []);
        await page.close();
        continue;
      }
      if (subset) {
        assert.equal(extractionRequests, 1);
        assert.deepEqual(errors, []);
        await page.close();
        continue;
      }
      const progress = page.getByRole('progressbar', { name: '검수 처리 진행 상태' });
      await progress.waitFor();
      assert.equal(await progress.getAttribute('aria-valuenow'), '0');
      await page.getByText('처리 단계 기준 진행률', { exact: true }).waitFor();
      allowReviewStages = true;
      await page.waitForFunction(() => document.querySelector('.rv-progress-track')?.getAttribute('aria-valuenow') === '67');
      await page.waitForFunction(() => {
        const bar = document.querySelector('.rv-progress-track');
        return bar && Math.abs(bar.firstElementChild.getBoundingClientRect().width - bar.clientWidth * .67) < 2;
      });
      assert.equal(await progress.locator('.rv-progress-shine').count(), 0);
      // 채움은 무늬 없이 화면의 잉크 색(--rv-ink, 기본 #171717) 한 가지다. 테마가 잉크 색을 바꾸면 그 색을 따른다.
      const fill = await progress.locator('.rv-progress-fill').evaluate(element => {
        const ink = document.createElement('i'); ink.style.backgroundColor = 'var(--rv-ink)'; element.append(ink);
        const seen = { color: getComputedStyle(element).backgroundColor, image: getComputedStyle(element).backgroundImage,
          ink: getComputedStyle(ink).backgroundColor };
        ink.remove();
        return seen;
      });
      assert.notEqual(fill.ink, 'rgba(0, 0, 0, 0)');
      assert.deepEqual([fill.color, fill.image], [fill.ink, 'none']);
      assert.equal(await page.getByRole('heading', { name: '검수 요구사항', exact: true }).count(), 0, 'partial validation must not hide the running report stage');
      assert.equal(await stageStatus.textContent(), '검수 보고서 정리');
      assert.equal(await steps.locator('button:disabled').count(), 4);
      if (mode === 'pdf' && process.env.E2E_PROGRESS_SCREENSHOT_DIR) {
        await page.screenshot({ path: process.env.E2E_PROGRESS_SCREENSHOT_DIR + '/poster-progress.png', fullPage: true });
      }
      allowReviewResult = true;
      await page.getByRole('heading', { name: '검수 요구사항', exact: true }).waitFor();
      assert.equal(await stageStatus.textContent(), '');
      assert.ok(await steps.getByRole('button', { name: /보고서/ }).isEnabled());
      const columns = await page.locator('.rv-result-workbench').evaluate(element =>
        Array.from(element.children, child => { const rect = child.getBoundingClientRect(); return { left: rect.left, right: rect.right, top: rect.top, height: rect.height }; }));
      assert.equal(columns.length, 2);
      assert.ok(columns.every(column => column.height > 200 && Math.abs(column.top - columns[0].top) < 2));
      assert.ok(columns[0].right <= columns[1].left, 'poster and requirements must form two columns');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.getByRole('button', { name: '보고서 보기', exact: true }).click();
      await page.getByText('보고서 원문 보기', { exact: true }).click();
      await page.getByText('A07 보고: 행사명이 기준과 다릅니다.', { exact: true }).waitFor();
      await page.getByText('행사명을 봄으로 수정해 주세요.', { exact: true }).waitFor();
      await page.getByRole('heading', { name: '요구사항별 결과', exact: true }).waitFor();
      await page.locator('.rv-report-result').getByText(/^관측 ‘여름’/).waitFor();
      await page.locator('.rv-report-result').getByRole('button', { name: /^01\. .+ 위치 보기$/ }).click();
      const viewer = page.getByRole('dialog', { name: /^01\. / });
      await viewer.locator('img').evaluate(image => image.complete || new Promise(done => image.addEventListener('load', done, { once: true })));
      await viewer.getByTestId('evidence-box').waitFor();
      const shown = await viewer.evaluate(dialog => {
        const image = dialog.querySelector('.image-stage-surface img').getBoundingClientRect();
        const boxes = [...dialog.querySelectorAll('[data-testid="evidence-box"]')];
        const box = boxes[0].getBoundingClientRect(), frame = dialog.getBoundingClientRect();
        return { count: boxes.length, selected: boxes[0].classList.contains('is-selected') && boxes[0].classList.contains('is-fail'),
          size: [frame.width / innerWidth, frame.height / innerHeight], pixels: [image.width, image.height],
          at: [(box.left - image.left) / image.width, (box.top - image.top) / image.height, (box.right - image.left) / image.width, (box.bottom - image.top) / image.height] };
      });
      assert.equal(shown.count, 1); assert.ok(shown.selected);
      shown.size.forEach(value => assert.ok(Math.abs(value - .5) < .02, 'viewer is half the screen ' + shown.size));
      // 시험 이미지는 10×10px라 작게 그려진다. 위치 오차는 비율 3% 또는 3px 안이면 같은 자리로 본다.
      [.1, .1, .8, .3].forEach((value, index) => assert.ok(Math.abs(shown.at[index] - value) * shown.pixels[index % 2] <= Math.max(3, .03 * shown.pixels[index % 2]),
        'box follows bbox ' + shown.at + ' on ' + shown.pixels));
      await page.keyboard.press('Escape');
      await viewer.waitFor({ state: 'detached' });
      await page.getByRole('button', { name: '인쇄 / PDF 저장', exact: true }).waitFor();
      await page.getByText('기계 판정 · 불충족', { exact: true }).waitFor();
      await page.getByRole('button', { name: '결과 확인으로 돌아가기', exact: true }).click();
      // 접수한 뒤에는 승인된 기준을 읽기 전용으로 다시 볼 수 있다.
      await page.getByRole('button', { name: '기준 다시 확인하기', exact: true }).click();
      await page.getByRole('heading', { name: '승인한 검수 기준', exact: true }).waitFor();
      assert.equal(await criteriaOnlyPage.count(), 0);
      assert.equal(await page.getByRole('button', { name: '기준 보완', exact: true }).count(), 0, 'approved criteria stay read-only');
      await page.reload();
      await page.getByRole('button', { name: '보고서 보기', exact: true }).click();
      await page.getByText('보고서 원문 보기', { exact: true }).click();
      await page.getByText('A07 보고: 행사명이 기준과 다릅니다.', { exact: true }).waitFor();
    }
    assert.deepEqual(legacy, [], mode); assert.deepEqual(errors, [], mode);
    await page.close();
  }
}

// 여섯 가지 흐름을 차례로 끝까지 돈다(흐름 하나에 10~15초).
test('직접 입력·PDF 입력을 한 화면에서 승인과 보고로 연결하고 미설정은 접수 전에 막는다', { timeout: 150000 }, t =>
  verifyFrameworkEntry(t, ['text', 'pdf', 'direct', 'blocker', 'skip', 'not_ready']));

test('포스터 PDF 부분 초안은 제외된 후보의 사유와 원문을 기준 목록과 분리한다', { timeout: 30000 }, t =>
  verifyFrameworkEntry(t, ['pdf'], { checkRejectedCandidates: true }));

test('검수 항목에서 체크를 푼 기준은 검수를 시작할 때 제외로 기록한다', { timeout: 30000 }, t =>
  verifyFrameworkEntry(t, ['pdf_subset']));

test('문서 기준에 직접 입력만 더해 접수하고, 다시 열면 그 원문이 남아 있다', { timeout: 30000 }, t =>
  verifyFrameworkEntry(t, ['pdf_addition']));

test('검수해줘는 추가 기준을 만들지 않고 선택한 PDF 기준으로 검수한다', { timeout: 30000 }, t =>
  verifyFrameworkEntry(t, ['pdf_no_addition']));

test('입력 분석 실패 항목과 원인을 즉시 확인하고 실패 기록에서도 다시 읽는다', { timeout: 30000 }, t =>
  verifyFrameworkEntry(t, ['pdf_addition_failed']));
