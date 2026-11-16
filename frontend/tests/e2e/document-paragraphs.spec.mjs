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

// 포스터·동영상 검수 모두 자료 입력 전에 보안 여부와 검수 유형을 고른다. 요구사항 문서 칸이 있는 문구 검수로 연다.
async function chooseReview(page, title = '문구 검수 자료 입력') {
  await page.getByRole('heading', { name: '보안이 필요한 자료인가요?', exact: true }).waitFor();
  await page.getByRole('button', { name: '일반 자료', exact: true }).click();
  await page.getByRole('heading', { name: '검수할 항목을 선택해 주세요', exact: true }).waitFor();
  await page.getByRole('button', { name: '문구 검수', exact: true }).click();
  await page.getByRole('heading', { name: title, exact: true }).waitFor();
}

// 문서를 추출하면 같은 화면에 그 실행의 검수 항목이 나타난다(기준만 따로 확인하는 화면으로 넘어가지 않는다).
for (const mode of ['resolved', 'pending_empty', 'pending_existing', 'pending_visual']) {
test(`PDF에서 만든 검수 항목·미확정 항목을 같은 화면에서 확인하고 보완한다 (${mode})`, { timeout: 60000 }, async t => {
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
  let uploads = 0, admissions = 0;
  const unexpectedRequests = [];
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
  const context = { run_id: 'run-fixture', project_id: 'project-fixture', actor_id: 'user-fixture' };
  const posterContext = { ...context, run_id: 'poster-fixture', asset_version_id: 'image' };
  // 값이 정해진 조건은 그대로 검수 항목이 되고, 미확정 조건은 기대값이 빈 항목으로 남는다.
  const criteria = pendingOnly ? pending.map(item => ({ criterion_id: item.criterion_id, text: item.source, expected: '',
    source: { page: item.page, quote: item.source }, kind: 'unsupported', operator: 'manual', unit: null, required: true,
    details: visual ? scope : { target: 'unspecified', field: 'unspecified' } }))
    : items.map((item, index) => ({ criterion_id: 'c' + (index + 1), text: item.text, expected: item.requirement || item.text,
      source: { page: item.page, quote: item.source }, kind: item.type === 'visual' ? 'visual' : 'text',
      operator: item.type === 'visual' ? 'present' : 'contains', unit: null, required: true,
      details: { target: item.type === 'visual' ? 'poster_visual' : 'poster_text', field: 'wording' } }));
  let latest = null, approved = false, reviewed = false, png;
  const saves = [];
  await page.route(base + '/api/**', async route => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    let body;
    if (path === '/api/v1/auth/me') body = { authenticated: true, user: { id: 'user-fixture', display_name: '개발 검증' } };
    else if (path === '/api/health') body = { status: 'ok', ready: true };
    else if (path === '/api/framework/v1/health') body = { requirements_presentation: true, pdf_extraction_mode: 'qwen', poster_review_ready: true };
    else if (path === '/api/framework/v1/projects') body = { items: [{ id: 'project-fixture', name: 'PDF 검증', my_role: 'owner' }], next_cursor: null };
    else if (path === '/api/framework/v1/csrf') body = { csrf_token: 'framework-browser-token' };
    else if (path === '/api/poster-logos') body = { items: [] };
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
      context, criteria: { criteria } };
    else if (path.endsWith('/run-fixture/criteria-review')) body = { latest_version: latest, source_draft_sha256: 'a'.repeat(64) };
    else if (path.endsWith('/run-fixture/criteria-versions')) {
      saves.push({ body: request.postDataJSON(), key: request.headers()['idempotency-key'] });
      if (mode === 'pending_existing' && saves.length === 1) return route.fulfill({ status: 503, json: { detail: '저장 응답 확인 필요' } });
      const edit = request.postDataJSON().edits[0];
      assert.equal(edit.action, 'amend');
      assert.deepEqual(edit.resolution, { ...scope, kind: visual ? 'visual' : 'text', operator: visual ? 'present' : 'contains',
        expected: value, unit: null, required: true, ...(visual ? { replacement_text: reply } : {}) });
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
    // '검수 시작하기'는 기준 승인에 이어 포스터를 등록하고 검수를 접수한다.
    else if (path.endsWith('/images')) { assert.ok(approved, 'image registration follows criteria approval'); body = { id: 'image' }; }
    else if (path.endsWith('/images/image/content')) return route.fulfill({ contentType: 'image/png', body: png });
    else if (path.endsWith('/run-fixture/poster-reviews')) {
      assert.equal(request.postDataJSON().criteria_version_id, latest.version.criteria_version_id);
      body = { context: posterContext };
    } else if (path.endsWith('/poster-fixture/approvals')) { reviewed = true; body = { execution_status: 'reviewing', context: posterContext }; }
    else if (path === '/api/framework/v1/runs/poster-fixture') body = { execution_status: 'reviewing', context: posterContext,
      baseline: { criteria_version_id: 'version-1', criteria_sha256: 'b'.repeat(64) }, criteria: { criteria: latest?.version.criteria || [] } };
    else if (path.endsWith('/run-fixture/requirements')) body = { ...recorded, conditions: items, pending, page_count: 2,
      // 서버는 미확정 항목이 있으면 경고 첫 줄에 그 개수를 적는다(backend requirements_presentation.py).
      warnings: pendingOnly ? ['미확정 1개는 조건 목록에 넣지 않았어요. 문서에 값이 아직 정해지지 않은 항목이에요.'] : recorded?.warnings || [],
      framework: { run_id: 'run-fixture', document_version_id: 'document-fixture' } };
    // 관계 조회(이전 기준 대비 변경 · 수정본 이력)는 부가 안내라 이 시험에서는 자료 없음으로 답한다.
    else if (/\/(criteria-change-impact|revision-history)$/.test(path)) return route.fulfill({ status: 404, json: { detail: 'no relation data' } });
    else { unexpectedRequests.push(path); return route.fulfill({ status: 404, json: { detail: 'unexpected test route' } }); }
    return route.fulfill({ json: body });
  });
  await page.goto(base + '/review/new');
  try { await chooseReview(page); }
  catch (error) { throw new Error(`${error.message}\n${page.url()}\n${(await page.locator('body').innerText()).slice(0, 1800)}\n${errors.join('\n')}`); }
  const current = mode === 'pending_empty' ? '' : '직접 입력: 보존';
  await page.locator('#requirements').fill(current);
  await page.waitForFunction(() => !document.querySelector('input[aria-label="요구사항 문서 선택"]').disabled);
  const upload = page.getByLabel('요구사항 문서 선택', { exact: true });
  if (mode === 'resolved') {
    const card = page.getByRole('region', { name: '요구사항 문서 첨부', exact: true });
    const heading = card.getByRole('heading', { name: '요구사항 문서 첨부', exact: true });
    const dataTransfer = await page.evaluateHandle(bytes => {
      const transfer = new DataTransfer();
      transfer.items.add(new File([new Uint8Array(bytes)], 'brief.pdf', { type: 'application/pdf' }));
      return transfer;
    }, [...data]);
    const drag = { dataTransfer };
    await heading.dispatchEvent('dragenter', drag);
    await card.getByText('여기에 PDF·DOCX를 놓으세요.', { exact: true }).waitFor();
    await heading.dispatchEvent('dragleave', drag);
    await card.getByText('여기에 PDF·DOCX를 놓으세요.', { exact: true }).waitFor({ state: 'hidden' });
    await heading.dispatchEvent('dragenter', drag);
    await heading.dispatchEvent('dragover', drag);
    await heading.dispatchEvent('drop', drag);
    await dataTransfer.dispose();
    await card.getByText('여기에 PDF·DOCX를 놓으세요.', { exact: true }).waitFor({ state: 'hidden' });
  } else {
    await upload.setInputFiles({ name: 'brief.pdf', mimeType: 'application/pdf', buffer: data });
  }
  const checklist = page.getByRole('list', { name: '검수 항목 목록' });
  const notice = page.getByText('추출 완료 · 2페이지', { exact: true });
  // 추출이 끝나면 같은 화면에 검수 항목이 나타나고, 주소에 그 실행의 번호가 붙는다. 직접 입력란은 그대로다.
  async function expectDraft() {
    await notice.waitFor();
    await checklist.getByRole('checkbox').first().waitFor();
    assert.equal(new URL(page.url()).searchParams.get('framework'), 'run-fixture');
    assert.equal(await page.getByRole('heading', { name: '검수할 항목을 확정해 주세요.' }).count(), 0);
    assert.equal(await page.locator('#requirements').inputValue(), current);
    await page.getByText(`${criteria.length}개 포함`, { exact: true }).waitFor();
    // 목록은 여섯 개씩 넘겨 본다.
    assert.deepEqual(await checklist.getByRole('checkbox').evaluateAll(rows => rows.map(row => row.getAttribute('aria-label'))),
      criteria.slice(0, 6).map(item => '검수에 포함: ' + item.text));
  }
  await expectDraft();
  if (pendingOnly) await checklist.getByText('기대값 미정', { exact: true }).waitFor();
  if (process.env.E2E_REQUIREMENTS_SCREENSHOT) await page.screenshot({ path: process.env.E2E_REQUIREMENTS_SCREENSHOT, fullPage: true });
  // 같은 문서를 다시 첨부하면 저장된 원문과 분석을 다시 쓴다(올리기·접수를 되풀이하지 않는다).
  await checklist.evaluate(list => { list.dataset.before = 'yes'; });
  await upload.setInputFiles({ name: 'brief.pdf', mimeType: 'application/pdf', buffer: data });
  await page.waitForFunction(() => { const list = document.querySelector('.rv-criteria-list'); return list && !list.dataset.before; });
  await expectDraft();
  if (pendingOnly) {
    await page.getByText('요구사항 원문 · 1쪽', { exact: true }).waitFor();
    await page.locator('.rv-criteria-source').getByText(pending[0].source, { exact: true }).waitFor();
    const start = page.getByRole('button', { name: '검수 시작하기', exact: true });
    await page.getByRole('button', { name: '기준 보완', exact: true }).click();
    const save = page.getByRole('button', { name: '보완 내용 저장', exact: true });
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
    if (visual) {
      await page.getByLabel('회신에 따라 조건 자체를 변경', { exact: true }).check();
      await save.click();
      assert.equal(saves.length, 0);
      await page.getByLabel('변경한 전체 조건', { exact: true }).fill(reply);
    }
    await save.click();
    if (mode === 'pending_existing') {
      await page.getByRole('button', { name: '서버 상태 다시 확인' }).waitFor();
      assert.equal(await page.getByLabel('보완값', { exact: true }).inputValue(), value);
      assert.equal(approved, false);
      await save.click();
    }
    await page.getByText('보완 회신: ' + reply + ' · 출처: 개발용 담당자 회신', { exact: true }).waitFor();
    assert.equal(saves.length, mode === 'pending_existing' ? 2 : 1);
    assert.ok(saves[0].key);
    if (mode === 'pending_existing') assert.deepEqual(saves[0], saves[1]);
    await page.getByRole('button', { name: '기준 보완', exact: true }).click();
    assert.equal(await page.getByLabel('검사 대상', { exact: true }).inputValue(), scope.target);
    assert.equal(await page.getByLabel('검사 항목', { exact: true }).inputValue(), scope.field);
    await page.getByRole('button', { name: '보완 취소' }).click();
    // 포스터가 없으면 시작할 수 없다. 포스터를 넣고 시작하면 보완한 기준을 승인하고 검수를 접수한다.
    assert.ok(await start.isDisabled());
    assert.equal(approved, false);
    png = Buffer.from(await page.evaluate(() => {
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 10;
      return canvas.toDataURL().split(',')[1];
    }), 'base64');
    await page.getByLabel('검수 이미지 선택', { exact: true }).setInputFiles({ name: 'poster.png', mimeType: 'image/png', buffer: png });
    // 직접 입력란에 글이 남아 있으면 시작할 때 그 글을 분석해 기준에 더한다(framework-entry의 pdf_addition이 확인한다). 여기서는 비우고 시작한다.
    await page.locator('#requirements').fill('');
    await start.click();
    await page.waitForURL(url => url.searchParams.get('framework') === 'poster-fixture');
    assert.equal(approved, true);
    assert.equal(reviewed, true);
    assert.equal(saves.length, mode === 'pending_existing' ? 2 : 1, 'starting does not save the criteria again');
  }
  assert.equal(uploads, 1); assert.equal(admissions, 1); assert.deepEqual(unexpectedRequests, []);
  assert.deepEqual(errors, []);
});
}

