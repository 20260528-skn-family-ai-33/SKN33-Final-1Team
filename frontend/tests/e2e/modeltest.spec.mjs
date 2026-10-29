import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chromium } from 'playwright';
import { createServer } from 'vite';
import { CONTRACT, FILE_LIMITS, FILE_TYPES, STAGES, FILTER_SCOPES } from '../../src/pages/modeltest/modeltest.js';

for (const resizeMode of ['pixels', 'percent']) test(`similarity test preserves one hundred images and resize, then shows both metrics and selected previews (${resizeMode})`, { timeout: 90000 }, async t => {
  const server = await createServer({ configFile: false, envFile: false, root: process.cwd(), cacheDir: '../work/modeltest-vite-cache', define: { 'import.meta.env.VITE_API_PROFILE': JSON.stringify('legacy') }, server: { host: '127.0.0.1', port: 5187, strictPort: true, hmr: false } });
  await server.listen(); t.after(() => server.close()); const origin = 'http://127.0.0.1:5187';
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'msedge' }); t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } }); page.setDefaultTimeout(8000);
  const errors = []; const unexpected = []; let manifest; let png; let jpeg;
  const health = { contract: CONTRACT, ready: true, similarity_test: 1, similarity_resize_percent: true, similarity_reference_resize: true, stages: STAGES.map(stage => stage.id), formats: FILE_TYPES,
    limits: { ...Object.fromEntries(Object.entries(FILE_LIMITS).map(([key, value]) => [`${key}_bytes`, value])), total_bytes: 600 * 1024 ** 2 } };
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const request = route.request(); const url = new URL(request.url());
    if (url.origin !== origin) { unexpected.push(url.origin); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const json = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    if (url.pathname === '/api/v1/auth/me') return json({ authenticated: false });
    if (url.pathname.endsWith('/models')) return json({ models: [], families: {} });
    if (url.pathname.endsWith('/connections') || url.pathname.endsWith('/health')) return json({ ...health, connection_id: 'similarity-fixture' });
    if (url.pathname.includes('/previews/')) return json({ mime: 'image/jpeg', data: jpeg });
    if (url.pathname.endsWith('/runs') && request.method() === 'POST') {
      manifest = JSON.parse(request.postDataBuffer().toString().match(/name="config"\r\n\r\n([^\r]+)/)[1]);
      return json({ contract: CONTRACT, id: request.headers()['idempotency-key'], status: 'completed', media: { preview: 'preview-poster.jpg', original_width: 200, original_height: 100, width: 100, height: 50, resized: true },
        references: manifest.files.references.map((_, index) => ({ index, preview: `preview-reference-${index}.jpg`, original_width: 200, original_height: 100, width: 50, height: 25, resized: true })),
        stages: manifest.stages.map(stage => ({ ...stage, status: 'completed', output: { items: manifest.files.references.map((_, reference_index) => ({ reference_index, ...(stage.id === 'logo_similarity' ? { image_cosine_similarity: 0.9 } : { descriptor_similarity: 0.85, matches: 12 + reference_index, inliers: 10, inlier_ratio: 0.8, keypoints: [100, 90], decision: 'candidate_match', image_sizes: [[100, 50], [50, 25]], matched_points: [{ crop: [25, 10], reference: [12.5, 5] }, { crop: [50, 25], reference: [25, 12.5] }] }) })) } })) });
    }
    unexpected.push(url.pathname); return route.abort();
  });
  await page.goto(`${origin}/modeltest`);
  png = await page.evaluate(() => { const canvas = document.createElement('canvas'); canvas.width = 200; canvas.height = 100; canvas.getContext('2d').fillRect(0, 0, 200, 100); return canvas.toDataURL('image/png').split(',')[1]; });
  jpeg = await page.evaluate(() => { const canvas = document.createElement('canvas'); canvas.width = 100; canvas.height = 50; canvas.getContext('2d').fillRect(0, 0, 100, 50); return canvas.toDataURL('image/jpeg').split(',')[1]; });
  const buffer = Buffer.from(png, 'base64'); const candidate = i => ({ name: `candidate-${i}.png`, mimeType: 'image/png', buffer });
  await page.getByLabel('RunPod Connect의 SSH 명령', { exact: true }).fill('ssh root@203.0.113.10 -p 1234');
  await page.getByRole('button', { name: '연결 확인', exact: true }).click(); await page.getByText('Pod 연결됨', { exact: true }).waitFor();
  await page.getByRole('button', { name: '유사도 테스트', exact: true }).click();
  await page.getByLabel('유사도 테스트 모델 선택', { exact: true }).selectOption('google/siglip2-so400m-patch14-384');
  assert.equal(await page.getByRole('group', { name: '워크플로우 편집 방식' }).count(), 0);
  await page.locator('input[type=file][aria-label="기준 이미지"]').setInputFiles({ name: 'base.png', mimeType: 'image/png', buffer });
  await page.getByLabel('기준 이미지 가로', { exact: true }).fill('100'); await page.getByLabel('기준 이미지 세로', { exact: true }).fill('50');
  const referenceResize = page.getByRole('group', { name: '대조 이미지 다운사이징', exact: true });
  await page.getByLabel('대조 이미지 가로', { exact: true }).fill('40'); await page.getByLabel('대조 이미지 세로', { exact: true }).fill('20');
  await referenceResize.getByRole('button', { name: '원본 크기 사용', exact: true }).click();
  assert.equal(await page.getByLabel('대조 이미지 가로', { exact: true }).inputValue(), '');
  assert.equal(await page.getByLabel('기준 이미지 가로', { exact: true }).inputValue(), '100');
  await page.getByLabel('대조 이미지 가로', { exact: true }).fill('50'); await page.getByLabel('대조 이미지 세로', { exact: true }).fill('25');
  if (resizeMode === 'percent') {
    await page.getByLabel('기준 이미지 크기 입력 방식', { exact: true }).selectOption('percent');
    await page.getByLabel('기준 이미지 축소 비율', { exact: true }).fill('50');
    await page.getByLabel('대조 이미지 크기 입력 방식', { exact: true }).selectOption('percent');
    await page.getByLabel('대조 이미지 축소 비율', { exact: true }).fill('25');
  }
  const input = page.locator('input[type=file][aria-label="대조 이미지"]');
  await input.setInputFiles(Array.from({ length: 101 }, (_, i) => candidate(i)));
  await page.getByRole('alert').filter({ hasText: '최대 100개까지' }).waitFor(); assert.equal(await page.locator('.mt-file-gallery li').count(), 0);
  await input.setInputFiles(Array.from({ length: 100 }, (_, i) => candidate(i)));
  assert.equal(await page.locator('.mt-file-gallery li').count(), 100);
  const gallery = page.getByRole('list', { name: '대조 이미지 미리보기 목록' });
  assert.equal(await gallery.locator('strong, small').count(), 0);
  assert.equal(await gallery.evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length), 10);
  const remove = gallery.getByRole('button', { name: 'candidate-99.png 삭제', exact: true }); await remove.focus(); await page.keyboard.press('Enter');
  assert.equal(await gallery.locator('li').count(), 99); await input.setInputFiles(candidate(99));
  const buttonInside = await gallery.locator('li').first().evaluate(element => { const a = element.getBoundingClientRect(); const b = element.querySelector('button').getBoundingClientRect(); return b.top >= a.top && b.top < a.top + 5 && b.right <= a.right && b.right > a.right - 5; });
  assert.equal(buttonInside, true);
  await page.setViewportSize({ width: 600, height: 900 });
  assert.equal(await gallery.evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length), 5);
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByText('첨부·모델 설정 저장 완료', { exact: false }).waitFor(); await page.reload();
  await page.getByText('Pod 연결됨', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '유사도 테스트', exact: true }).getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('.mt-file-gallery li').count(), 100);
  assert.equal(await page.getByLabel('기준 이미지 크기 입력 방식', { exact: true }).inputValue(), resizeMode);
  assert.equal(await page.getByLabel('대조 이미지 크기 입력 방식', { exact: true }).inputValue(), resizeMode);
  if (resizeMode === 'percent') {
    assert.equal(await page.getByLabel('대조 이미지 축소 비율', { exact: true }).inputValue(), '25');
    await page.getByLabel('대조 이미지 크기 입력 방식', { exact: true }).selectOption('pixels');
  }
  assert.equal(await page.getByLabel('대조 이미지 가로', { exact: true }).inputValue(), '50');
  assert.equal(await page.getByLabel('대조 이미지 세로', { exact: true }).inputValue(), '25');
  if (resizeMode === 'percent') await page.getByLabel('대조 이미지 크기 입력 방식', { exact: true }).selectOption('percent');
  if (resizeMode === 'percent') {
    assert.equal(await page.getByLabel('기준 이미지 축소 비율', { exact: true }).inputValue(), '50');
    await page.getByLabel('기준 이미지 크기 입력 방식', { exact: true }).selectOption('pixels');
  }
  assert.equal(await page.getByLabel('기준 이미지 가로', { exact: true }).inputValue(), '100');
  assert.equal(await page.getByLabel('기준 이미지 세로', { exact: true }).inputValue(), '50');
  if (resizeMode === 'percent') await page.getByLabel('기준 이미지 크기 입력 방식', { exact: true }).selectOption('percent');
  assert.equal(await page.getByLabel('유사도 테스트 모델 선택', { exact: true }).inputValue(), 'google/siglip2-so400m-patch14-384');
  const clearAll = page.getByRole('button', { name: '대조 이미지 전체 해제', exact: true });
  const clearDialog = page.getByRole('dialog', { name: '대조 이미지 전체 해제', exact: true });
  await clearAll.focus(); await page.keyboard.press('Enter'); await clearDialog.waitFor();
  await page.keyboard.press('Escape'); await clearDialog.waitFor({ state: 'hidden' });
  assert.equal(await clearAll.evaluate(element => element === document.activeElement), true);
  assert.equal(await gallery.locator('li').count(), 100);
  await clearAll.click(); await clearDialog.getByRole('button', { name: '전체 해제', exact: true }).click();
  assert.equal(await page.locator('.mt-file-gallery li').count(), 0); assert.equal(await clearAll.isDisabled(), true);
  await page.getByText('첨부·모델 설정 저장 완료', { exact: false }).waitFor(); await page.reload();
  await page.getByText('Pod 연결됨', { exact: true }).waitFor();
  assert.equal(await page.locator('.mt-file-gallery li').count(), 0);
  assert.equal(await page.getByRole('button', { name: 'base.png 삭제', exact: true }).count(), 1);
  assert.equal(await page.getByLabel('기준 이미지 크기 입력 방식', { exact: true }).inputValue(), resizeMode);
  assert.equal(await page.getByLabel('대조 이미지 크기 입력 방식', { exact: true }).inputValue(), resizeMode);
  await input.setInputFiles(Array.from({ length: 100 }, (_, i) => candidate(i)));
  await page.getByRole('button', { name: '검수 실행', exact: true }).click();
  await page.getByRole('region', { name: '이미지별 유사도 표', exact: true }).waitFor();
  assert.equal(manifest.flow, 'similarity'); assert.equal(manifest.files.references.length, 100); assert.deepEqual(manifest.resize, resizeMode === 'percent' ? { percent: 50 } : { width: 100, height: 50 });
  assert.deepEqual(manifest.reference_resize, resizeMode === 'percent' ? { percent: 25 } : { width: 50, height: 25 });
  assert.deepEqual(manifest.stages.map(stage => stage.model_id), ['google/siglip2-so400m-patch14-384', 'vismatch/xfeat']);
  await page.getByRole('button', { name: '100. candidate-99.png', exact: true }).click();
  const detail = page.locator('.mt-similarity-results');
  await detail.getByRole('img', { name: '대조 이미지 100 미리보기', exact: true }).waitFor();
  await detail.getByRole('img', { name: '측정에 사용한 기준 이미지', exact: true }).waitFor();
  assert.match(await detail.innerText(), /원본 200 × 100px → 측정 100 × 50px/);
  assert.match(await detail.innerText(), /원본 200 × 100px → 측정 50 × 25px/);
  if (resizeMode === 'percent') assert.match(await detail.innerText(), /원본의 25%/);
  if (resizeMode === 'percent') assert.match(await detail.innerText(), /원본의 50%/);
  assert.match(await detail.locator('dl').innerText(), /0\.9000/); assert.match(await detail.locator('dl').innerText(), /0\.8500/); assert.match(await detail.locator('dl').innerText(), /111 \/ 10개/);
  await detail.getByRole('img', { name: '기하 검증 대응점 2쌍', exact: true }).waitFor();
  async function pointError() {
    return detail.locator('.mt-match-overlay-frame').evaluate(element => {
      const root = element.getBoundingClientRect();
      return [...element.querySelectorAll('img')].reduce((error, image, i) => {
        const box = image.getBoundingClientRect(); const scale = Math.min(box.width / image.naturalWidth, box.height / image.naturalHeight); const width = image.naturalWidth * scale; const height = image.naturalHeight * scale;
        const point = element.querySelectorAll('circle')[i];
        return error + Math.abs(Number(point.getAttribute('cx')) - (box.x - root.x + (box.width - width) / 2 + width * .25)) + Math.abs(Number(point.getAttribute('cy')) - (box.y - root.y + (box.height - height) / 2 + height * .2));
      }, 0);
    });
  }
  assert.ok(await pointError() < 1);
  await detail.getByRole('checkbox', { name: '기하 검증 대응점 표시' }).uncheck(); assert.equal(await detail.locator('.mt-match-overlay').count(), 0);
  await detail.getByRole('checkbox', { name: '기하 검증 대응점 표시' }).check();
  await page.setViewportSize({ width: 600, height: 900 });
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.ok(await pointError() < 1);
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByRole('button', { name: '디버그', exact: true }).click(); await page.getByRole('button', { name: '단계별 결과', exact: true }).click();
  await page.getByRole('navigation', { name: '결과 단계 선택' }).getByRole('button', { name: 'XFeat 특징점 대조' }).click();
  assert.equal(await page.getByRole('button', { name: '100. candidate-99.png', exact: true }).getAttribute('aria-pressed'), 'true');
  await clearAll.click(); await clearDialog.getByRole('button', { name: '전체 해제', exact: true }).click();
  assert.equal(await page.locator('.mt-file-gallery li').count(), 0);
  assert.equal(await page.getByRole('button', { name: 'base.png 삭제', exact: true }).count(), 1);
  assert.equal(await page.getByRole('button', { name: '100. candidate-99.png', exact: true }).getAttribute('aria-pressed'), 'true');
  assert.match(await detail.locator('dl').innerText(), /111 \/ 10개/);
  assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
});

