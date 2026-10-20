import test from 'node:test';
import assert from 'node:assert/strict';
import { PendingStore, STORAGE_KEY } from '../../src/v1/pending.js';
import { ids, uid } from './fixtures.mjs';
function storage() { const values = new Map(); return { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }; }
const body = () => ({ draft_id: ids.draft, expected_revision: '9007199254740993' });
test('reload retains exact key/body, and only explicit completion removes it', () => {
  const disk = storage(); const first = new PendingStore(disk, () => uid(30)); const entry = first.prepare(ids.actor, 'confirm_criteria', ids.project, body());
  const reloaded = new PendingStore(disk, () => uid(31)); assert.deepEqual(reloaded.prepare(ids.actor, 'confirm_criteria', ids.project, body()), entry); reloaded.complete(entry); assert.deepEqual(reloaded.all(ids.actor), []);
});
test('changing content while outcome unknown does not mint a new request', () => {
  const pending = new PendingStore(storage(), () => uid(30)); const entry = pending.prepare(ids.actor, 'confirm_criteria', ids.project, body());
  assert.throws(() => pending.prepare(ids.actor, 'confirm_criteria', ids.project, { ...body(), expected_revision: '9007199254740994' })); assert.deepEqual(pending.all(ids.actor), [entry]);
});
test('equivalent key order reuses the same request', () => { const pending = new PendingStore(storage()); const entry = pending.prepare(ids.actor, 'confirm_criteria', ids.project, body()); assert.deepEqual(pending.prepare(ids.actor, 'confirm_criteria', ids.project, { expected_revision: body().expected_revision, draft_id: ids.draft }), entry); });
test('same actor reauth retains; different actor and explicit logout purge', () => {
  const pending = new PendingStore(storage()); pending.prepare(ids.actor, 'confirm_criteria', ids.project, body()); pending.actor(ids.actor); assert.equal(pending.all(ids.actor).length, 1); pending.actor(uid(88)); assert.equal(pending.all(ids.actor).length, 0);
  pending.prepare(uid(88), 'retry_review', ids.project, {}, ids.run); pending.clearAll(); assert.deepEqual(pending.all(uid(88)), []);
});
test('only operation IDs and revision can be persisted, never credentials/results/files', () => {
  const pending = new PendingStore(storage()); for (const extra of [{ password: 'secret' }, { results: [] }, { image: {} }, { csrf: 'token' }]) assert.throws(() => pending.prepare(ids.actor, 'confirm_criteria', ids.project, { ...body(), ...extra }));
});
test('storage failures preserve memory and expose nonpersistent status', () => {
  const pending = new PendingStore({ getItem() { throw Error(); }, setItem() { throw Error(); }, removeItem() { throw Error(); } }); const entry = pending.prepare(ids.actor, 'confirm_criteria', ids.project, body()); assert.equal(pending.persistent, false); assert.deepEqual(pending.all(ids.actor), [entry]);
});
test('corrupt storage is rejected and returned objects cannot mutate frozen request', () => {
  const disk = storage(); disk.setItem(STORAGE_KEY, '{broken'); const pending = new PendingStore(disk); assert.deepEqual(pending.all(ids.actor), []); const entry = pending.prepare(ids.actor, 'confirm_criteria', ids.project, body()); entry.body.expected_revision = '99'; assert.equal(pending.all(ids.actor)[0].body.expected_revision, body().expected_revision);
});
test('new review and retry keep different operations and exact source run', () => {
  const pending = new PendingStore(storage()); pending.prepare(ids.actor, 'create_review', ids.project, { project_id: ids.project, image_id: ids.image, criteria_version_id: ids.version }); pending.prepare(ids.actor, 'retry_review', ids.project, {}, ids.run); assert.equal(pending.all(ids.actor).length, 2);
  assert.throws(() => pending.prepare(ids.actor, 'create_review', ids.project, { project_id: uid(99), image_id: ids.image, criteria_version_id: ids.version }));
});
