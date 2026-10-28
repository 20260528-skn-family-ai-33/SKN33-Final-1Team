import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chromium } from 'playwright';
import { createServer } from 'vite';
import { CONTRACT, FILE_LIMITS, FILE_TYPES, STAGES } from '../../src/pages/modeltest/modeltest.js';

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
    if (url.pathname === '/api/v1/auth/me') return json({ authenticated: false });
    if (url.pathname === '/api/modeltest/ssh/connections') return json({ ...health, connection_id: 'fixture-connection' });
    if (url.pathname.endsWith('/health')) return json(health);
    if (url.pathname.endsWith('/runs') && request.method() === 'POST') {
      submissions++;
      const frames = [0, 30].map((index, i) => ({ index, timestamp_seconds: i * 1.25, width: 80, height: 40, preview: `frame-${String(index).padStart(8, '0')}.jpg` }));
      job = { contract: CONTRACT, id: request.headers()['idempotency-key'], status: 'running', references: [{ index: 0, preview: 'preview-reference-0.jpg' }],
        stages: [{ id: 'frames', status: 'completed', output: { frames } },
          { id: 'logo_extract', model_id: 'google/owlv2-base-patch16', status: 'completed', output: { items: frames.map((frame, index) => ({ ...frame, logos: [{ bbox: [8, 4, 40, 20], logo_index: 0, preview: `logo-${index}-0.png`, score: 0.8 }] })) } },
          { id: 'logo_compare', model_id: 'vismatch/xfeat', status: 'running', output: { items: frames.map(frame => ({ ...frame, matches: [{ logo_index: 0, reference_index: 0, matches: 12, inliers: 9, keypoints: [100, 105], inlier_ratio: 0.75, descriptor_similarity: 0.93, image_cosine_similarity: 0.86, decision: 'candidate_match' }] })) } }] };
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
  await page.locator('input[type=file][aria-label="원본 로고·레퍼런스"]').setInputFiles({ name: 'logo.jpg', mimeType: 'image/jpeg', buffer: Buffer.from(jpeg, 'base64') });
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
  await page.locator('.mt-connection-wrap>summary').click();
  assert.equal(await page.locator('.mt-connection-wrap').getAttribute('open'), null);
  await page.getByRole('button', { name: '검수 실행', exact: true }).click();
  await page.getByRole('button', { name: '검수 강제 중단', exact: true }).waitFor();
  await page.reload();
  await page.getByRole('button', { name: '검수 강제 중단', exact: true }).waitFor();
  assert.equal(submissions, 1, 'Reload must query, never resubmit');
  assert.equal(await page.getByLabel('RunPod Connect의 SSH 명령', { exact: true }).inputValue(), '');
  await page.getByRole('navigation', { name: '결과 단계 선택' }).getByRole('button', { name: '원본 로고와 대조' }).click();
  await page.getByRole('navigation', { name: '분석 프레임 선택' }).getByRole('button', { name: '30 프레임 · 1.25초', exact: true }).click();
  await page.getByRole('img', { name: '추출 로고 1', exact: true }).waitFor();
  await page.getByRole('img', { name: '원본 로고 logo.jpg', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '로고 1 위치 보기', exact: true }).evaluate(node => node.style.left), '10%');
  for (const text of ['0.860', '0.930', '75.0%', '12 / 9개', '일치 후보']) await page.getByText(text, { exact: true }).waitFor();
  await page.getByRole('img', { name: '30 프레임 · 1.25초 분석 이미지', exact: true }).waitFor();
  const loadedPreviews = previews;
  for (const name of ['실행 설정', '전체 응답']) {
    await page.getByRole('button', { name, exact: true }).click();
    await page.getByRole('button', { name: '단계별 결과', exact: true }).click();
    await page.getByRole('heading', { name: '30 프레임 · 1.25초', exact: true }).waitFor();
    await page.getByRole('img', { name: '추출 로고 1', exact: true }).waitFor();
  }
  assert.equal(previews, loadedPreviews, 'Changing result tabs must reuse previews and keep the selected frame');
  await page.getByRole('navigation', { name: '결과 단계 선택' }).getByRole('button', { name: '로고 추출', exact: true }).click();
  await page.getByRole('navigation', { name: '결과 단계 선택' }).getByRole('button', { name: '원본 로고와 대조', exact: true }).click();
  await page.getByRole('heading', { name: '30 프레임 · 1.25초', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '검수 강제 중단', exact: true }).evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(185, 28, 28)');
  await page.getByRole('button', { name: '검수 강제 중단', exact: true }).click();
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].some(button => button.textContent === '검수 강제 중단'));
  assert.equal(cancellations, 1); assert.equal(submissions, 1);
  await page.getByRole('button', { name: '저장된 첨부·설정 지우기', exact: true }).click();
  await page.getByText('첨부·모델 설정 저장 완료', { exact: false }).waitFor();
  await page.reload();
  await page.getByRole('button', { name: '영상 검수', exact: true }).click();
  assert.equal(await page.locator('.mt-file-list').count(), 0);
  assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
});