async function verifyDocx(t, mode) {
  const video = mode !== 'poster';
  const DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
  if (!process.env.E2E_REQUIREMENTS_URL) {
    const child = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5177', '--strictPort'], {
      cwd: root, stdio: 'pipe', env: { ...process.env, VITE_API_PROFILE: 'legacy' },
    });
    let output = ''; child.stdout.on('data', data => { output += data; }); child.stderr.on('data', data => { output += data; });
    t.after(() => child.exitCode === null ? new Promise(resolve => { child.once('exit', resolve); child.kill(); }) : undefined);
    let ready = false;
    for (let i = 0; i < 80 && !ready; i++) {
      if (child.exitCode !== null) throw new Error(output);
      try { ready = (await fetch(base)).ok; } catch {}
      if (!ready) await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.ok(ready, output);
  }
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(10000);
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const docx = Buffer.concat([Buffer.from([0x50, 0x4b, 0x03, 0x04]), Buffer.from('docx fixture')]);
  const items = [['행사명', '진연', 'information'], ['연주자', '전통 복식과 관모를 쓴 연주자 2명', 'visual']]
    .map(([label, requirement, type]) => ({ label, requirement, type, inspection: '', level: '필수', page: 1, source: requirement,
      text: type === 'visual' ? requirement : `${label}: ${requirement}` }));
  const docxCriteria = items.map((item, index) => ({ criterion_id: 'c' + (index + 1), text: item.text, expected: item.requirement,
    source: { page: 1, quote: item.source }, kind: item.type === 'visual' ? 'visual' : 'text', operator: item.type === 'visual' ? 'present' : 'contains',
    unit: null, required: true, details: { target: item.type === 'visual' ? 'poster_visual' : 'poster_text', field: 'wording' } }));
  let docxUploads = 0, pdfUploads = 0, admissions = 0;
  const videoJob = { id: 'video-docx', status: 'queued', stage: 'queued', project_name: 'DOCX 검증', criteria_version: 1 };
  const videoSubmissions = [];
  await page.route(base + '/api/**', async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname;
    let body;
    if (path === '/api/v1/auth/me') body = { authenticated: true, user: { id: 'user-fixture', display_name: '개발 검증' } };
    else if (path === '/api/health') body = { status: 'ok', ready: true };
    else if (path === '/api/framework/v1/health') body = { requirements_presentation: true, pdf_extraction_mode: 'qwen', poster_review_ready: true, docx_text_registration: true };
    else if (path === '/api/framework/v1/projects') body = { items: [{ id: 'project-fixture', name: 'DOCX 검증', my_role: 'owner' }], next_cursor: null };
    else if (path === '/api/framework/v1/csrf') body = { csrf_token: 'framework-browser-token' };
    else if (video && path === '/api/v1/auth/csrf') body = { csrf_token: 'video-browser-token' };
    else if (video && ['/api/video-reviews/status', '/api/video-logos/status'].includes(path)) body = { ready: true, project_reviews: true };
    else if (video && path === '/api/video-logos') body = { items: [] };
    else if (video && path === '/api/video-reviews' && request.method() === 'POST') {
      const form = await new Response(request.postDataBuffer(), {
        headers: { 'Content-Type': request.headers()['content-type'] },
      }).formData();
      assert.equal(form.get('project_id'), 'project-fixture');
      assert.equal(form.get('criteria_confirmed'), 'true');
      assert.match(request.headers()['idempotency-key'], /^[a-f0-9-]{36}$/);
      videoSubmissions.push(JSON.parse(form.get('criteria')));
      return route.fulfill({ status: 202, json: videoJob });
    }
    else if (video && path === '/api/video-reviews/video-docx') body = videoJob;
    else if (path.endsWith('/docx-documents')) {
      docxUploads++;
      assert.equal(url.searchParams.get('filename'), '계약서.docx');
      assert.equal(request.headers()['content-type'], DOCX);
      assert.equal(request.headers()['x-csrftoken'], 'framework-browser-token');
      assert.deepEqual(request.postDataBuffer(), docx);
      body = { document: { document_version_id: 'document-docx', sha256: 'c'.repeat(64), media_type: 'text/plain' },
        source: { sha256: createHash('sha256').update(docx).digest('hex'), converter: 'docx-text-v1' } };
    } else if (path.endsWith('/documents')) { pdfUploads++; return route.fulfill({ status: 500, json: { detail: 'PDF 경로를 쓰면 안 됩니다.' } }); }
    else if (path.endsWith('/extractions')) { admissions++; body = { context: { run_id: 'run-docx' }, execution_status: 'queued' }; }
    else if (path === '/api/framework/v1/runs/run-docx') body = { execution_status: 'waiting_criteria',
      context: { run_id: 'run-docx', project_id: 'project-fixture', actor_id: 'user-fixture' }, criteria: { criteria: docxCriteria } };
    else if (path.endsWith('/run-docx/criteria-review')) body = { latest_version: null, source_draft_sha256: 'a'.repeat(64) };
    else if (path === '/api/poster-logos') body = { items: [] };
    else if (/\/(criteria-change-impact|revision-history)$/.test(path)) return route.fulfill({ status: 404, json: { detail: 'no relation data' } });
    else if (path.endsWith('/run-docx/requirements')) body = { conditions: items, pending: [], page_count: 1, warnings: [],
      framework: { run_id: 'run-docx', document_version_id: 'document-docx' } };
    else return route.fulfill({ status: 404, json: { detail: 'unexpected test route' } });
    return route.fulfill({ json: body });
  });
  await page.goto(base + (video ? '/video' : '/review/new'));
  await chooseReview(page, video ? '숏폼 동영상 문구 검수 자료 입력' : undefined);
  await page.waitForFunction(() => document.querySelector('input[aria-label="요구사항 문서 선택"]')?.disabled === false);
  const card = page.getByRole('region', { name: '요구사항 문서 첨부' });
  const upload = page.getByLabel('요구사항 문서 선택', { exact: true });
  assert.match(await upload.getAttribute('accept'), /\.docx/);
  assert.equal(await upload.getAttribute('multiple'), null);
  assert.equal(await card.getByRole('link', { name: 'PDF 여러 개 올리기' }).count(), 0);
  await card.getByText('PDF 텍스트층 · DOCX 본문 · 최대 10MiB', { exact: true }).waitFor();
  if (mode === 'video-drop') {
    const dataTransfer = await page.evaluateHandle(bytes => {
      const data = new DataTransfer();
      data.items.add(new File([new Uint8Array(bytes)], '계약서.docx'));
      return data;
    }, [...docx]);
    await card.dispatchEvent('dragenter', { dataTransfer });
    await card.getByText('여기에 PDF·DOCX를 놓으세요.', { exact: true }).waitFor();
    await card.dispatchEvent('dragover', { dataTransfer });
    await card.dispatchEvent('drop', { dataTransfer });
    await dataTransfer.dispose();
  } else {
    const choose = page.waitForEvent('filechooser');
    await card.getByRole('button', { name: '파일 선택', exact: true }).click();
    await (await choose).setFiles({ name: '계약서.docx', mimeType: DOCX, buffer: docx });
  }
  // 영상은 추출한 조건을 그 자리에서 고르고, 포스터는 같은 화면의 검수 항목 칸에 그 실행의 기준이 나타난다.
  await page.getByText(video ? '추출 완료 · Word 문서 · 조건 2개' : '추출 완료 · Word 문서', { exact: true }).waitFor();
  // DOCX는 추출을 마친 뒤 '확인할 점'에 본문만 읽었다는 안내가 붙는다.
  await card.getByText(/Word 본문 글자만 읽었어요/).waitFor();
  if (video) assert.deepEqual(await page.getByLabel(/문서 요구사항 \d+번 문구/).evaluateAll(fields => fields.map(field => field.value)), items.map(item => item.text));
  if (video) {
    // 추출한 항목은 처음부터 모두 검수에 포함되어 있다. 1번의 체크를 풀어 2번만 보낸다.
    await page.getByRole('checkbox', { name: '문서 요구사항 1번 검수에 포함', exact: true }).uncheck();
    await page.getByLabel('검수 동영상 선택', { exact: true }).setInputFiles(
      fileURLToPath(new URL('../../src/assets/sample-shortform.mp4', import.meta.url)));
    await page.getByText('검수 포함 1 / 2개', { exact: true }).waitFor();
    await page.getByRole('button', { name: '검수 시작하기', exact: true }).click();
    await page.waitForURL('**/video?id=video-docx');
    assert.deepEqual(videoSubmissions, [[{
      id: 'criterion-1', text: items[1].text, type: 'visual',
      source: `계약서.docx · 본문\n${items[1].source}`,
    }]]);
  } else {
    const checklist = page.getByRole('list', { name: '검수 항목 목록' });
    await checklist.getByRole('checkbox').first().waitFor();
    assert.deepEqual(await checklist.getByRole('checkbox').evaluateAll(rows => rows.map(row => row.getAttribute('aria-label'))),
      items.map(item => '검수에 포함: ' + item.text));
    assert.equal(new URL(page.url()).searchParams.get('framework'), 'run-docx');
    if (process.env.E2E_DOCX_SCREENSHOT) await page.screenshot({ path: process.env.E2E_DOCX_SCREENSHOT, fullPage: true });
  }
  assert.equal(docxUploads, 1); assert.equal(pdfUploads, 0); assert.equal(admissions, 1);
  assert.deepEqual(errors, []);
}

test('포스터 DOCX를 등록하고 원본 해시를 대조한 뒤 분석 결과를 반영한다', { timeout: 60000 }, t => verifyDocx(t, 'poster'));
test('영상 DOCX를 파일 선택으로 첨부하고 체크한 기준만 검수에 보낸다', { timeout: 60000 }, t => verifyDocx(t, 'video-picker'));
test('영상 DOCX를 드래그앤드롭으로 첨부하고 체크한 기준만 검수에 보낸다', { timeout: 60000 }, t => verifyDocx(t, 'video-drop'));