test('HF registry persists, selects a compatible node, prepares separately and shows embedding results', { timeout: 90000 }, async t => {
  const server = await createServer({ configFile: false, envFile: false, root: process.cwd(), cacheDir: '../work/modeltest-vite-cache', define: { 'import.meta.env.VITE_API_PROFILE': JSON.stringify('legacy') }, server: { host: '127.0.0.1', port: 5187, strictPort: true, hmr: false } });
  await server.listen(); t.after(() => server.close()); const origin = 'http://127.0.0.1:5187';
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'msedge' }); t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } }); page.setDefaultTimeout(8000);
  const errors = []; const unexpected = []; const requests = []; let records = [];
  const profile = { model_id: 'google/siglip2-base-patch16-224', revision: 'main', resolved_revision: 'a'.repeat(40), name: 'SigLIP 등록', family: 'clip', status: 'unverified', tasks: ['image_embed', 'logo_similarity'], options: { normalize: true, pooling: 'projection' }, training: {}, reasons: [], fingerprint: 'space-one' };
  const families = { clip: { engine: 'transformers', defaults: profile.options, requirements: { transformers: '>=5.21,<6' } } };
  const health = { contract: CONTRACT, ready: true, registry_version: 1, workflow: { version: 1, agents: true, max_parallel: 2 }, stages: [...STAGES.map(stage => stage.id), 'model_prepare'], formats: FILE_TYPES, limits: { ...Object.fromEntries(Object.entries(FILE_LIMITS).map(([key, value]) => [`${key}_bytes`, value])), total_bytes: 600 * 1024 ** 2 } };
  page.on('pageerror', error => errors.push(error.message)); page.on('dialog', dialog => { if (dialog.type() === 'beforeunload') return dialog.accept(); errors.push(`Unexpected native ${dialog.type()}`); return dialog.dismiss(); });
  await page.route('**/*', async route => {
    const request = route.request(); const url = new URL(request.url());
    if (url.origin !== origin) { unexpected.push(url.origin); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const json = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    if (url.pathname === '/api/v1/auth/me') return json({ authenticated: false });
    if (url.pathname.endsWith('/models/inspect')) return json(profile);
    if (url.pathname === '/api/modeltest/ssh/models' && request.method() === 'POST') { records = [{ ...profile, ...request.postDataJSON(), id: 'registered-one' }]; return json(records[0]); }
    if (url.pathname === '/api/modeltest/ssh/models') return json({ models: records, families });
    if (url.pathname.endsWith('/models/registered-one')) {
      if (request.method() === 'DELETE') { records = []; return json({ deleted: true }); }
      records[0] = { ...records[0], ...request.postDataJSON() }; return json(records[0]);
    }
    if (url.pathname.endsWith('/connections')) return json({ ...health, connection_id: 'fixture' });
    if (url.pathname.endsWith('/runs') && request.method() === 'POST') {
      const config = JSON.parse(request.postDataBuffer().toString().match(/name="config"\r\n\r\n([^\r]+)/)[1]); requests.push(config);
      return json({ contract: CONTRACT, id: request.headers()['idempotency-key'], status: 'completed', stages: config.stages.map(stage => ({ ...stage, status: 'completed', output: { items: config.purpose === 'prepare' ? [{ text: '가중치 준비 완료' }] : [{ text: 'fixture embedding', vector: [1, 0], dimension: 2, space_id: 'space-one', model_id: profile.model_id, revision: profile.resolved_revision }] } })) });
    }
    unexpected.push(`${request.method()} ${url.pathname}`); return route.abort();
  });
  await page.goto(`${origin}/modeltest`); await page.getByRole('button', { name: '모델 관리', exact: true }).click();
  const library = page.getByRole('dialog', { name: '모델 관리', exact: true });
  await library.waitFor();
  assert.equal(await page.getByRole('button', { name: '모델 관리 닫기' }).evaluate(node => node === document.activeElement), true);
  await page.keyboard.press('Shift+Tab');
  assert.equal(await library.evaluate(node => node.contains(document.activeElement)), true, 'Dialog keyboard focus stays inside');
  const helpButton = library.getByRole('button', { name: '모델 관리 도움말', exact: true });
  const help = page.getByRole('dialog', { name: '모델 관리 도움말', exact: true });
  assert.equal(await help.isVisible(), false);
  await helpButton.focus(); await page.keyboard.press('Enter'); await help.waitFor();
  await page.waitForFunction(() => document.querySelector('.mt-help-popover:popover-open button') === document.activeElement);
  assert.equal(await help.getByRole('button', { name: '모델 관리 도움말 닫기', exact: true }).evaluate(node => node === document.activeElement), true);
  assert.equal(await help.getByText('가중치 준비와 샘플 실행은 별도 버튼으로 요청합니다.', { exact: false }).isVisible(), true);
  await page.keyboard.press('Escape'); await help.waitFor({ state: 'hidden' });
  assert.equal(await library.isVisible(), true, 'Escape closes only the help popover');
  assert.equal(await helpButton.evaluate(node => node === document.activeElement), true, 'Help returns focus to its invoker');
  await page.keyboard.press('Space'); await help.waitFor();
  await help.getByRole('button', { name: '모델 관리 도움말 닫기', exact: true }).click();
  await help.waitFor({ state: 'hidden' });
  await helpButton.click(); await help.waitFor();
  await library.getByRole('heading', { name: '모델 관리', exact: true }).click();
  await help.waitFor({ state: 'hidden' });
  assert.equal(await library.isVisible(), true, 'Clicking outside help keeps model management open');
  assert.equal(requests.length, 0, 'Help never starts preparation or inference');
  await page.getByLabel('추가할 HF 모델', { exact: true }).fill('https://huggingface.co/google/siglip2-base-patch16-224');
  await page.getByRole('button', { name: '메타데이터 확인', exact: true }).click();
  await page.getByRole('button', { name: '모델 등록·저장', exact: true }).click();
  await page.locator('.mt-library-list article').waitFor(); assert.equal(requests.length, 0);
  await page.reload(); await page.getByRole('button', { name: '모델 관리', exact: true }).click();
  await page.locator('.mt-library-list').getByText('SigLIP 등록', { exact: true }).waitFor();
  await page.keyboard.press('Escape'); await library.waitFor({ state: 'hidden' });
  assert.equal(await page.getByRole('button', { name: '모델 관리', exact: true }).evaluate(node => node === document.activeElement), true);
  await page.getByLabel('RunPod Connect의 SSH 명령', { exact: true }).fill('ssh root@203.0.113.10 -p 12345');
  await page.getByRole('button', { name: '연결 확인', exact: true }).click(); await page.getByText('Pod 연결됨', { exact: true }).waitFor();
  await page.getByRole('button', { name: '모델 관리', exact: true }).click();
  await page.getByRole('button', { name: '가중치·환경 준비', exact: true }).click();
  await library.waitFor({ state: 'hidden' });
  await page.waitForFunction(() => document.activeElement?.id === 'mt-results-title');
  await page.getByText('가중치 준비 완료', { exact: true }).waitFor(); assert.equal(requests[0].files.media, null); assert.equal(requests[0].purpose, 'prepare');
  await page.getByRole('button', { name: '에이전트', exact: true }).click();
  const tabs = page.locator('[data-board="image-agents"]');
  for (const name of ['A02 문서·기준 분석', 'A04 문구 검수', 'A05 시각 요소 검수', 'A06 근거·결과 검증', 'A07 보고서 작성']) { await tabs.getByRole('button', { name: `${name} 노드 이동`, exact: true }).press('Enter'); await page.getByRole('checkbox', { name: '이 에이전트 실행', exact: true }).uncheck(); }
  await tabs.getByRole('button', { name: 'A01 업무 총괄 노드 이동', exact: true }).press('Enter');
  await page.getByLabel('내부 단계 처리 유형', { exact: true }).selectOption('image_embed');
  await page.getByLabel('내부 단계 등록 모델', { exact: true }).selectOption('registered:registered-one');
  await page.locator('input[type=file][aria-label="포스터 이미지"]').setInputFiles({ name: 'image.png', mimeType: 'image/png', buffer: Buffer.from('fixture') });
  await page.locator('input[type=file][aria-label="발주서·계약서"]').setInputFiles({ name: 'contract.txt', mimeType: 'text/plain', buffer: Buffer.from('contract') });
  await page.getByRole('button', { name: '모델 관리', exact: true }).click();
  await page.getByRole('button', { name: '현재 워크플로우 샘플 실행', exact: true }).click();
  await library.waitFor({ state: 'hidden' });
  await page.waitForFunction(() => document.activeElement?.id === 'mt-results-title');
  await page.getByText('임베딩 차원', { exact: true }).waitFor();
  assert.equal(requests[1].purpose, 'sample'); assert.equal(requests[1].stages[0].registered_model, 'registered-one');
  assert.equal(requests[1].stages[0].model_fingerprint, 'space-one');
  await page.getByRole('button', { name: '모델 관리', exact: true }).click();
  await page.locator('.mt-library-list').getByRole('button', { name: '편집', exact: true }).click();
  await page.getByLabel('등록 모델 이름', { exact: true }).fill('수정한 SigLIP'); await page.getByRole('button', { name: '모델 등록·저장', exact: true }).click();
  await page.locator('.mt-library-list').getByText('수정한 SigLIP', { exact: true }).waitFor();
  assert.ok((await library.boundingBox()).width >= 900, 'Desktop management panel has room for model settings');
  await library.screenshot({ path: '../work/modeltest-hf-registry.png' });
  const deleteModel = page.locator('.mt-library-list').getByRole('button', { name: '삭제', exact: true });
  const confirmation = page.getByRole('dialog', { name: '모델 등록 삭제', exact: true });
  await deleteModel.click(); await confirmation.waitFor();
  assert.equal(await confirmation.getByRole('button', { name: '취소', exact: true }).evaluate(node => node === document.activeElement), true);
  await page.keyboard.press('Shift+Tab');
  assert.equal(await confirmation.getByRole('button', { name: '삭제', exact: true }).evaluate(node => node === document.activeElement), true);
  await page.keyboard.press('Tab');
  assert.equal(await confirmation.getByRole('button', { name: '취소', exact: true }).evaluate(node => node === document.activeElement), true);
  await page.keyboard.press('Escape'); await confirmation.waitFor({ state: 'hidden' });
  assert.equal(await library.isVisible(), true); assert.equal(records.length, 1);
  assert.equal(await deleteModel.evaluate(node => node === document.activeElement), true);
  await deleteModel.click(); await confirmation.getByRole('button', { name: '취소', exact: true }).click();
  await confirmation.waitFor({ state: 'hidden' }); assert.equal(records.length, 1);
  assert.equal(await deleteModel.evaluate(node => node === document.activeElement), true);
  await deleteModel.click();
  assert.equal(await confirmation.getByRole('button', { name: '삭제', exact: true }).evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(185, 28, 28)');
  await confirmation.getByRole('button', { name: '삭제', exact: true }).click();
  await page.locator('.mt-library-list article').waitFor({ state: 'detached' });
  assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
});

