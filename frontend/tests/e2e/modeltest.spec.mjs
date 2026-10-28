import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chromium } from 'playwright';
import { createServer } from 'vite';
import { CONTRACT, FILE_LIMITS, FILE_TYPES, STAGES } from '../../src/pages/modeltest/modeltest.js';

test('HF registry persists, selects a compatible node, prepares separately and shows embedding results', { timeout: 90000 }, async t => {
  const server = await createServer({ configFile: false, envFile: false, root: process.cwd(), cacheDir: '../work/modeltest-vite-cache', define: { 'import.meta.env.VITE_API_PROFILE': JSON.stringify('legacy') }, server: { host: '127.0.0.1', port: 5187, strictPort: true, hmr: false } });
  await server.listen(); t.after(() => server.close()); const origin = 'http://127.0.0.1:5187';
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'msedge' }); t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } }); page.setDefaultTimeout(8000);
  const errors = []; const unexpected = []; const requests = []; let records = [];
  const profile = { model_id: 'google/siglip2-base-patch16-224', revision: 'main', resolved_revision: 'a'.repeat(40), name: 'SigLIP 등록', family: 'clip', status: 'unverified', tasks: ['image_embed', 'logo_similarity'], options: { normalize: true, pooling: 'projection' }, training: {}, reasons: [], fingerprint: 'space-one' };
  const families = { clip: { engine: 'transformers', defaults: profile.options, requirements: { transformers: '>=5.21,<6' } } };
  const health = { contract: CONTRACT, ready: true, registry_version: 1, workflow: { version: 1, agents: true, max_parallel: 2 }, stages: [...STAGES.map(stage => stage.id), 'model_prepare'], formats: FILE_TYPES, limits: { ...Object.fromEntries(Object.entries(FILE_LIMITS).map(([key, value]) => [`${key}_bytes`, value])), total_bytes: 600 * 1024 ** 2 } };
  page.on('pageerror', error => errors.push(error.message)); page.on('dialog', dialog => dialog.accept());
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
  await page.goto(`${origin}/modeltest`); await page.locator('.mt-library>summary').click();
  await page.getByLabel('추가할 HF 모델', { exact: true }).fill('https://huggingface.co/google/siglip2-base-patch16-224');
  await page.getByRole('button', { name: '메타데이터 확인', exact: true }).click();
  await page.getByRole('button', { name: '모델 등록·저장', exact: true }).click();
  await page.locator('.mt-library-list article').waitFor(); assert.equal(requests.length, 0);
  await page.reload(); await page.locator('.mt-library>summary').click();
  await page.locator('.mt-library-list').getByText('SigLIP 등록', { exact: true }).waitFor();
  await page.getByLabel('RunPod Connect의 SSH 명령', { exact: true }).fill('ssh root@203.0.113.10 -p 12345');
  await page.getByRole('button', { name: '연결 확인', exact: true }).click(); await page.getByText('Pod 연결됨', { exact: true }).waitFor();
  await page.getByRole('button', { name: '가중치·환경 준비', exact: true }).click();
  await page.getByText('가중치 준비 완료', { exact: true }).waitFor(); assert.equal(requests[0].files.media, null); assert.equal(requests[0].purpose, 'prepare');
  await page.getByRole('button', { name: '에이전트 보드', exact: true }).click();
  const tabs = page.locator('[aria-label="편집할 에이전트"]');
  for (const name of ['A02 문서·기준 분석', 'A04 문구 검수', 'A05 시각 요소 검수', 'A06 근거·결과 검증', 'A07 보고서 작성']) { await tabs.getByRole('button', { name, exact: true }).click(); await page.getByRole('checkbox', { name: '이 에이전트 실행', exact: true }).uncheck(); }
  await tabs.getByRole('button', { name: 'A01 업무 총괄', exact: true }).click();
  await page.getByLabel('내부 단계 처리 유형', { exact: true }).selectOption('image_embed');
  await page.getByLabel('내부 단계 등록 모델', { exact: true }).selectOption('registered:registered-one');
  await page.locator('input[type=file][aria-label="포스터 이미지"]').setInputFiles({ name: 'image.png', mimeType: 'image/png', buffer: Buffer.from('fixture') });
  await page.locator('input[type=file][aria-label="발주서·계약서"]').setInputFiles({ name: 'contract.txt', mimeType: 'text/plain', buffer: Buffer.from('contract') });
  await page.getByRole('button', { name: '현재 워크플로우 샘플 실행', exact: true }).click();
  await page.getByText('임베딩 차원', { exact: true }).waitFor();
  assert.equal(requests[1].purpose, 'sample'); assert.equal(requests[1].stages[0].registered_model, 'registered-one');
  assert.equal(requests[1].stages[0].model_fingerprint, 'space-one');
  await page.locator('.mt-library-list').getByRole('button', { name: '편집', exact: true }).click();
  await page.getByLabel('등록 모델 이름', { exact: true }).fill('수정한 SigLIP'); await page.getByRole('button', { name: '모델 등록·저장', exact: true }).click();
  await page.locator('.mt-library-list').getByText('수정한 SigLIP', { exact: true }).waitFor();
  await page.locator('.mt-library').screenshot({ path: '../work/modeltest-hf-registry.png' });
  await page.locator('.mt-library-list').getByRole('button', { name: '삭제', exact: true }).click();
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
  const errors = []; const unexpected = []; let submissions = 0; let cancellations = 0; let previews = 0; let job;
  page.on('pageerror', error => errors.push(error.message)); page.on('dialog', dialog => dialog.accept());
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
  await page.getByText('이전 로컬 세션을 복원했습니다.', { exact: false }).waitFor();
  await page.getByText('Pod 연결됨', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('RunPod Connect의 SSH 명령', { exact: true }).inputValue(), 'ssh root@203.0.113.10');
  await page.locator('.mt-connection-wrap>summary').click();
  assert.equal(await page.locator('.mt-connection-wrap').getAttribute('open'), null);
  await page.getByRole('button', { name: '검수 실행', exact: true }).click();
  await page.getByRole('button', { name: '검수 강제 중단', exact: true }).waitFor();
  await page.reload();
  await page.getByRole('button', { name: '검수 강제 중단', exact: true }).waitFor();
  assert.equal(submissions, 1, 'Reload must query, never resubmit');
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
  for (const name of ['소요 시간', '실행 설정', '전체 응답']) {
    await page.getByRole('button', { name, exact: true }).click();
    if (name === '소요 시간') {
      const table = page.getByRole('table', { name: '모델별 누적 소요 시간' });
      await table.getByRole('rowheader', { name: 'google/siglip2-base-patch16-224', exact: true }).waitFor();
      assert.match(await table.getByRole('row').filter({ hasText: 'google/siglip2-base-patch16-224' }).innerText(), /2\.0초\s+4\.0초\s+6\.0초\s+2회/);
      await page.getByRole('table', { name: '단계별 소요 시간' }).getByText('1.3초', { exact: true }).waitFor();
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
  await page.getByRole('button', { name: '검수 강제 중단', exact: true }).click();
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].some(button => button.textContent === '검수 강제 중단'));
  assert.equal(cancellations, 1); assert.equal(submissions, 1);
  await page.getByRole('button', { name: '라이트', exact: true }).click();
  assert.equal(await page.locator('.mt-shell').evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(244, 247, 250)');
  await page.getByRole('button', { name: '저장된 첨부·설정 지우기', exact: true }).click();
  await page.getByText('첨부·모델 설정 저장 완료', { exact: false }).waitFor();
  await page.reload();
  await page.getByRole('button', { name: '영상 검수', exact: true }).click();
  assert.equal(await page.locator('.mt-file-list').count(), 0);
  assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
});

