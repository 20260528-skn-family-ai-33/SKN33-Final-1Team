import assert from 'node:assert/strict';
import test from 'node:test';
import { chromium } from 'playwright';
import { createServer } from 'vite';

// Synthetic API replies exercise the real desktop UI. DB isolation/races have
// separate PostgreSQL integration coverage; no production records are altered.
test('내 작업: 대기 취소, 충돌 재조회, 숨김·복원과 새로고침 유지', { timeout: 60000 }, async t => {
  const vite = await createServer({ server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  t.after(() => vite.close()); await vite.listen();
  const base = `http://127.0.0.1:${vite.httpServer.address().port}`;
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } }); page.setDefaultTimeout(8000);
  const errors = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  const item = (run_id, state, project_name) => ({ run_id, state, project_name, project_id: 'project', kind: 'document',
    created_at: '2026-11-09T06:30:00Z', hidden: false, can_cancel: state === 'queued', can_hide: ['failed', 'completed'].includes(state) });
  const items = [item('queued', 'queued', '대기 중인 문서'), item('race', 'queued', '방금 시작된 문서'),
    item('failed', 'failed', '실패한 문서'), item('completed', 'completed', '완료된 검수'),
    item('pending', 'waiting_review', '담당자 승인 대기 중')];
  const video = { id: 'video-done', status: 'completed', project_name: '완료된 동영상 검수', criteria_version: 1,
    created_at: '2026-11-09T07:30:00Z', media: { container: 'avi', width: 640, height: 480, duration_ms: 3000 },
    coverage: { duration_ms: 3000, inspected_ms: [1000] }, review: { results: [{ criterion_id: 'title', criterion: '제목 확인', status: 'pass',
      observed_text: '전시 제목', evidence_segments: [{ start_ms: 1000, end_ms: 2000, time_ms: 1000, text: '전시 제목' }] }] } };
  await page.route(base + '/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    let body;
    if (path === '/api/v1/auth/me') body = { authenticated: true, user: { id: 'user', display_name: '검수 담당자' } };
    else if (path.endsWith('/health')) body = { status: 'ok', ready: true };
    else if (path.endsWith('/csrf')) body = { csrf_token: 'token' };
    else if (path === '/api/video-reviews') body = { items: [video] };
    else if (path === '/api/video-reviews/status') body = { ready: true };
    else if (path === `/api/video-reviews/${video.id}`) body = video;
    else if (path === '/api/video-logos') body = { items: [] };
    else if (path.endsWith('/my-tasks')) {
      const mode = url.searchParams.get('visibility');
      body = { items: items.filter(row => mode === 'all' || row.hidden === (mode === 'hidden')), has_more: false, next_cursor: null };
    } else if (request.method() === 'POST') {
      writes.push(path); assert.equal(request.headers()['x-csrftoken'], 'token');
      const id = path.split('/').at(-2), row = items.find(item => item.run_id === id);
      assert.ok(row, path);
      if (path.endsWith('/cancel')) {
        if (id === 'race') {
          row.state = 'extracting'; row.can_cancel = false;
          return route.fulfill({ status: 409, json: { error: { code: 'FRAMEWORK_TASK_NOT_QUEUED', message: '이미 시작된 작업은 취소할 수 없습니다.' } } });
        }
        row.state = 'cancelled'; row.can_cancel = false; row.can_hide = true; body = {};
      } else {
        assert.ok(path.endsWith('/history-visibility')); row.hidden = request.postDataJSON().hidden;
        body = { run_id: row.run_id, hidden: row.hidden };
      }
    } else throw new Error('Unexpected route ' + path);
    await route.fulfill({ json: body });
  });
  await page.goto(base + '/review/tasks');
  const row = name => page.getByRole('row').filter({ hasText: name });
  await row('대기 중인 문서').waitFor();
  assert.equal(await page.getByRole('heading', { name: 'PDF 묶음 분석' }).count(), 0);
  await row('대기 중인 문서').getByRole('button', { name: '대기 취소' }).click();
  await row('대기 중인 문서').getByText('취소됨', { exact: true }).waitFor();
  await row('방금 시작된 문서').getByRole('button', { name: '대기 취소' }).click();
  await page.getByText('이미 시작된 작업은 취소할 수 없습니다.', { exact: true }).waitFor();
  await row('방금 시작된 문서').getByText('문서 분석 중', { exact: true }).waitFor();
  assert.equal(await row('방금 시작된 문서').getByRole('button', { name: '대기 취소' }).count(), 0);
  assert.equal(await row('담당자 승인 대기 중').getByRole('button', { name: '이력 숨기기' }).count(), 0);
  await row('실패한 문서').getByRole('button', { name: '이력 숨기기' }).click();
  await page.getByText('이력을 숨겼습니다.', { exact: false }).waitFor();
  await row('실패한 문서').waitFor({ state: 'detached' });
  await page.reload(); await row('완료된 검수').waitFor();
  assert.equal(await row('실패한 문서').count(), 0);
  await page.getByLabel('이력 표시').selectOption('hidden');
  await row('실패한 문서').getByRole('button', { name: '다시 표시' }).click();
  await page.getByText('숨긴 이력이 없어요.', { exact: true }).waitFor();
  assert.equal(await row(video.project_name).count(), 0);
  await page.getByLabel('이력 표시').selectOption('visible');
  await row('실패한 문서').waitFor();
  assert.equal(writes.length, 4); assert.deepEqual(errors, []);
  if (process.env.E2E_TASKS_SCREENSHOT) await page.screenshot({ path: process.env.E2E_TASKS_SCREENSHOT, fullPage: true });
  await row(video.project_name).getByRole('link', { name: '열기' }).click();
  await page.getByRole('button', { name: '보고서 생성하기', exact: true }).click();
  await page.getByRole('heading', { name: '검수 보고서', exact: true }).waitFor();
  await page.locator('.rv-report-print').getByText(video.project_name + ' · 동영상 검수', { exact: true }).waitFor();
  await page.getByText('0:01–0:02 · 전시 제목', { exact: true }).waitFor();
  await page.getByRole('button', { name: '결과 확인으로 돌아가기', exact: true }).click();
  await page.getByRole('heading', { name: video.project_name + ' 검수 결과', exact: true }).waitFor();
});