test('modeltest preserves files, keeps controls open, previews evidence and resumes/cancels the same job', { timeout: 90000 }, async t => {
  const server = await createServer({ configFile: false, envFile: false, root: process.cwd(), cacheDir: '../work/modeltest-vite-cache',
    define: { 'import.meta.env.VITE_API_PROFILE': JSON.stringify('legacy') }, server: { host: '127.0.0.1', port: 5187, strictPort: true, hmr: false } });
  await server.listen(); t.after(() => server.close());
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'msedge' });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage(); page.setDefaultTimeout(8000);
  const errors = []; const unexpected = []; let submissions = 0; let cancellations = 0; let previews = 0; let job; let submittedManifest;
  await page.addInitScript(() => {
    window.resultScrolls = 0;
    const scroll = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (...args) { if (this.id === 'mt-results-title') window.resultScrolls++; return scroll.apply(this, args); };
  });
  page.on('pageerror', error => errors.push(error.message)); page.on('dialog', dialog => { if (dialog.type() === 'beforeunload') return dialog.accept(); errors.push(`Unexpected native ${dialog.type()}`); return dialog.dismiss(); });
  const health = { contract: CONTRACT, ready: true, can_prepare: true, cancel: true, stages: STAGES.map(stage => stage.id),
    formats: FILE_TYPES, limits: { ...Object.fromEntries(Object.entries(FILE_LIMITS).map(([key, value]) => [`${key}_bytes`, value])), total_bytes: 600 * 1024 ** 2 } };
  let jpeg;
  await context.route('**/*', async route => {
    const request = route.request(); const url = new URL(request.url());
    if (url.origin !== origin) { unexpected.push(url.origin); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const json = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    if (url.pathname === '/api/modeltest/ssh/models') return json({ models: [], families: {} });
    if (url.pathname === '/api/v1/auth/me') return json({ authenticated: false });
    if (url.pathname === '/api/modeltest/ssh/connections') return json({ ...health, connection_id: 'fixture-connection' });
    if (url.pathname.endsWith('/health')) return json(health);
    if (url.pathname.endsWith('/runs') && request.method() === 'POST') {
      submissions++;
      submittedManifest = JSON.parse(request.postDataBuffer().toString().match(/name="config"\r\n\r\n([^\r]+)/)[1]);
      const frames = [0, 30].map((index, i) => ({ index, timestamp_seconds: i * 1.25, width: 80, height: 40, preview: `frame-${String(index).padStart(8, '0')}.jpg` }));
      job = { contract: CONTRACT, id: request.headers()['idempotency-key'], status: 'running', references: [0, 1].map(index => ({ index, preview: `preview-reference-${index}.jpg` })),
        stages: [{ id: 'frames', status: 'completed', output: { frames } },
          { id: 'logo_extract', model_id: 'google/owlv2-base-patch16', status: 'completed', output: { items: frames.map((frame, index) => ({ ...frame, logos: [0, 1].map(logo => ({ bbox: [8 + logo * 32, 4, 40 + logo * 32, 20], logo_index: logo, preview: `logo-${index}-${logo}.png`, score: 0.8 })) })) } },
          { id: 'logo_compare', model_id: 'vismatch/xfeat', status: 'running', output: { items: frames.map(frame => ({ ...frame, matches: [0, 1].flatMap(logo => [0, 1].map(reference => ({ logo_index: logo, reference_index: reference, matches: 12, inliers: 9, keypoints: [100, 105], inlier_ratio: 0.75, descriptor_similarity: 0.93, image_cosine_similarity: logo !== reference ? 0.86 : 0.45, decision: 'candidate_match' }))) })) } }] };
      job.stages[0].elapsed_seconds = 1.25;
      job.stages[1].elapsed_seconds = 5;
      job.stages[1].model_timings = [{ model_id: 'google/owlv2-base-patch16', load_seconds: 2, inference_seconds: 3, calls: 2 }];
      job.stages[2].elapsed_seconds = 11;
      job.stages[2].model_timings = [
        { model_id: 'vismatch/xfeat', load_seconds: 2, inference_seconds: 3, calls: 2 },
        { model_id: 'google/siglip2-base-patch16-224', load_seconds: 2, inference_seconds: 4, calls: 2 },
      ];
      job.stages[0].started_at = 100;
      job.stages[1].started_at = 101.25;
      // Response order is not completion order; extraction finished after frames.
      job.stages = [job.stages[1], job.stages[0], job.stages[2]];
      return json(job);
    }
    if (url.pathname.includes('/previews/')) { previews++; return json({ mime: 'image/jpeg', data: jpeg }); }
    if (url.pathname.endsWith('/cancel') && request.method() === 'POST') {
      cancellations++; job = { ...job, status: 'cancelled', stages: job.stages.map(stage => stage.status === 'running' ? { ...stage, status: 'cancelled' } : stage) }; return json(job);
    }
    if (job && url.pathname.endsWith(`/runs/${job.id}`)) return json(job);
    unexpected.push(`${request.method()} ${url.pathname}`); return route.abort();
  });
  await page.goto(`${origin}/modeltest`);
  await page.getByRole('button', { name: '다크', exact: true }).click();
  assert.equal(await page.locator('.mt-shell').getAttribute('data-theme'), 'dark');
  assert.equal(await page.locator('.mt-shell').evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(16, 27, 41)');
  await page.getByRole('button', { name: '영상 검수', exact: true }).click();
  const monitor = page.getByRole('complementary', { name: '실행 모니터', exact: true });
  assert.equal(await monitor.getByRole('link', { name: '결과 보기', exact: true }).count(), 0);
  assert.equal(await monitor.evaluate(node => getComputedStyle(node).position), 'sticky');
  await page.locator('.mt-run-layout').scrollIntoViewIfNeeded();
  await page.evaluate(() => { const top = document.querySelector('.mt-run-layout').getBoundingClientRect().top + window.scrollY; window.scrollTo({ top: top + 150, behavior: 'instant' }); });
  assert.ok(Math.abs((await monitor.boundingBox()).y - 22) < 2, 'Monitor remains visible while scrolling through settings');
  await page.setViewportSize({ width: 1100, height: 1100 });
  assert.equal(await monitor.evaluate(node => getComputedStyle(node).position), 'static');
  assert.ok((await monitor.boundingBox()).y >= (await page.locator('.mt-materials').boundingBox()).y + (await page.locator('.mt-materials').boundingBox()).height);
  await page.setViewportSize({ width: 1440, height: 1100 });
  jpeg = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = 80; canvas.height = 40;
    const ctx = canvas.getContext('2d'); ctx.fillStyle = '#1264ad'; ctx.fillRect(0, 0, 80, 40);
    return canvas.toDataURL('image/jpeg').split(',')[1];
  });
  await page.locator('.mt-conditions summary').click();
  await page.getByLabel('추가 검수 조건', { exact: true }).fill('temporary condition');
  await page.getByLabel('추가 검수 조건', { exact: true }).fill('');
  assert.equal(await page.locator('.mt-conditions').getAttribute('open'), '');
  await page.locator('input[type=file][aria-label="검수 영상"]').setInputFiles({ name: 'fixture.mp4', mimeType: 'video/mp4', buffer: Buffer.from('test-video') });
  await page.locator('input[type=file][aria-label="발주서·계약서"]').setInputFiles({ name: 'contract.txt', mimeType: 'text/plain', buffer: Buffer.from('fixture contract') });
  await page.locator('input[type=file][aria-label="원본 로고·레퍼런스"]').setInputFiles(['logo.jpg', 'best-logo.jpg'].map(name => ({ name, mimeType: 'image/jpeg', buffer: Buffer.from(jpeg, 'base64') })));
  await page.getByRole('checkbox', { name: '문구 추출', exact: true }).uncheck();
  await page.getByRole('checkbox', { name: '시각 요소 추출', exact: true }).uncheck();
  await page.getByLabel('로고 추출 등록 모델 선택', { exact: true }).selectOption('google/owlv2-base-patch16');
  assert.equal(await page.getByLabel('로고 추출 모델', { exact: true }).inputValue(), 'google/owlv2-base-patch16');
  await page.getByLabel('로고 추출 모델', { exact: true }).fill('https://huggingface.co/google/owlv2-base-patch16');
  await page.getByRole('checkbox', { name: '원본 로고와 대조', exact: true }).check();
  await page.getByLabel('원본 로고와 대조 등록 모델 선택', { exact: true }).selectOption('vismatch/xfeat');
  await page.getByRole('checkbox', { name: 'SigLIP2 이미지 코사인 유사도도 계산' }).check();
  await page.getByText('첨부·모델 설정 저장 완료', { exact: false }).waitFor();
  await page.reload();
  await page.getByText('fixture.mp4', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '다크', exact: true }).getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('.mt-conditions').getAttribute('open'), '');
  assert.equal(await page.getByLabel('로고 추출 모델', { exact: true }).inputValue(), 'https://huggingface.co/google/owlv2-base-patch16');
  const storedBytes = await page.evaluate(async () => {
    const { openDraftDB, readDraft } = await import('/src/pages/modeltest/modeltestDraft.js');
    return (await readDraft(await openDraftDB()))['video.media'].text();
  });
  assert.equal(storedBytes, 'test-video');
  await page.getByLabel('RunPod Connect의 SSH 명령', { exact: true }).fill('ssh root@203.0.113.10');
  await page.getByRole('button', { name: '연결 확인', exact: true }).click();
  await page.getByText('Pod 연결됨', { exact: true }).waitFor();
  await page.reload();
  await page.getByText('Pod 연결됨', { exact: true }).waitFor();
  assert.equal(await page.locator('.mt-connection-wrap').getAttribute('open'), null);
  await page.locator('.mt-connection-wrap>summary').click();
  await page.getByText('이전 로컬 세션을 복원했습니다.', { exact: false }).waitFor();
  assert.equal(await page.getByLabel('RunPod Connect의 SSH 명령', { exact: true }).inputValue(), 'ssh root@203.0.113.10');
  await page.locator('.mt-connection-wrap>summary').click();
  assert.equal(await page.locator('.mt-connection-wrap').getAttribute('open'), null);
  await page.getByRole('button', { name: '검수 실행', exact: true }).click();
  await page.getByRole('button', { name: '검수 강제 중단', exact: true }).waitFor();
  await page.waitForFunction(() => document.activeElement?.id === 'mt-results-title');
  assert.equal(await page.evaluate(() => window.resultScrolls), 1);
  assert.ok((await page.locator('#mt-results-title').boundingBox()).y >= 0 && (await page.locator('#mt-results-title').boundingBox()).y < 1050, 'Accepted results are in the viewport');
  assert.equal(await monitor.getByText('실행 위치', { exact: true }).count(), 0);
  assert.equal(await monitor.getByRole('heading', { name: '단계별 상태', exact: true }).count(), 0);
  assert.equal(await monitor.getByRole('button', { name: '실패 단계 0개 · 첫 실패 단계 결과 보기', exact: true }).count(), 0);
  await page.getByRole('button', { name: '다크', exact: true }).focus();
  await page.waitForResponse(response => response.request().method() === 'GET' && response.url().endsWith(`/runs/${job.id}`));
  assert.equal(await page.evaluate(() => window.resultScrolls), 1, 'Polling must not scroll again');
  assert.equal(await page.getByRole('button', { name: '다크', exact: true }).evaluate(node => node === document.activeElement), true);
  await monitor.getByRole('link', { name: '결과 보기', exact: true }).click();
  assert.equal(await page.locator('#mt-results-title').evaluate(node => node === document.activeElement), true);
  await monitor.getByRole('button', { name: '완료 단계 2개 · 마지막 완료 단계 결과 보기', exact: true }).click();
  assert.equal(await page.getByRole('navigation', { name: '결과 단계 선택' }).getByRole('button', { name: '로고 추출', exact: true }).getAttribute('aria-pressed'), 'true');
  await monitor.getByRole('button', { name: '원본 로고와 대조 실행 중 · 결과 보기', exact: true }).click();
  assert.equal(await page.getByRole('navigation', { name: '결과 단계 선택' }).getByRole('button', { name: '원본 로고와 대조', exact: true }).getAttribute('aria-pressed'), 'true');
  await page.reload();
  await page.getByRole('button', { name: '검수 강제 중단', exact: true }).waitFor();
  assert.equal(await page.evaluate(() => window.resultScrolls), 0, 'Restoring a run must not auto-scroll');
  assert.notEqual(await page.evaluate(() => document.activeElement?.id), 'mt-results-title');
  assert.equal(submissions, 1, 'Reload must query, never resubmit');
  assert.equal(await page.locator('#mt-lock-reason').textContent(), '검수가 실행 중이라 설정을 바꿀 수 없습니다.');
  for (const name of ['크리에이티브체커 홈', '서비스 홈']) {
    const home = page.getByRole('button', { name, exact: true });
    assert.equal(await home.isDisabled(), true); assert.equal(await home.getAttribute('title'), '검수가 실행 중이라 설정을 바꿀 수 없습니다.');
    assert.equal(await home.getAttribute('aria-describedby'), 'mt-lock-reason');
  }
  assert.equal(await page.getByLabel('RunPod Connect의 SSH 명령', { exact: true }).inputValue(), 'ssh root@203.0.113.10');
  await page.getByRole('navigation', { name: '결과 단계 선택' }).getByRole('button', { name: '원본 로고와 대조' }).click();
  await page.getByRole('navigation', { name: '분석 프레임 선택' }).getByRole('button', { name: '30 프레임 · 1.25초', exact: true }).click();
  await page.getByRole('img', { name: '추출 로고 1', exact: true }).waitFor();
  await page.getByRole('img', { name: '원본 로고 best-logo.jpg', exact: true }).waitFor();
  assert.equal(await page.getByLabel('원본 로고', { exact: true }).inputValue(), '1');
  assert.equal(await page.getByRole('button', { name: '로고 1 위치 보기', exact: true }).evaluate(node => node.style.left), '10%');
  for (const text of ['0.860', '0.930', '75.0%', '12 / 9개', '일치 후보']) await page.getByText(text, { exact: true }).waitFor();
  await page.getByLabel('원본 로고', { exact: true }).selectOption('0');
  await page.getByRole('img', { name: '원본 로고 logo.jpg', exact: true }).waitFor();
  await page.getByRole('button', { name: '로고 2', exact: true }).click();
  assert.equal(await page.getByLabel('원본 로고', { exact: true }).inputValue(), '0', 'Each crop gets its own best reference');
  await page.getByLabel('원본 로고', { exact: true }).selectOption('1');
  await page.getByRole('button', { name: '로고 1', exact: true }).click();
  assert.equal(await page.getByLabel('원본 로고', { exact: true }).inputValue(), '0', 'Keep the manual choice for the first crop');
  await page.getByRole('navigation', { name: '분석 프레임 선택' }).getByRole('button', { name: '0 프레임 · 0.00초', exact: true }).click();
  assert.equal(await page.getByLabel('원본 로고', { exact: true }).inputValue(), '1', 'A new frame starts with its best reference');
  await page.getByRole('navigation', { name: '분석 프레임 선택' }).getByRole('button', { name: '30 프레임 · 1.25초', exact: true }).click();
  assert.equal(await page.getByLabel('원본 로고', { exact: true }).inputValue(), '0');
  await page.getByRole('img', { name: '30 프레임 · 1.25초 분석 이미지', exact: true }).waitFor();
  const loadedPreviews = previews;
  for (const name of ['소요 시간', '디버그']) {
    await page.getByRole('button', { name, exact: true }).click();
    if (name === '소요 시간') {
      const table = page.getByRole('table', { name: '모델별 누적 소요 시간' });
      await table.getByRole('rowheader', { name: 'google/siglip2-base-patch16-224', exact: true }).waitFor();
      assert.match(await table.getByRole('row').filter({ hasText: 'google/siglip2-base-patch16-224' }).innerText(), /2\.0초\s+4\.0초\s+6\.0초\s+2회/);
      await page.getByRole('table', { name: '단계별 소요 시간' }).getByText('1.3초', { exact: true }).waitFor();
    } else {
      const debug = page.locator('.mt-debug');
      assert.equal(await debug.getByRole('heading', { name: '실행 설정', exact: true }).count(), 1);
      assert.equal(await debug.getByRole('heading', { name: '전체 응답', exact: true }).count(), 1);
      assert.deepEqual(JSON.parse(await debug.locator('pre').nth(0).textContent()), submittedManifest);
      assert.deepEqual(JSON.parse(await debug.locator('pre').nth(1).textContent()), job);
      assert.equal(await debug.getByText(`요청 ID: ${job.id}`, { exact: true }).count(), 1);
    }
    await page.getByRole('button', { name: '단계별 결과', exact: true }).click();
    await page.getByRole('heading', { name: '30 프레임 · 1.25초', exact: true }).waitFor();
    await page.getByRole('img', { name: '추출 로고 1', exact: true }).waitFor();
    assert.equal(await page.getByLabel('원본 로고', { exact: true }).inputValue(), '0', 'Tab changes must preserve the manual reference');
  }
  assert.equal(previews, loadedPreviews, 'Changing result tabs must reuse previews and keep the selected frame');
  await page.getByRole('navigation', { name: '결과 단계 선택' }).getByRole('button', { name: '로고 추출', exact: true }).click();
  await page.getByRole('navigation', { name: '결과 단계 선택' }).getByRole('button', { name: '원본 로고와 대조', exact: true }).click();
  await page.getByRole('heading', { name: '30 프레임 · 1.25초', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '검수 강제 중단', exact: true }).evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(185, 28, 28)');
  const stopButton = page.getByRole('button', { name: '검수 강제 중단', exact: true });
  const confirmation = page.locator('.mt-confirm-dialog');
  await stopButton.click(); await confirmation.waitFor();
  assert.equal(cancellations, 0);
  await page.keyboard.press('Escape'); await confirmation.waitFor({ state: 'hidden' });
  assert.equal(await stopButton.evaluate(node => node === document.activeElement), true);
  assert.equal(cancellations, 0);
  await stopButton.click(); await confirmation.getByRole('heading', { name: '검수 강제 중단', exact: true }).waitFor(); await confirmation.getByRole('button', { name: '중단', exact: true }).click();
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].some(button => button.textContent === '검수 강제 중단'));
  assert.equal(cancellations, 1); assert.equal(submissions, 1);
  job = { ...job, status: 'failed', diagnostics: { returncode: -11, signal: 'SIGSEGV', log_tail: 'Fatal Python error: fixture <script>unsafe()</script>' }, stages: job.stages.map(stage => stage.id === 'logo_compare' ? { ...stage, status: 'failed' } : stage) };
  await page.reload();
  await monitor.getByRole('button', { name: '실패 단계 1개 · 첫 실패 단계 결과 보기', exact: true }).click();
  assert.equal(await page.getByRole('navigation', { name: '결과 단계 선택' }).getByRole('button', { name: '원본 로고와 대조', exact: true }).getAttribute('aria-pressed'), 'true');
  const diagnosis = page.locator('summary').filter({ hasText: '종료 진단 · 코드 -11 · SIGSEGV' });
  await diagnosis.focus(); await page.keyboard.press('Enter');
  assert.equal(await page.getByLabel('Pod 실행 오류 로그', { exact: true }).textContent(), job.diagnostics.log_tail);
  assert.equal(await page.getByLabel('Pod 실행 오류 로그', { exact: true }).locator('script').count(), 0);
  await page.getByRole('button', { name: '라이트', exact: true }).click();
  assert.equal(await page.locator('.mt-shell').evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(244, 247, 250)');
  await page.getByRole('button', { name: '저장된 첨부·설정 지우기', exact: true }).click();
  await confirmation.getByRole('button', { name: '취소', exact: true }).click();
  assert.ok(await page.locator('.mt-file-list').count() > 0);
  await page.getByRole('button', { name: '저장된 첨부·설정 지우기', exact: true }).click();
  await confirmation.getByRole('button', { name: '지우기', exact: true }).click();
  await page.getByText('첨부·모델 설정 저장 완료', { exact: false }).waitFor();
  await page.reload();
  await monitor.getByRole('button', { name: '실패 단계 1개 · 첫 실패 단계 결과 보기', exact: true }).waitFor();
  await page.locator('#mt-lock-reason').waitFor({ state: 'detached' });
  await page.getByRole('button', { name: '영상 검수', exact: true }).click();
  assert.equal(await page.locator('.mt-file-list').count(), 0);
  await page.locator('input[type=file][aria-label="검수 영상"]').setInputFiles({ name: 'receipt.mp4', mimeType: 'video/mp4', buffer: Buffer.from('test-video') });
  for (const toggle of await page.locator('.mt-stage-heading input[type=checkbox]').all()) await toggle.uncheck();
  await page.getByRole('checkbox', { name: '로고 추출', exact: true }).check();
  await page.getByLabel('로고 추출 등록 모델 선택', { exact: true }).selectOption('google/owlv2-base-patch16');
  let releaseSubmission;
  const pendingSubmission = new Promise(resolve => { releaseSubmission = resolve; });
  t.after(() => releaseSubmission());
  await page.route('**/runs', async route => { await pendingSubmission; await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'fixture uncertain submission' }) }); });
  await page.getByRole('button', { name: '검수 실행', exact: true }).click();
  await page.locator('#mt-lock-reason').filter({ hasText: '자료를 전송하고 작업을 접수하는 중이라 편집할 수 없습니다.' }).waitFor();
  releaseSubmission();
  await page.locator('#mt-lock-reason').filter({ hasText: '작업 접수 여부를 확인해야 설정을 바꿀 수 있습니다.' }).waitFor();
  const receiptExit = page.getByRole('button', { name: '접수 확인 종료', exact: true });
  await receiptExit.click(); await confirmation.waitFor();
  await page.keyboard.press('Escape'); await confirmation.waitFor({ state: 'hidden' });
  assert.equal(await receiptExit.evaluate(node => node === document.activeElement), true);
  assert.equal(await page.locator('#mt-lock-reason').textContent(), '작업 접수 여부를 확인해야 설정을 바꿀 수 있습니다.');
  await receiptExit.click(); assert.equal(await confirmation.getByRole('button', { name: '종료', exact: true }).evaluate(node => node.classList.contains('primary')), true); await confirmation.getByRole('button', { name: '종료', exact: true }).click();
  await page.locator('#mt-lock-reason').waitFor({ state: 'detached' });
  assert.equal(cancellations, 1, 'Ending receipt checks does not cancel Pod work');
  assert.equal(await page.getByRole('button', { name: '크리에이티브체커 홈', exact: true }).isDisabled(), false);
  assert.equal(await page.getByRole('button', { name: '서비스 홈', exact: true }).isDisabled(), false);

  assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
});

