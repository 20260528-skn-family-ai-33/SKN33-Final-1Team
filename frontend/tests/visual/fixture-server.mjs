#!/usr/bin/env node
/**
 * Browser-visible test API. No production imports, database, inference, writes,
 * outbound requests, or real-backend fallback. Everything is process memory.
 * Response shapes/data originate in ../v1/fixtures.mjs and the existing E2Es.
 */
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { fixtures, ids, uid, wrap, page as pageDto } from '../v1/fixtures.mjs';

const MAX_BODY = 25 * 1024 * 1024;
const CSRF = 'visual-fixture-csrf';
const NOTICE = '시각 QA용 합성 응답입니다. 실제 모델·DB·업무 승인과 연결되지 않습니다.';
const clone = value => structuredClone(value);
const timestamp = () => new Date().toISOString();
const fail = (code, status = 400) => Object.assign(new Error(code), { code, status });
const isLoopback = host => ['127.0.0.1', 'localhost', '[::1]', '::1'].includes(host);
const get = (map, id) => { const value = map.get(id); if (!value) throw fail('RESOURCE_NOT_FOUND', 404); return value; };
const nonempty = value => typeof value === 'string' && value.trim().length > 0;

function bodyJSON(buffer) {
  try { const value = JSON.parse(buffer.toString('utf8')); if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(); return value; }
  catch { throw fail('INVALID_REQUEST', 422); }
}
async function readBody(request) {
  const chunks = []; let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY) throw fail('PAYLOAD_TOO_LARGE', 413);
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}
async function formData(buffer, contentType) {
  if (!contentType?.startsWith('multipart/form-data;')) throw fail('INVALID_MULTIPART', 422);
  try {
    // Constructing a Request only parses the in-memory body; it sends no request.
    return await new Request('http://127.0.0.1/fixture-parser', {
      method: 'POST', headers: { 'content-type': contentType }, body: buffer,
    }).formData();
  } catch { throw fail('INVALID_MULTIPART', 422); }
}
function selectedItems(body) {
  if (!nonempty(body.raw_requirements) || !Array.isArray(body.items) || body.items.length > 100
    || body.items.some(item => !nonempty(item.id) || typeof item.text !== 'string'
      || !['information', 'visual'].includes(item.type) || !nonempty(item.source) || typeof item.selected !== 'boolean')
    || new Set(body.items.map(item => item.id)).size !== body.items.length) throw fail('INVALID_REQUEST', 422);
  return { raw_requirements: body.raw_requirements, items: clone(body.items), source_documents: Array.isArray(body.source_documents) ? clone(body.source_documents) : null };
}