test('workflow and agent boards edit connections, persist settings and submit dynamic OCR stages', { timeout: 90000 }, async t => {
  const server = await createServer({ configFile: false, envFile: false, root: process.cwd(), cacheDir: '../work/modeltest-vite-cache',
    define: { 'import.meta.env.VITE_API_PROFILE': JSON.stringify('legacy') }, server: { host: '127.0.0.1', port: 5187, strictPort: true, hmr: false } });
  await server.listen(); t.after(() => server.close());
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'msedge' }); t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } }); page.setDefaultTimeout(8000);
  const errors = []; const unexpected = []; const previews = []; let manifest; let jpeg;
  page.on('pageerror', error => errors.push(error.message)); page.on('dialog', dialog => dialog.accept());
  const health = { contract: CONTRACT, ready: true, prompt_overrides: 1, workflow: { version: 1, max_parallel: 2, agents: true }, stages: STAGES.map(stage => stage.id), formats: FILE_TYPES,
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
      manifest = JSON.parse(request.postDataBuffer().toString().match(/name="config"\r\n\r\n([^\r]+)/)[1]);
      const frame = { index: 0, timestamp_seconds: 0, width: 80, height: 40, preview: 'frame-00000000.jpg' };
      const outputs = stage => {
        const row = { ...frame };
        if (stage.kind === 'logo_extract') row.logos = [{ logo_index: 0, bbox: [0, 0, 20, 20], preview: `logo-${stage.id}-0-0.png`, score: 0.8 }];
        else if (['logo_similarity', 'candidate_filter', 'logo_compare'].includes(stage.kind)) {
          row.matches = [{ logo_index: 0, reference_index: 0, image_cosine_similarity: 0.9, ...(stage.kind === 'logo_compare' ? { matches: 12, inliers: 9, decision: 'candidate_match' } : {}) }];
          if (stage.kind !== 'logo_similarity') Object.assign(row, { candidate_count: 1, selected_count: 1 });
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
  await page.getByRole('checkbox', { name: '편집 사용', exact: true }).check();
  const fixedPrompt = page.locator('#mt-stage-logo_extract .mt-prompt');
  await page.getByLabel('로고 추출 등록 모델 선택', { exact: true }).selectOption('google/owlv2-base-patch16');
  assert.equal(await fixedPrompt.getAttribute('open'), null); await fixedPrompt.locator('summary').click();
  assert.equal(await page.getByLabel('로고 추출 프롬프트', { exact: true }).inputValue(), 'a brand logo');
  await page.getByLabel('로고 추출 프롬프트', { exact: true }).fill('sponsor logo');
  await fixedPrompt.locator('summary').click(); await page.getByText('첨부·모델 설정 저장 완료', { exact: false }).waitFor(); await page.reload();
  assert.equal(await fixedPrompt.getAttribute('open'), null); await fixedPrompt.locator('summary').click();
  assert.equal(await page.getByLabel('로고 추출 프롬프트', { exact: true }).inputValue(), 'sponsor logo');
  await fixedPrompt.getByRole('button', { name: '기본값 복원', exact: true }).click();
  assert.equal(await page.getByLabel('로고 추출 프롬프트', { exact: true }).inputValue(), 'a brand logo');
  await page.getByLabel('로고 추출 프롬프트', { exact: true }).fill('logo\nlogo');
  assert.equal(await page.getByLabel('로고 추출 프롬프트', { exact: true }).isVisible(), true);
  await fixedPrompt.getByRole('button', { name: '기본값 복원', exact: true }).click();
  await page.getByRole('button', { name: 'SigLIP2 → 필터 → XFeat', exact: true }).click();
  const node = page.getByRole('button', { name: '로고 추출 노드 이동', exact: true });
  const before = await node.locator('..').evaluate(element => element.style.left);
  await node.focus(); await node.press('ArrowRight');
  assert.notEqual(await node.locator('..').evaluate(element => element.style.left), before);
  const box = await node.boundingBox(); await page.mouse.move(box.x + 30, box.y + 15); await page.mouse.down(); await page.mouse.move(box.x + 70, box.y + 45); await page.mouse.up();
  await page.getByRole('button', { name: '로고 추출에서 로고 이미지 유사도 연결 삭제', exact: true }).click();
  await page.getByRole('button', { name: '로고 추출 출력 연결', exact: true }).click();
  await page.getByRole('button', { name: '로고 이미지 유사도 입력 연결', exact: true }).click();
  await page.getByRole('button', { name: '로고 추출에서 로고 이미지 유사도 연결 삭제', exact: true }).waitFor();
  await page.getByRole('button', { name: '에이전트 보드', exact: true }).click();
  await page.getByRole('button', { name: '에이전트 추가', exact: true }).click();
  await page.getByLabel('에이전트 이름', { exact: true }).fill('임시 에이전트');
  await page.getByRole('button', { name: '내부 단계 추가', exact: true }).click();
  await page.locator('.mt-agent-settings .mt-prompt>summary').click();
  await page.getByLabel('내부 단계 프롬프트', { exact: true }).fill('근거를 요약하세요.');
  await page.getByRole('button', { name: '에이전트 삭제', exact: true }).click();
  const agents = page.locator('[aria-label="편집할 에이전트"]');
  await agents.getByRole('button', { name: 'A04 문구 검수', exact: true }).click();
  await page.getByLabel('에이전트 이름', { exact: true }).fill('A04 OCR 비교');
  await page.getByLabel('내부 단계 이름', { exact: true }).fill('브랜드 문구 OCR');
  await page.getByLabel('내부 단계 등록 모델', { exact: true }).selectOption('PaddlePaddle/PaddleOCR-VL-1.6-GGUF');
  await page.locator('.mt-agent-settings .mt-prompt>summary').click();
  assert.equal(await page.getByLabel('내부 단계 프롬프트', { exact: true }).inputValue(), 'OCR:');
  await page.getByLabel('내부 단계 프롬프트', { exact: true }).fill('OCR: Read the headline only.');
  await page.getByRole('button', { name: '내부 단계 추가', exact: true }).click();
  await page.getByRole('button', { name: '내부 단계 삭제', exact: true }).click();
  await agents.getByRole('button', { name: 'A05 시각 요소 검수', exact: true }).click();
  await page.locator('[aria-label="편집할 내부 단계"]').getByRole('button', { name: '후보 필터', exact: true }).click();
  await page.getByLabel('내부 필터 최소 유사도', { exact: true }).fill('0.85');
  await page.getByLabel('내부 필터 상위 후보 수', { exact: true }).fill('1');
  await page.getByLabel('에이전트 보드 동시 실행 수', { exact: true }).selectOption('2');
  await page.locator('input[type=file][aria-label="검수 영상"]').setInputFiles({ name: 'agent.mp4', mimeType: 'video/mp4', buffer: Buffer.from('fixture') });
  await page.locator('input[type=file][aria-label="발주서·계약서"]').setInputFiles({ name: 'contract.txt', mimeType: 'text/plain', buffer: Buffer.from('fixture') });
  await page.locator('input[type=file][aria-label="원본 로고·레퍼런스"]').setInputFiles({ name: 'reference.png', mimeType: 'image/png', buffer: Buffer.from('fixture') });
  await page.getByText('첨부·모델 설정 저장 완료', { exact: false }).waitFor(); await page.reload();
  await agents.getByRole('button', { name: 'A04 OCR 비교', exact: true }).click();
  assert.equal(await page.getByLabel('내부 단계 모델', { exact: true }).inputValue(), 'PaddlePaddle/PaddleOCR-VL-1.6-GGUF');
  assert.equal(await page.getByLabel('내부 단계 이름', { exact: true }).inputValue(), '브랜드 문구 OCR');
  await page.locator('.mt-agent-settings .mt-prompt>summary').click();
  assert.equal(await page.getByLabel('내부 단계 프롬프트', { exact: true }).inputValue(), 'OCR: Read the headline only.');
  await page.getByLabel('RunPod Connect의 SSH 명령', { exact: true }).fill('ssh root@203.0.113.10');
  await page.getByRole('button', { name: '연결 확인', exact: true }).click(); await page.getByText('Pod 연결됨', { exact: true }).waitFor();
  await page.locator('.mt-agent-board').screenshot({ path: '../work/modeltest-agent-board.png' });
  await page.getByRole('button', { name: '검수 실행', exact: true }).click();
  await page.getByRole('navigation', { name: '결과 단계 선택' }).getByRole('button', { name: /브랜드 문구 OCR/ }).waitFor();
  assert.equal(manifest.workflow.agents.length, 6); assert.equal(manifest.workflow.max_parallel, 2);
  const ocr = manifest.stages.find(stage => stage.name === '브랜드 문구 OCR');
  assert.equal(ocr.kind, 'text_extract'); assert.equal(ocr.model_id, 'PaddlePaddle/PaddleOCR-VL-1.6-GGUF');
  assert.equal(ocr.prompt_override, 'OCR: Read the headline only.');
  assert.equal(manifest.stages.find(stage => stage.kind === 'text_compare').inputs.text_extract, ocr.id);
  const filter = manifest.stages.find(stage => stage.kind === 'candidate_filter'); assert.equal(filter.threshold, 0.85); assert.equal(filter.top_k, 1);
  await page.getByRole('navigation', { name: '결과 단계 선택' }).getByRole('button', { name: /원본 로고와 대조/ }).click();
  await page.getByRole('img', { name: '추출 로고 1', exact: true }).waitFor();
  await page.getByText('0.900', { exact: true }).waitFor(); await page.getByText('12 / 9개', { exact: true }).waitFor();
  assert.ok(previews.some(path => path.endsWith(`/logo-${manifest.stages.find(stage => stage.kind === 'logo_extract').id}-0-0.png`)));
  assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
});