test('workflow and agent boards edit connections, persist settings and submit dynamic OCR stages', { timeout: 90000 }, async t => {
  const server = await createServer({ configFile: false, envFile: false, root: process.cwd(), cacheDir: '../work/modeltest-vite-cache',
    define: { 'import.meta.env.VITE_API_PROFILE': JSON.stringify('legacy') }, server: { host: '127.0.0.1', port: 5187, strictPort: true, hmr: false } });
  await server.listen(); t.after(() => server.close());
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'msedge' }); t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } }); page.setDefaultTimeout(8000);
  const errors = []; const unexpected = []; const previews = []; let manifest; let jpeg; let submissions = 0;
  page.on('pageerror', error => errors.push(error.message)); page.on('dialog', dialog => { if (dialog.type() === 'beforeunload') return dialog.accept(); errors.push(`Unexpected native ${dialog.type()}`); return dialog.dismiss(); });
  const health = { contract: CONTRACT, ready: true, candidate_filter_scopes: Object.keys(FILTER_SCOPES), prompt_overrides: 1, workflow: { version: 1, max_parallel: 2, agents: true }, stages: STAGES.map(stage => stage.id), formats: FILE_TYPES,
    limits: { ...Object.fromEntries(Object.entries(FILE_LIMITS).map(([key, value]) => [`${key}_bytes`, value])), total_bytes: 600 * 1024 ** 2 } };
  await page.route('**/*', async route => {
    const request = route.request(); const url = new URL(request.url());
    if (url.origin !== origin) { unexpected.push(url.origin); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const json = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    if (url.pathname === '/api/v1/auth/me') return json({ authenticated: false });
    if (url.pathname === '/api/modeltest/ssh/connections') return json({ ...health, connection_id: 'agent-fixture' });
    if (url.pathname === '/api/modeltest/ssh/models') return json({ models: [], families: {} });
    if (url.pathname.includes('/previews/')) {
      previews.push(url.pathname);
      return json({ mime: 'image/jpeg', data: jpeg });
    }
    if (url.pathname.endsWith('/runs') && request.method() === 'POST') {
      submissions++;
      manifest = JSON.parse(request.postDataBuffer().toString().match(/name="config"\r\n\r\n([^\r]+)/)[1]);
      const frame = { index: 0, timestamp_seconds: 0, width: 80, height: 40, preview: 'frame-00000000.jpg' };
      const outputs = stage => {
        const row = { ...frame };
        if (stage.kind === 'logo_extract') row.logos = [{ logo_index: 0, bbox: [0, 0, 20, 20], preview: `logo-${stage.id}-0-0.png`, score: 0.8 }];
        else if (['logo_similarity', 'candidate_filter', 'logo_compare'].includes(stage.kind)) {
          row.matches = [{ logo_index: 0, reference_index: 0, image_cosine_similarity: 0.9, ...(stage.kind === 'logo_compare' ? { matches: 12, inliers: 9, decision: 'candidate_match' } : {}) }];
          if (stage.kind !== 'logo_similarity') Object.assign(row, { candidate_count: 2, selected_count: 1,
            filter: { scope: manifest.stages.find(item => item.kind === 'candidate_filter').scope, threshold: .85, top_k: 1, total_candidates: 2, total_selected: 1 },
            excluded_matches: [{ logo_index: 1, reference_index: 0, image_cosine_similarity: .3, candidate_rank: 2, exclusion_reason: 'below_threshold' }] });
        } else row.text = 'fixture output';
        return { items: [row] };
      };
      return json({ contract: CONTRACT, id: request.headers()['idempotency-key'], status: 'completed', agents: manifest.workflow.agents,
        references: [{ index: 0, preview: 'preview-reference-0.jpg' }],
        stages: [{ id: 'frames', status: 'completed', output: { frames: [frame] } }, ...manifest.stages.map(stage => ({ ...stage, status: 'completed', elapsed_seconds: 1, output: outputs(stage) }))] });
    }
    unexpected.push(`${request.method()} ${url.pathname}`); return route.abort();
  });
  await page.goto(`${origin}/modeltest`);
  jpeg = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = 80; canvas.height = 40;
    canvas.getContext('2d').fillRect(0, 0, 80, 40); return canvas.toDataURL('image/jpeg').split(',')[1];
  });
  await page.getByRole('button', { name: '영상 검수', exact: true }).click();
  await page.getByRole('button', { name: '그래프', exact: true }).click();
  assert.equal(await page.locator('.mt-pipeline').count(), 0);
  assert.equal(await page.locator('.mt-result-guidance').count(), 1);
  assert.equal(await page.locator('.mt-page-help>summary').textContent(), '페이지 도움말');
  assert.equal(await page.locator('.mt-help-popover:popover-open').count(), 0);
  assert.equal(await page.getByText('단계를 선택해 설정하고 연결해 실행 순서와 분기를 설정합니다.', { exact: true }).isVisible(), false);
  await page.getByLabel('워크플로우 동시 실행 수', { exact: true }).selectOption('2');
  assert.equal(await page.getByText('두 모델을 함께 올릴 GPU 메모리가 필요하며, 부족하면 실행이 실패할 수 있습니다.', { exact: false }).isVisible(), true);
  await page.getByLabel('워크플로우 동시 실행 수', { exact: true }).selectOption('1');
  await page.getByRole('button', { name: '워크플로우 도움말', exact: true }).focus();
  await page.keyboard.press('Space');
  await page.getByRole('dialog', { name: '워크플로우 도움말', exact: true }).waitFor();
  await page.keyboard.press('Escape');
  const settings = page.getByRole('complementary', { name: '모델 단계 설정', exact: true });
  await page.getByRole('button', { name: '로고 추출 노드 이동', exact: true }).click();
  assert.equal(await settings.locator('#mt-stage-logo_extract').count(), 1);
  assert.equal(await page.locator('#mt-stage-logo_extract').count(), 1, 'Only one editor for a stage');
  const fixedPrompt = page.locator('#mt-stage-logo_extract .mt-prompt');
  await page.getByLabel('로고 추출 등록 모델 선택', { exact: true }).selectOption('google/owlv2-base-patch16-ensemble');
  assert.equal(await fixedPrompt.getAttribute('open'), null); await fixedPrompt.locator('summary').click();
  assert.equal(await page.getByLabel('로고 추출 프롬프트', { exact: true }).inputValue(), 'a brand logo');
  await page.getByLabel('로고 추출 프롬프트', { exact: true }).fill('sponsor logo');
  await fixedPrompt.locator('summary').click(); await page.getByText('첨부·모델 설정 저장 완료', { exact: false }).waitFor(); await page.reload();
  await page.locator('[data-board="video-models"]').waitFor();
  assert.equal(await page.getByRole('button', { name: '그래프', exact: true }).getAttribute('aria-pressed'), 'true');
  await page.getByRole('button', { name: '로고 추출 노드 이동', exact: true }).press('Enter');
  assert.equal(await fixedPrompt.getAttribute('open'), null); await fixedPrompt.locator('summary').click();
  assert.equal(await page.getByLabel('로고 추출 프롬프트', { exact: true }).inputValue(), 'sponsor logo');
  await page.locator('.mt-workflow-editor').screenshot({ path: '../work/modeltest-graph-inspector.png' });
  assert.equal(await page.evaluate(() => {
    const selectors = ['.mt-connection-wrap', '.mt-workspace-bar', '.mt-editor-mode', '.mt-workflow-editor', '.mt-run-layout', '.mt-results'];
    return selectors.every((selector, i) => !i || (document.querySelector(selectors[i - 1]).compareDocumentPosition(document.querySelector(selector)) & Node.DOCUMENT_POSITION_FOLLOWING));
  }), true, 'Connection, mode, board/settings, materials/monitor and results follow the reading order');
  await fixedPrompt.getByRole('button', { name: '기본값 복원', exact: true }).click();
  assert.equal(await page.getByLabel('로고 추출 프롬프트', { exact: true }).inputValue(), 'a brand logo');
  await page.getByLabel('로고 추출 프롬프트', { exact: true }).fill('logo\nlogo');
  assert.equal(await page.getByLabel('로고 추출 프롬프트', { exact: true }).isVisible(), true);
  await fixedPrompt.getByRole('button', { name: '기본값 복원', exact: true }).click();
  await page.getByRole('button', { name: 'SigLIP2 → 필터 → XFeat', exact: true }).click();
  await page.locator('[data-board="video-models"]').getByRole('button', { name: '전체 보기', exact: true }).click();
  await page.locator('[data-node="candidate_filter"]').getByRole('button', { name: '설정', exact: true }).click();
  await settings.getByLabel('필터 최소 코사인 유사도').fill('0.7');
  await page.getByRole('button', { name: '순차', exact: true }).click();
  assert.equal(await page.locator('.mt-model-inspector').count(), 0);
  await page.getByRole('region', { name: '검수 단계 설정 목록', exact: true }).waitFor();
  await page.getByRole('button', { name: '그래프', exact: true }).click();
  await page.locator('[data-node="candidate_filter"]').getByRole('button', { name: '설정', exact: true }).click();
  assert.equal(await settings.getByLabel('필터 최소 코사인 유사도').inputValue(), '0.7');
  await settings.getByLabel('필터 선정 기준').selectOption('crops_per_reference_frame');
  await page.locator('[data-node="logo_similarity"]').getByRole('button', { name: '설정', exact: true }).click();
  await settings.getByLabel('로고 이미지 유사도 등록 모델 선택', { exact: true }).selectOption('google/siglip2-so400m-patch14-384');
  await page.locator('[data-node="candidate_filter"]').getByRole('button', { name: '설정', exact: true }).click();
  assert.equal(await settings.getByLabel('필터 최소 코사인 유사도').inputValue(), '');
  assert.equal(await settings.getByLabel('필터 선정 기준').inputValue(), 'crops_per_reference_frame');
  await page.getByRole('button', { name: '프레임 이미지 추출 노드 이동', exact: true }).press('Enter');
  assert.equal(await settings.getByLabel('기준', { exact: true }).isVisible(), true);
  const sampling = page.locator('#mt-stage-frames');
  assert.equal(await page.locator('.mt-materials #mt-stage-frames').count(), 0);
  assert.equal(await settings.locator('.mt-sampling-fields').count(), 1, 'Frame settings have only one editor');
  await sampling.getByLabel('기준', { exact: true }).selectOption('seconds');
  assert.equal(await sampling.getByLabel('간격', { exact: true }).inputValue(), '1');
  await sampling.getByLabel('간격', { exact: true }).fill('0.5');
  await sampling.getByLabel('기준', { exact: true }).selectOption('frames');
  assert.equal(await sampling.getByLabel('간격', { exact: true }).inputValue(), '30');
  await sampling.getByLabel('간격', { exact: true }).fill('12');
  await sampling.getByLabel('최대 추출 수', { exact: true }).fill('80');
  assert.equal(await page.locator('#mt-stage-frames').count(), 1);
  const node = page.getByRole('button', { name: '로고 추출 노드 이동', exact: true });
  const before = await node.locator('..').evaluate(element => element.style.left);
  await node.focus(); await node.press('ArrowRight');
  assert.notEqual(await node.locator('..').evaluate(element => element.style.left), before);
  const box = await node.boundingBox(); await page.mouse.move(box.x + 30, box.y + 15); await page.mouse.down(); await page.mouse.move(box.x + 70, box.y + 45); await page.mouse.up();
  await page.getByRole('button', { name: '로고 추출에서 로고 이미지 유사도 연결 삭제', exact: true }).click();
  await page.getByRole('button', { name: '로고 추출 출력 연결', exact: true }).click();
  await page.getByRole('button', { name: '로고 이미지 유사도 입력 연결', exact: true }).click();
  await page.getByRole('button', { name: '로고 추출에서 로고 이미지 유사도 연결 삭제', exact: true }).waitFor();
  await page.getByRole('button', { name: '에이전트', exact: true }).click();
  assert.equal(await page.locator('.mt-sampling-fields').count(), 1);
  assert.equal(await sampling.getByLabel('에이전트 프레임 추출 간격', { exact: true }).inputValue(), '12');
  assert.equal(await sampling.getByLabel('에이전트 최대 추출 수', { exact: true }).inputValue(), '80');
  await page.getByRole('button', { name: '에이전트 추가', exact: true }).click();
  await page.getByLabel('에이전트 이름', { exact: true }).fill('임시 에이전트');
  await page.getByRole('button', { name: '내부 단계 추가', exact: true }).click();
  await page.locator('.mt-agent-settings .mt-prompt>summary').click();
  await page.getByLabel('내부 단계 프롬프트', { exact: true }).fill('근거를 요약하세요.');
  await page.getByRole('button', { name: '에이전트 삭제', exact: true }).click();
  await page.getByRole('dialog', { name: '에이전트 삭제', exact: true }).getByRole('button', { name: '삭제', exact: true }).click();
  const agents = page.locator('[data-board="video-agents"]');
  await agents.getByRole('button', { name: 'A04 문구 검수 노드 이동', exact: true }).press('Enter');
  await page.getByLabel('에이전트 이름', { exact: true }).fill('A04 OCR 비교');
  await page.getByLabel('내부 단계 이름', { exact: true }).fill('브랜드 문구 OCR');
  await page.getByLabel('내부 단계 등록 모델', { exact: true }).selectOption('PaddlePaddle/PaddleOCR-VL-1.6-GGUF');
  await page.locator('.mt-agent-settings .mt-prompt>summary').click();
  assert.equal(await page.getByLabel('내부 단계 프롬프트', { exact: true }).inputValue(), 'OCR:');
  assert.equal(await page.locator('.mt-agent-settings>.mt-hint').getByText('Pod에 git·C++ 컴파일러·nvcc가 있어야 합니다.', { exact: false }).isVisible(), true);
  assert.equal(await page.getByText('기본 OCR: 지시를 변경하면 인식 품질·출력 형식이 달라질 수 있습니다. 문서 레이아웃 분석 기능이 추가되는 것은 아닙니다.', { exact: true }).isVisible(), true);
  assert.equal(await page.getByText('수정한 내용은 기본 프롬프트를 대체합니다.', { exact: false }).isVisible(), false);
  await page.getByLabel('내부 단계 프롬프트', { exact: true }).fill('OCR: Read the headline only.');
  await page.getByRole('button', { name: '내부 단계 추가', exact: true }).click();
  await page.getByRole('button', { name: '내부 단계 삭제', exact: true }).click();
  await agents.getByRole('button', { name: 'A05 시각 요소 검수 노드 이동', exact: true }).press('Enter');
  await page.locator('.mt-agent-detail .mt-board').getByRole('button', { name: '후보 필터 노드 이동', exact: true }).press('Enter');
  await page.getByLabel('내부 필터 최소 유사도', { exact: true }).fill('0.85');
  await page.locator('.mt-agent-detail .mt-board').getByRole('button', { name: '로고 이미지 유사도 노드 이동', exact: true }).press('Enter');
  await page.getByLabel('내부 단계 등록 모델', { exact: true }).selectOption('google/siglip2-so400m-patch14-384');
  await page.locator('.mt-agent-detail .mt-board').getByRole('button', { name: '로고 추출 노드 이동', exact: true }).press('Enter');
  await page.getByLabel('내부 단계 등록 모델', { exact: true }).selectOption('google/owlv2-base-patch16-ensemble');
  await page.locator('.mt-agent-detail .mt-board').getByRole('button', { name: '후보 필터 노드 이동', exact: true }).press('Enter');
  assert.equal(await page.getByLabel('내부 필터 최소 유사도', { exact: true }).inputValue(), '');
  await page.getByLabel('내부 필터 최소 유사도', { exact: true }).fill('0.85');
  await page.getByLabel('내부 필터 선정 기준', { exact: true }).selectOption('crops_per_reference_video');
  await page.getByLabel('내부 필터 상위 후보 수', { exact: true }).fill('1');
  await page.getByLabel('에이전트 보드 동시 실행 수', { exact: true }).selectOption('2');
  await page.locator('input[type=file][aria-label="검수 영상"]').setInputFiles({ name: 'agent.mp4', mimeType: 'video/mp4', buffer: Buffer.from('fixture') });
  await page.locator('input[type=file][aria-label="발주서·계약서"]').setInputFiles({ name: 'contract.txt', mimeType: 'text/plain', buffer: Buffer.from('fixture') });
  await page.locator('input[type=file][aria-label="원본 로고·레퍼런스"]').setInputFiles({ name: 'reference.png', mimeType: 'image/png', buffer: Buffer.from('fixture') });
  await page.getByText('첨부·모델 설정 저장 완료', { exact: false }).waitFor(); await page.reload();
  await agents.getByRole('button', { name: 'A04 OCR 비교 노드 이동', exact: true }).press('Enter');
  assert.equal(await sampling.getByLabel('에이전트 프레임 추출 간격', { exact: true }).inputValue(), '12');
  assert.equal(await sampling.getByLabel('에이전트 최대 추출 수', { exact: true }).inputValue(), '80');
  assert.equal(await page.getByLabel('내부 단계 모델', { exact: true }).inputValue(), 'PaddlePaddle/PaddleOCR-VL-1.6-GGUF');
  assert.equal(await page.getByLabel('내부 단계 이름', { exact: true }).inputValue(), '브랜드 문구 OCR');
  await page.locator('.mt-agent-settings .mt-prompt>summary').click();
  assert.equal(await page.getByLabel('내부 단계 프롬프트', { exact: true }).inputValue(), 'OCR: Read the headline only.');
  await page.getByLabel('RunPod Connect의 SSH 명령', { exact: true }).fill('ssh root@203.0.113.10');
  await page.getByRole('button', { name: '연결 확인', exact: true }).click(); await page.getByText('Pod 연결됨', { exact: true }).waitFor();
  assert.equal(await page.locator('.mt-readiness li').last().getByText('준비됨', { exact: true }).count(), 1);
  await page.locator('.mt-execution-form').evaluate(form => {
    window.renameSubmissions = 0;
    form.addEventListener('submit', () => { window.renameSubmissions++; });
  });
  for (const [board, name, label] of [
    [agents, 'A04 OCR 비교', '에이전트 이름'],
    [page.locator('.mt-agent-detail .mt-board'), '브랜드 문구 OCR', '내부 단계 이름'],
  ]) {
    await board.getByRole('button', { name: `${name} 노드 이동`, exact: true }).press('Shift+F10');
    await page.getByRole('menuitem', { name: '이름 변경', exact: true }).click();
    const input = page.getByLabel(label, { exact: true });
    assert.equal(await input.evaluate(node => node === document.activeElement), true);
    await input.fill(`${name} 수정`); await input.press('Enter');
    assert.equal(await page.evaluate(() => window.renameSubmissions), 0, `${label}: Enter cannot submit the review form`);
    assert.equal(submissions, 0);
    assert.equal(await input.inputValue(), `${name} 수정`);
    await input.fill(name);
  }
  await page.locator('.mt-agent-board').screenshot({ path: '../work/modeltest-agent-board.png' });
  await page.getByRole('button', { name: '검수 실행', exact: true }).click();
  await page.getByRole('navigation', { name: '결과 단계 선택' }).getByRole('button', { name: /브랜드 문구 OCR/ }).waitFor();
  assert.equal(submissions, 1, 'The explicit run button still submits once');
  assert.equal(manifest.workflow.agents.length, 6); assert.equal(manifest.workflow.max_parallel, 2);
  assert.deepEqual(manifest.sampling, { unit: 'frames', interval: 12, max_frames: 80 });
  const ocr = manifest.stages.find(stage => stage.name === '브랜드 문구 OCR');
  assert.equal(ocr.kind, 'text_extract'); assert.equal(ocr.model_id, 'PaddlePaddle/PaddleOCR-VL-1.6-GGUF');
  assert.equal(ocr.prompt_override, 'OCR: Read the headline only.');
  assert.equal(manifest.stages.find(stage => stage.kind === 'text_compare').inputs.text_extract, ocr.id);
  const filter = manifest.stages.find(stage => stage.kind === 'candidate_filter'); assert.equal(filter.threshold, 0.85); assert.equal(filter.top_k, 1);
  assert.equal(filter.scope, 'crops_per_reference_video');
  assert.equal(manifest.stages.find(stage => stage.kind === 'logo_extract').model_id, 'google/owlv2-base-patch16-ensemble');
  assert.equal(manifest.stages.find(stage => stage.kind === 'logo_similarity').model_id, 'google/siglip2-so400m-patch14-384');
  await page.getByRole('navigation', { name: '결과 단계 선택' }).getByRole('button', { name: /원본 로고와 대조/ }).click();
  await page.getByRole('img', { name: '추출 로고 1', exact: true }).waitFor();
  await page.getByText('0.900', { exact: true }).waitFor(); await page.getByText('12 / 9개', { exact: true }).waitFor();
  const audit = page.locator('.mt-candidate-audit'); await audit.locator('summary').click();
  assert.match(await audit.innerText(), /영상 전체 크롭 상위 N개/);
  await audit.getByRole('cell', { name: '제외 · 최소 유사도 미달', exact: true }).waitFor();
  assert.ok(previews.some(path => path.endsWith(`/logo-${manifest.stages.find(stage => stage.kind === 'logo_extract').id}-0-0.png`)));
  assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
});