test('대기 실행 화면에서 취소하면 작업 종료를 표시한다', { timeout: 30000 }, async t => {
  const vite = await createServer({ server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  t.after(() => vite.close()); await vite.listen();
  const base = `http://127.0.0.1:${vite.httpServer.address().port}`;
  const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' });
  t.after(() => browser.close()); const page = await browser.newPage(); page.setDefaultTimeout(8000);
  let cancelled = false;
  const context = { run_id: 'queued', actor_id: 'user', project_id: 'project' };
  const snapshot = () => ({ context, execution_status: cancelled ? 'failed' : 'queued',
    ...(cancelled ? { error: { code: 'USER_CANCELLED', message: '대기 작업 취소' } } : {}) });
  await page.route(base + '/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body;
    if (path.endsWith('/auth/me')) body = { authenticated: true, user: { id: 'user', display_name: '담당자' } };
    else if (path.endsWith('/health')) body = { status: 'ok', ready: true };
    else if (path.endsWith('/csrf')) body = { csrf_token: 'token' };
    else if (path.endsWith('/cancel')) { cancelled = true; body = snapshot(); }
    else if (path.endsWith('/runs/queued')) body = snapshot();
    // 실행 화면은 이 실행에 연결된 포스터 로고 작업도 함께 읽는다. 이 시험에는 로고 작업이 없다.
    else if (path === '/api/poster-logos') body = { items: [] };
    else throw new Error('Unexpected route ' + path);
    await route.fulfill({ json: body });
  });
  await page.goto(base + '/review/new?framework=queued');
  await page.getByRole('button', { name: '대기 작업 취소', exact: true }).click();
  await page.getByRole('heading', { name: '대기 작업 취소됨', exact: true }).waitFor();
  assert.equal(cancelled, true);
  assert.equal(await page.getByRole('progressbar').count(), 0);
  await page.getByRole('link', { name: '내 작업으로' }).waitFor();
});
