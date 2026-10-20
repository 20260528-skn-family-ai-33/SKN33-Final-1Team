import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import * as api from '../../src/v1/api.js';
import { fixtures, ids, uid, wrap } from './fixtures.mjs';
const originalFetch = globalThis.fetch; const originalWindow = globalThis.window;
afterEach(() => { globalThis.fetch = originalFetch; globalThis.window = originalWindow; });
const json = (body, status = 200) => new Response(JSON.stringify(wrap(body)), { status, headers: { 'content-type': 'application/json' } });
const csrf = () => json({ csrf_token: 'fresh-token', developer_login_available: true });
test('email login sends fresh csrf, untouched password, same-origin cookies, then verifies me', async () => {
  const calls = []; const f = fixtures(); globalThis.fetch = async (path, options) => { calls.push([path, options]); return path.endsWith('/csrf') ? csrf() : path.endsWith('/login') ? json({ user: f.user }) : json({ authenticated: true, user: f.user }); };
  assert.equal((await api.login({ email: 'x@example.test', password: '  untrimmed  ' })).id, ids.actor); assert.deepEqual(calls.map(([path]) => path), ['/api/v1/auth/csrf', '/api/v1/auth/login', '/api/v1/auth/me']);
  const [, options] = calls[1]; assert.equal(options.headers['X-CSRFToken'], 'fresh-token'); assert.deepEqual(JSON.parse(options.body), { email: 'x@example.test', password: '  untrimmed  ' }); assert.equal(options.credentials, 'same-origin'); assert.equal(options.redirect, 'error');
});
test('developer login is an explicit username body and logout accepts only 204', async () => {
  const calls = []; const f = fixtures(); globalThis.fetch = async (path, options) => { calls.push([path, options]); return path.endsWith('csrf') ? csrf() : path.endsWith('logout') ? new Response(null, { status: 204 }) : path.endsWith('login') ? json({ user: f.user }) : json({ authenticated: true, user: f.user }); };
  await api.login({ username: 'admin', password: 'admin' }); await api.logout(); assert.deepEqual(JSON.parse(calls[1][1].body), { username: 'admin', password: 'admin' }); assert.equal(calls.at(-1)[0], '/api/v1/auth/logout');
});
test('401 signals expiry; 403 preserves authentication; unsafe server messages stay private', async () => {
  let expired = 0; globalThis.window = { dispatchEvent() { expired++; } };
  globalThis.fetch = async () => json({ error: { code: 'UNRECOGNIZED', message: '/private/secret' } }, 403);
  await assert.rejects(api.project(ids.project), error => !error.message.includes('secret') && error.status === 403); assert.equal(expired, 0);
  globalThis.fetch = async () => json({ error: { code: 'AUTHENTICATION_REQUIRED' } }, 401); await assert.rejects(api.project(ids.project)); assert.equal(expired, 1);
});
test('HTML 200, legacy JSON, wrong ID and wrong project cannot pass as server data', async () => {
  for (const response of [new Response('<html>fallback</html>', { headers: { 'content-type': 'text/html' } }), new Response('{}', { headers: { 'content-type': 'application/json' } }), json({ ...fixtures().project, id: uid(88) })]) { globalThis.fetch = async () => response; await assert.rejects(api.project(ids.project), error => error.code === 'invalid_response'); }
  globalThis.fetch = async () => json({ ...fixtures().image, project_id: uid(88) }); await assert.rejects(api.image(ids.image, ids.project), error => error.status === 404);
});
test('mutations never retry a network failure and preserve idempotency inputs', async () => {
  const entry = { operation: 'confirm_criteria', project_id: ids.project, body: { draft_id: ids.draft, expected_revision: '9007199254740993' }, key: uid(30) }; const calls = [];
  globalThis.fetch = async (path, options) => { calls.push([path, options]); if (path.endsWith('csrf')) return csrf(); throw new TypeError('offline'); };
  await assert.rejects(api.sendPending(entry), error => error.code === 'network_error'); assert.equal(calls.length, 2); assert.equal(calls[1][1].headers['Idempotency-Key'], entry.key); assert.deepEqual(JSON.parse(calls[1][1].body), entry.body);
});
test('successful mutation must describe the exact frozen input', async () => {
  globalThis.fetch = async path => path.endsWith('csrf') ? csrf() : json({ ...fixtures().criteria, source_draft_revision: '99' });
  await assert.rejects(api.sendPending({ operation: 'confirm_criteria', project_id: ids.project, body: { draft_id: ids.draft, expected_revision: '1' }, key: uid(31) }), error => error.code === 'invalid_response');
});
test('upload uses native multipart boundary and enforces 20MiB before network', async () => {
  let options; let calls = 0; globalThis.fetch = async (path, input) => { calls++; if (path.endsWith('csrf')) return csrf(); options = input; return json(fixtures().image, 201); };
  await api.upload(ids.project, new File(['png'], 'test.png', { type: 'image/png' })); assert.equal(options.body instanceof FormData, true); assert.equal(options.headers['Content-Type'], undefined);
  await assert.rejects(api.upload(ids.project, { size: 20 * 1024 * 1024 + 1 })); assert.equal(calls, 2);
});
test('PUT uses server decimal revision and exact editable fields', async () => {
  const f = fixtures(); const body = { expected_revision: f.draft.revision, raw_requirements: f.draft.raw_requirements, items: f.draft.items, source_documents: null }; let options;
  globalThis.fetch = async (path, input) => { if (path.endsWith('csrf')) return csrf(); options = input; return json(f.draft); };
  await api.putDraft(ids.project, ids.draft, body); assert.equal(options.method, 'PUT'); assert.deepEqual(JSON.parse(options.body), body);
});
test('export downloads server bytes with identity checks rather than serializing current view', async () => {
  const body = { contract_version: 'cc-review-export-v1', id: ids.run, project_id: ids.project, results: ['server-only-full-data'] }; globalThis.fetch = async () => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });
  const blob = await api.download(ids.project, ids.run); assert.deepEqual(JSON.parse(await blob.text()), body);
  globalThis.fetch = async () => new Response(JSON.stringify({ ...body, id: uid(88) }), { headers: { 'content-type': 'application/json' } }); await assert.rejects(api.download(ids.project, ids.run));
});
test('API paths cannot fall back to a legacy or external endpoint', () => { for (const path of ['https://evil.test/api/v1/me', '/api/reviews', '/api/v1/../legacy', '/api/v1/\\evil']) assert.throws(() => api.apiPath(path)); });