test('connection summary collapses once, restores compactly and reopens on connection errors', { timeout: 90000 }, async t => {
  const server = await createServer({ configFile: false, envFile: false, root: process.cwd(), cacheDir: '../work/modeltest-vite-cache', define: { 'import.meta.env.VITE_API_PROFILE': JSON.stringify('legacy') }, server: { host: '127.0.0.1', port: 5187, strictPort: true, hmr: false } });
  await server.listen(); t.after(() => server.close()); const origin = 'http://127.0.0.1:5187';
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'msedge' }); t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } }); page.setDefaultTimeout(8000);
  const errors = []; const unexpected = [];
  const health = { contract: CONTRACT, ready: true, stages: STAGES.map(stage => stage.id), formats: FILE_TYPES, limits: { ...Object.fromEntries(Object.entries(FILE_LIMITS).map(([key, value]) => [`${key}_bytes`, value])), total_bytes: 600 * 1024 ** 2 } };
  let failConnect = false; let releaseConnect; let connectGate = Promise.resolve();
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => { if (dialog.type() === 'beforeunload') return dialog.accept(); errors.push(`Unexpected native ${dialog.type()}`); return dialog.dismiss(); });
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) { unexpected.push(url.origin); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const json = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    if (url.pathname === '/api/v1/auth/me') return json({ authenticated: false });
    if (url.pathname === '/api/modeltest/ssh/models') return json({ models: [], families: {} });
    if (url.pathname === '/api/modeltest/ssh/connections') {
      await connectGate;
      if (failConnect) return route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ detail: 'fixture connection failed' }) });
      return json({ ...health, connection_id: 'fixture' });
    }
    if (url.pathname.endsWith('/health')) return json(health);
    unexpected.push(url.pathname); return route.abort();
  });
  await page.goto(`${origin}/modeltest`);
  const connection = page.locator('.mt-connection-wrap'); const summary = connection.locator(':scope>summary');
  const help = page.getByRole('dialog', { name: 'RunPod 연결 도움말', exact: true });
  await page.getByRole('button', { name: 'RunPod 연결 도움말', exact: true }).click();
  assert.equal(await help.getByText('SSH over exposed TCP', { exact: false }).isVisible(), true);
  assert.equal(await help.getByText('SSH 명령과 연결 설정은 이 브라우저에 저장됩니다.', { exact: false }).isVisible(), true);
  await page.keyboard.press('Escape');
  await page.getByLabel('RunPod Connect의 SSH 명령', { exact: true }).fill('ssh -i "C:/private keys/key@private" root@203.0.113.10 -p 1234');
  await page.getByRole('button', { name: '연결 확인', exact: true }).click();
  await page.getByText('Pod 연결됨', { exact: true }).waitFor();
  assert.equal(await connection.getAttribute('open'), null);
  assert.equal(await summary.locator('.mt-connection-target').textContent(), 'SSH · root@203.0.113.10');
  assert.equal((await summary.textContent()).includes('private'), false);
  await summary.click();
  await page.getByRole('button', { name: '다크', exact: true }).click();
  assert.equal(await connection.getAttribute('open'), '', 'Manual expansion survives subsequent renders');
  await page.getByRole('button', { name: 'RunPod 연결 도움말', exact: true }).click();
  assert.equal(await help.getByText('SSH over exposed TCP', { exact: false }).count(), 0);
  assert.equal(await help.getByText('SSH 명령과 연결 설정은 이 브라우저에 저장됩니다.', { exact: false }).count(), 0);
  assert.equal(await help.getByText('환경 준비는 Pod의 전용 가상환경에 패키지를 설치합니다.', { exact: false }).isVisible(), true);
  await page.keyboard.press('Escape');
  await page.reload(); await page.getByText('Pod 연결됨', { exact: true }).waitFor();
  assert.equal(await connection.getAttribute('open'), null, 'A restored ready connection starts collapsed');
  assert.equal(await summary.locator('.mt-connection-target').textContent(), 'SSH · root@203.0.113.10');
  await summary.click();
  failConnect = true; connectGate = new Promise(resolve => { releaseConnect = resolve; });
  await page.getByRole('button', { name: '연결 확인', exact: true }).click();
  await summary.click(); assert.equal(await connection.getAttribute('open'), null);
  releaseConnect();
  await connection.getByRole('alert').filter({ hasText: 'fixture connection failed' }).waitFor();
  assert.equal(await connection.getAttribute('open'), '');
  assert.equal(await summary.getByText('연결 오류', { exact: true }).isVisible(), true);
  failConnect = false;
  await page.getByRole('button', { name: '연결 확인', exact: true }).click();
  await page.getByText('Pod 연결됨', { exact: true }).waitFor();
  assert.equal(await connection.getAttribute('open'), null);
  await summary.click(); await page.getByRole('button', { name: 'HTTP API', exact: true }).click();
  assert.equal(await connection.getAttribute('open'), '', 'Clearing the connection opens its settings');
  await page.getByLabel('Pod HTTP 서비스 주소', { exact: true }).fill(origin);
  await page.getByRole('button', { name: '연결 확인', exact: true }).click(); await page.getByText('Pod 연결됨', { exact: true }).waitFor();
  assert.equal(await connection.getAttribute('open'), null);
  assert.equal(await summary.locator('.mt-connection-target').textContent(), 'HTTP · 127.0.0.1:5187');
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await summary.evaluate(node => node.scrollWidth <= node.clientWidth), true, 'Compact summary fits the viewport');
  assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
});