export async function createFixtureHandler({ profile = 'v1' } = {}) {
  if (!['legacy', 'v1'].includes(profile)) throw new Error('Profile must be legacy or v1');
  const f = fixtures();
  const png = await readFile(new URL('../v1/poster.png', import.meta.url));
  const cookieName = `cc_visual_${profile}`;
  const sessions = new Map(), replay = new Map();
  const projects = new Map([[f.project.id, f.project]]), images = new Map([[f.image.id, f.image]]);
  const sets = new Map([[f.set.id, f.set]]), drafts = new Map([[f.draft.id, f.draft]]);
  const versions = new Map([[f.criteria.id, f.criteria]]), runs = new Map([[f.run.id, { dto: f.run, started: null }]]);
  const evidence = new Map([[ids.result, [f.evidence]]]);
  const tokens = new Map([[ids.run, [f.token]]]), legacyRuns = new Map();
  let sequence = 1000;
  const nextId = () => uid(sequence++);
  const scoped = (map, id, pid) => { const value = get(map, id); if (value.project_id !== pid) throw fail('RESOURCE_NOT_FOUND', 404); return value; };
  const projectWrite = pid => { const value = get(projects, pid); if (value.business_state === 'archived') throw fail('PROJECT_ARCHIVED', 409); return value; };
  function currentVersion(value) {
    const draft = drafts.get(value.source_draft_id);
    return { ...clone(value), draft_is_current: !!draft && draft.revision === value.source_draft_revision };
  }
  function runView(entry) {
    const age = entry.started === null ? Infinity : Date.now() - entry.started;
    if (age >= 2200) return clone(entry.dto);
    return { ...clone(entry.dto), status: 'running', stage: age < 1000 ? 'ocr' : 'review',
      progress: age < 1000 ? 25 : 65, results: [], results_complete: false, overall_verdict: null, ocr: null };
  }
  function makeRun(pid, imageId, versionId, retryOf = null) {
    const p = projectWrite(pid), image = scoped(images, imageId, pid), version = scoped(versions, versionId, pid);
    if (!currentVersion(version).draft_is_current) throw fail('CRITERIA_STALE', 409);
    const runId = nextId(), ocrTokens = [];
    const results = version.criteria.map((criterion, index) => {
      const resultId = nextId(), tokenId = nextId(), evidenceId = nextId();
      const isVisual = criterion.type === 'visual', status = isVisual ? 'review' : 'pass';
      const y = 24 + (index % 8) * 30;
      const token = { ...clone(f.token), id: tokenId, external_token_id: String(index), order: index,
        text: '합성 관측: ' + criterion.text, frame_id: image.frame_id, bbox_xyxy: [20, y, 180, y + 22] };
      ocrTokens.push(token);
      evidence.set(resultId, [{ ...clone(f.evidence), id: evidenceId, frame_id: image.frame_id,
        token_ids: [tokenId], text: token.text, bbox_xyxy: token.bbox_xyxy, limitations: [NOTICE] }]);
      return { ...clone(f.run.results[0]), result_id: resultId, criteria_item_id: criterion.criteria_item_id,
        criterion_id: criterion.id, criterion: criterion.text, type: criterion.type, verdict: status, status,
        observed_text: token.text, observed_value: null, reason: isVisual ? '시각 조건은 담당자 확인 대상으로 표시하는 합성 사례입니다.' : NOTICE,
        limitations: [NOTICE], check_method: 'visual-http-fixture', evidence: [], evidence_count: 1, evidence_complete: false };
    });
    const dto = { ...clone(f.run), id: runId, project_id: pid, image_id: imageId, criteria_version_id: versionId,
      retry_of: retryOf, project_name: p.name, criteria_version: version.version_no, criteria: clone(version.criteria), image: clone(image),
      results, overall_verdict: results.some(result => result.verdict === 'review') ? 'review' : 'pass',
      created_at: timestamp(), runtime: { pipeline: 'visual-http-fixture', notice: NOTICE },
      ocr: { ...clone(f.run.ocr), id: nextId(), frame_id: image.frame_id, record_count: ocrTokens.length } };
    runs.set(runId, { dto, started: Date.now() }); tokens.set(runId, ocrTokens);
    return { id: runId, project_id: pid, image_id: imageId, criteria_version_id: versionId, retry_of: retryOf, status: 'queued' };
  }

  return async function handler(request, response) {
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.setHeader('X-Creative-Checker-Fixture', profile);
    const json = (body, status = 200) => {
      response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
      response.end(JSON.stringify(profile === 'v1' ? wrap(body) : body));
    };
    const imageResponse = () => { response.writeHead(200, { 'Content-Type': 'image/png' }); response.end(png); };
    try {
      const url = new URL(request.url, `http://${request.headers.host || '127.0.0.1'}`);
      const peer = request.socket?.remoteAddress;
      if ((peer && !['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(peer)) || !isLoopback(url.hostname)) throw fail('LOCAL_ONLY', 403);
      if (request.headers.origin && !isLoopback(new URL(request.headers.origin).hostname)) throw fail('LOCAL_ONLY', 403);
      const path = url.pathname, method = request.method;
      if (path === '/__fixture' && method === 'GET') return json({ profile, notice: NOTICE, persistent: false, model_calls: false });
      const cookie = String(request.headers.cookie || '').split(';').map(part => part.trim()).find(part => part.startsWith(cookieName + '='))?.slice(cookieName.length + 1);
      const user = sessions.get(cookie) || null;
      const auth = '/api/v1/auth';
      if (path === auth + '/me' && method === 'GET') return json({ authenticated: !!user, user });
      if (path === auth + '/csrf' && method === 'GET') return json({ csrf_token: CSRF, developer_login_available: true });
      if (path === auth + '/login' && method === 'POST') {
        if (request.headers['x-csrftoken'] !== CSRF) throw fail('CSRF_FAILED', 403);
        const body = bodyJSON(await readBody(request));
        const developer = body.username === 'admin' && body.password === 'admin';
        const regular = profile === 'v1' ? body.email === 'reviewer@example.test' && body.password?.trim() === 'fixture-password'
          : body.username?.trim() === 'tester' && body.password === 'test-password';
        if (!developer && !regular) throw fail(profile === 'v1' ? 'INVALID_CREDENTIALS' : 'invalid_credentials', 401);
        const currentUser = profile === 'v1' ? { ...clone(f.user), ...(developer ? { account_kind: 'developer', email: null } : {}) }
          : { id: 'visual-test-user', username: developer ? 'admin' : 'tester', display_name: developer ? '관리자 (개발용)' : '검증 사용자' };
        if (cookie) sessions.delete(cookie);
        const sessionId = randomUUID(); sessions.set(sessionId, currentUser);
        response.setHeader('Set-Cookie', `${cookieName}=${sessionId}; Path=/; HttpOnly; SameSite=Strict`);
        return json({ user: currentUser });
      }
      if (path === auth + '/logout' && method === 'POST') {
        if (request.headers['x-csrftoken'] !== CSRF) throw fail('CSRF_FAILED', 403);
        sessions.delete(cookie); response.setHeader('Set-Cookie', `${cookieName}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict`);
        response.writeHead(204); response.end(); return;
      }
      if (method === 'GET' && path === (profile === 'v1' ? '/api/v1/health' : '/api/health')) {
        return json(profile === 'v1'
          ? { status: 'degraded', checks: { db: { state: 'ready', simulated: true, backend: 'in_memory_fixture' }, model: { state: 'not_connected' } }, limits: { sample_available: false }, notice: NOTICE }
          : { runtime: { ready: true, engine: 'fixture-only', execution_target: 'test', device: 'fixture' }, limits: {}, notice: NOTICE });
      }
      if (!path.startsWith('/api/')) throw fail('FIXTURE_ROUTE_NOT_FOUND', 404);
      const knownPath = profile === 'legacy'
        ? /^\/api\/(?:sample(?:\/image)?|requirements\/extract\/stream|reviews(?:\/[^/]+(?:\/image)?)?)$/.test(path)
        : /^\/api\/v1\/(?:projects(?:\/[^/]+(?:\/(?:images|criteria-sets|criteria-drafts|criteria-versions|reviews)(?:\/[^/]+)?)?)?|images\/[^/]+(?:\/content)?|reviews(?:\/[^/]+(?:\/(?:retry|export|ocr-records|results\/[^/]+\/evidence))?)?)$/.test(path);
      if (!knownPath) throw fail('FIXTURE_ROUTE_NOT_FOUND', 404);
      if (!user) throw fail('AUTHENTICATION_REQUIRED', 401);

      if (profile === 'legacy') {
        if (path === '/api/sample' && method === 'GET') return json({ project_name: '시각 QA 샘플 프로젝트', requirements: '행사명: 가을 문화 축제\n기간: 2026.10.30 ~ 11.01\n로고와 배치를 확인해 주세요.' });
        if (path === '/api/sample/image' && method === 'GET') return imageResponse();
        if (path === '/api/requirements/extract/stream' && method === 'POST') {
          const form = await formData(await readBody(request), request.headers['content-type']);
          if (!form.get('document')) throw fail('INVALID_MULTIPART', 422);
          const conditions = [{ text: '행사명: 가을 문화 축제', type: 'information', page: 1, source: '행사명은 가을 문화 축제로 표기한다. (시각 QA 예시)',
            label: '행사명', requirement: '가을 문화 축제', level: '필수', inspection: '원문의 행사명과 대조한다.', approval_status: 'pending', source_kind: 'fixture' }];
          response.writeHead(200, { 'Content-Type': 'application/x-ndjson; charset=utf-8' });
          const events = [
            { type: 'progress', stage: 'reading', percent: 20, message: '예시 문서 읽기 단계' },
            { type: 'progress', stage: 'extracting', percent: 70, message: '예시 조건과 출처 정리 중' },
            { type: 'result', result: { conditions, requirements: conditions[0].text, page_count: 1, scanned_pages: 0, model: 'fixture-only', warnings: [NOTICE] } },
          ];
          let index = 0;
          const emit = () => {
            if (response.destroyed || response.writableEnded) return;
            response.write(JSON.stringify(events[index++]) + '\n');
            if (index === events.length) response.end();
            else { const timer = setTimeout(emit, 450); response.once('close', () => clearTimeout(timer)); }
          };
          emit(); return;
        }
        if (path === '/api/reviews' && method === 'POST') {
          const form = await formData(await readBody(request), request.headers['content-type']);
          let criteria;
          try { criteria = JSON.parse(String(form.get('criteria'))); } catch { throw fail('INVALID_REQUEST', 422); }
          if (!form.get('image') || !Array.isArray(criteria) || !criteria.length || criteria.length > 100) throw fail('INVALID_REQUEST', 422);
          const runId = randomUUID();
          const dto = { id: runId, status: 'completed', stage: 'completed', progress: 100,
            project_name: String(form.get('project_name') || '시각 QA'), criteria_version: Number(form.get('criteria_version')) || 1,
            image: { url: `/api/reviews/${runId}/image`, width: f.image.width, height: f.image.height }, criteria,
            results: criteria.map((item, index) => ({ criterion_id: item.id, criterion: item.text, type: item.type,
              status: item.type === 'visual' ? 'review' : index === 1 ? 'fail' : 'pass',
              reason: NOTICE, observed_text: '합성 관측: ' + item.text,
              evidence: [{ text: '합성 관측: ' + item.text, bbox_xyxy: [20, 24 + index % 8 * 30, 180, 46 + index % 8 * 30], confidence: null }] })),
            model_runtime: { status: 'not_configured' }, runtime: { pipeline: 'visual-http-fixture', notice: NOTICE } };
          legacyRuns.set(runId, { dto, started: Date.now() }); return json({ id: runId, status: 'queued' }, 202);
        }
        const match = path.match(/^\/api\/reviews\/([^/]+)(\/image)?$/);
        if (match && method === 'GET') {
          const entry = get(legacyRuns, match[1]); if (match[2]) return imageResponse();
          if (Date.now() - entry.started < 1800) return json({ id: entry.dto.id, status: 'running', stage: 'ocr', progress: 40 });
          return json(entry.dto);
        }
        throw fail('FIXTURE_ROUTE_NOT_FOUND', 404);
      }

      if (!path.startsWith('/api/v1/')) throw fail('FIXTURE_PROFILE_MISMATCH', 404);
      const mutating = !['GET', 'HEAD'].includes(method);
      if (mutating && request.headers['x-csrftoken'] !== CSRF) throw fail('CSRF_FAILED', 403);
      const buffer = mutating ? await readBody(request) : Buffer.alloc(0);
      const body = buffer.length && !request.headers['content-type']?.startsWith('multipart/') ? bodyJSON(buffer) : null;
      const key = request.headers['idempotency-key'];
      const replayKey = key ? `${cookie}:${method}:${path}:${key}` : null;
      const bodySignature = buffer.toString('base64');
      if (replayKey && replay.has(replayKey)) {
        const previous = replay.get(replayKey); if (previous.signature !== bodySignature) throw fail('IDEMPOTENCY_KEY_REUSED', 409);
        return json(previous.body, previous.status);
      }
      const reply = (value, status = 200) => {
        if (replayKey && status < 300) replay.set(replayKey, { signature: bodySignature, body: clone(value), status });
        return json(value, status);
      };
      if (path === '/api/v1/projects') {
        if (method === 'GET') {
          const archived = url.searchParams.get('archived') || 'exclude';
          return reply(pageDto([...projects.values()].filter(p => archived === 'include' || (p.business_state === 'archived') === (archived === 'only'))));
        }
        if (method === 'POST') {
          if (!nonempty(body?.name)) throw fail('INVALID_REQUEST', 422);
          const value = { ...clone(f.project), id: nextId(), name: body.name.trim() }; projects.set(value.id, value);
          const set = { id: nextId(), project_id: value.id, name: '기본 검수 기준' }; sets.set(set.id, set);
          return reply(value, 201);
        }
      }
      const imageMatch = path.match(/^\/api\/v1\/images\/([^/]+)(\/content)?$/);
      if (imageMatch && method === 'GET') { const image = get(images, imageMatch[1]); return imageMatch[2] ? imageResponse() : reply(image); }
      const projectMatch = path.match(/^\/api\/v1\/projects\/([^/]+)(?:\/([^/]+)(?:\/([^/]+))?)?$/);
      if (projectMatch) {
        const [, pid, resource, identifier] = projectMatch; const project = get(projects, pid);
        if (!resource) {
          if (method === 'GET') return reply(project);
          if (method === 'PATCH') {
            if (body.name !== undefined) { if (!nonempty(body.name)) throw fail('INVALID_REQUEST', 422); project.name = body.name.trim(); }
            if (typeof body.archived === 'boolean') project.business_state = body.archived ? 'archived' : 'active';
            return reply(project);
          }
        }
        if (mutating) projectWrite(pid);
        if (resource === 'images' && !identifier) {
          if (method === 'GET') return reply(pageDto([...images.values()].filter(value => value.project_id === pid)));
          if (method === 'POST') {
            const form = await formData(buffer, request.headers['content-type']);
            if (!form.get('image')) throw fail('INVALID_MULTIPART', 422);
            const id = nextId(); const value = { ...clone(f.image), id, asset_id: nextId(), frame_id: nextId(), project_id: pid, url: `/api/v1/images/${id}/content` };
            images.set(id, value); return reply(value, 201);
          }
        }
        if (resource === 'criteria-sets' && !identifier) {
          if (method === 'GET') return reply(pageDto([...sets.values()].filter(value => value.project_id === pid)));
          if (method === 'POST') { if (!nonempty(body.name)) throw fail('INVALID_REQUEST', 422); const value = { id: nextId(), project_id: pid, name: body.name }; sets.set(value.id, value); return reply(value, 201); }
        }
        if (resource === 'criteria-drafts') {
          if (!identifier && method === 'GET') return reply(pageDto([...drafts.values()].filter(value => value.project_id === pid)));
          if (!identifier && method === 'POST') {
            scoped(images, body.image_id, pid); scoped(sets, body.criteria_set_id, pid);
            const value = { ...selectedItems(body), id: nextId(), project_id: pid, criteria_set_id: body.criteria_set_id, image_id: body.image_id, revision: '1', state: 'editing' };
            drafts.set(value.id, value); return reply(value, 201);
          }
          if (identifier) {
            const value = scoped(drafts, identifier, pid);
            if (method === 'GET') return reply(value);
            if (method === 'PUT') {
              if (body.expected_revision !== value.revision) throw fail('DRAFT_REVISION_CONFLICT', 409);
              Object.assign(value, selectedItems(body), { revision: String(BigInt(value.revision) + 1n) }); return reply(value);
            }
          }
        }
        if (resource === 'criteria-versions') {
          if (identifier && method === 'GET') return reply(currentVersion(scoped(versions, identifier, pid)));
          if (!identifier && method === 'GET') return reply(pageDto([...versions.values()].filter(value => value.project_id === pid).map(currentVersion)));
          if (!identifier && method === 'POST') {
            const draft = scoped(drafts, body.draft_id, pid);
            if (body.expected_revision !== draft.revision) throw fail('DRAFT_REVISION_CONFLICT', 409);
            const selected = draft.items.filter(item => item.selected);
            if (!selected.length || selected.some(item => !nonempty(item.text))) throw fail('INVALID_REQUEST', 422);
            const previous = [...versions.values()].filter(value => value.criteria_set_id === draft.criteria_set_id);
            const value = { ...clone(f.criteria), id: nextId(), project_id: pid, criteria_set_id: draft.criteria_set_id, image_id: draft.image_id,
              version_no: Math.max(0, ...previous.map(value => value.version_no)) + 1, source_draft_id: draft.id, source_draft_revision: draft.revision,
              criteria: selected.map((item, index) => ({ criteria_item_id: nextId(), id: item.id, position: index + 1, text: item.text, type: item.type, source: item.source, is_required: null })) };
            versions.set(value.id, value); return reply(value, 201);
          }
        }
        if (resource === 'reviews' && !identifier && method === 'GET') {
          let list = [...runs.values()].map(runView).filter(value => value.project_id === pid);
          if (url.searchParams.get('status')) list = list.filter(value => value.status === url.searchParams.get('status'));
          if (url.searchParams.get('image_id')) list = list.filter(value => value.image_id === url.searchParams.get('image_id'));
          return reply(pageDto(list.reverse()));
        }
      }
      if (path === '/api/v1/reviews' && method === 'POST') return reply(makeRun(body.project_id, body.image_id, body.criteria_version_id), 202);
      const runMatch = path.match(/^\/api\/v1\/reviews\/([^/]+)(?:\/(retry|export|ocr-records|results)(?:\/([^/]+)\/evidence)?)?$/);
      if (runMatch) {
        const [, runId, action, resultId] = runMatch; const entry = get(runs, runId), run = runView(entry);
        if (!action && method === 'GET') return reply(run);
        if (action === 'retry' && method === 'POST') return reply(makeRun(run.project_id, run.image_id, run.criteria_version_id, runId), 202);
        if (action === 'ocr-records' && method === 'GET') {
          const rows = run.status === 'completed' ? tokens.get(runId) || [] : [];
          return reply(pageDto(rows, { availability: run.status === 'completed' ? 'available' : 'pending', record_count: rows.length }));
        }
        if (action === 'results' && resultId && method === 'GET') {
          if (!run.results.some(result => result.result_id === resultId)) throw fail('RESOURCE_NOT_FOUND', 404);
          const rows = evidence.get(resultId) || []; return reply(pageDto(rows, { availability: 'available', evidence_count: rows.length }));
        }
        if (action === 'export' && method === 'GET') {
          return reply({ ...run, contract_version: 'cc-review-export-v1', exported_at: timestamp(), fixture_notice: NOTICE,
            results: run.results.map(result => ({ ...result, evidence: evidence.get(result.result_id) || [], evidence_complete: true })), ocr_records: tokens.get(runId) || [] });
        }
      }
      throw fail('FIXTURE_ROUTE_NOT_FOUND', 404);
    } catch (error) {
      if (response.headersSent || response.destroyed) { response.end(); return; }
      json({ error: { code: error.code || 'FIXTURE_INTERNAL_ERROR', message: error.code === 'invalid_credentials' ? '아이디 또는 비밀번호를 확인해 주세요.' : NOTICE, fields: {} } }, error.status || 500);
    }
  };
}

export function parseOptions(argv) {
  let profile = 'v1', port = 8991;
  for (let index = 0; index < argv.length; index++) {
    if (argv[index] === '--profile') profile = argv[++index];
    else if (argv[index] === '--port') port = Number(argv[++index]);
    else throw new Error('Usage: node fixture-server.mjs [--profile v1|legacy] [--port 8991]');
  }
  if (!['legacy', 'v1'].includes(profile) || !Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid profile or local port');
  return { profile, port };
}

async function main() {
  const options = parseOptions(process.argv.slice(2));
  const handler = await createFixtureHandler(options);
  const server = createServer(handler);
  server.requestTimeout = 15000; server.headersTimeout = 10000;
  server.on('error', error => { console.error(`Fixture server could not start (${error.code || 'error'}).`); process.exitCode = 1; });
  server.listen(options.port, '127.0.0.1', () => console.log(`VISUAL FIXTURE ONLY: ${options.profile} API at http://127.0.0.1:${options.port} — no database, model, or persistence.`));
  for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { server.close(); server.closeAllConnections(); });
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(() => { console.error('Invalid fixture options or unavailable bundled fixture. See README.txt.'); process.exitCode = 1; });
}
