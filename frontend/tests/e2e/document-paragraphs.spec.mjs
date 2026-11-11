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
    else if (path === '/api/framework/v1/health') body = { requirements_presentation: true, pdf_extraction_mode: 'qwen', poster_review_ready: true };
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
    else if (path.endsWith('/run-fixture/requirements')) body = { ...recorded, conditions: items, pending, page_count: 2, warnings: recorded?.warnings || [],
      framework: { run_id: 'run-fixture', document_version_id: 'document-fixture' } };
    // 관계 조회(이전 기준 대비 변경 · 수정본 이력)는 부가 안내라 이 시험에서는 자료 없음으로 답한다.
    else if (/\/(criteria-change-impact|revision-history)$/.test(path)) return route.fulfill({ status: 404, json: { detail: 'no relation data' } });
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
  const expectedTexts = [...new Set(items.map(item => item.text))];
  const extracted = page.getByLabel(/문서 요구사항 \d+번 문구/);
  const notice = page.getByText(new RegExp(`추출을 완료했어요.*${expectedTexts.length}개 추출${pendingOnly ? '.*미확정 1개' : ''}`));
  await notice.waitFor();
  assert.equal(await page.locator('#requirements').inputValue(), current);
  assert.deepEqual(await extracted.evaluateAll(fields => fields.map(field => field.value)), expectedTexts);
  if (process.env.E2E_REQUIREMENTS_SCREENSHOT) await page.screenshot({ path: process.env.E2E_REQUIREMENTS_SCREENSHOT, fullPage: true });
  await upload.setInputFiles({ name: 'brief.pdf', mimeType: 'application/pdf', buffer: data });
  await notice.waitFor();
  assert.equal(await page.locator('#requirements').inputValue(), current);
  assert.deepEqual(await extracted.evaluateAll(fields => fields.map(field => field.value)), expectedTexts);
  if (pendingOnly) {
    await page.getByRole('button', { name: 'PDF 원문과 기준 확인', exact: true }).click();
    await page.getByRole('heading', { name: '검수할 항목을 확정해 주세요.' }).waitFor();
    await page.getByText('요구사항 원문 · 1쪽', { exact: true }).waitFor();
    await page.locator('.rv-criteria-source').getByText(pending[0].source, { exact: true }).waitFor();
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

test('Word(.docx) 요구사항 문서는 DOCX 경로로 올리고 원본 해시를 대조한 뒤 같은 분석 결과를 반영한다', { timeout: 60000 }, async t => {
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
  let docxUploads = 0, pdfUploads = 0, admissions = 0;
  await page.route(base + '/api/**', async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname;
    let body;
    if (path === '/api/v1/auth/me') body = { authenticated: true, user: { id: 'user-fixture', display_name: '개발 검증' } };
    else if (path === '/api/health') body = { status: 'ok', ready: true };
    else if (path === '/api/framework/v1/health') body = { requirements_presentation: true, pdf_extraction_mode: 'qwen', poster_review_ready: true, docx_text_registration: true };
    else if (path === '/api/framework/v1/projects') body = { items: [{ id: 'project-fixture', name: 'DOCX 검증', my_role: 'owner' }], next_cursor: null };
    else if (path === '/api/framework/v1/csrf') body = { csrf_token: 'framework-browser-token' };
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
    else if (path === '/api/framework/v1/runs/run-docx') body = { execution_status: 'waiting_criteria', context: { run_id: 'run-docx' }, criteria: { criteria: [] } };
    else if (path.endsWith('/run-docx/requirements')) body = { conditions: items, pending: [], page_count: 1, warnings: [],
      framework: { run_id: 'run-docx', document_version_id: 'document-docx' } };
    else return route.fulfill({ status: 404, json: { detail: 'unexpected test route' } });
    return route.fulfill({ json: body });
  });
  await page.goto(base + '/review/new');
  await page.getByRole('heading', { name: '어떤 광고물을 검수할까요?' }).waitFor();
  await page.waitForFunction(() => !document.querySelector('input[aria-label="요구사항 PDF 선택"]').disabled);
  const upload = page.getByLabel('요구사항 PDF 선택', { exact: true });
  assert.match(await upload.getAttribute('accept'), /\.docx/);
  await page.getByText('텍스트 PDF(50페이지) · DOCX(본문 글자) · 최대 10MiB', { exact: true }).waitFor();
  await upload.setInputFiles({ name: '계약서.docx', mimeType: DOCX, buffer: docx });
  await page.getByText(/추출을 완료했어요\. Word 문서 · 2개 추출/).waitFor();
  assert.deepEqual(await page.getByLabel(/문서 요구사항 \d+번 문구/).evaluateAll(fields => fields.map(field => field.value)), items.map(item => item.text));
  await page.getByRole('button', { name: 'DOCX 본문과 기준 확인', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'PDF 원문과 기준 확인', exact: true }).count(), 0);
  if (process.env.E2E_DOCX_SCREENSHOT) await page.screenshot({ path: process.env.E2E_DOCX_SCREENSHOT, fullPage: true });
  assert.equal(docxUploads, 1); assert.equal(pdfUploads, 0); assert.equal(admissions, 1);
  assert.deepEqual(errors, []);
});