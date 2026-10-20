import test from 'node:test';
import assert from 'node:assert/strict';
import { PollLoop } from '../../src/v1/polling.js';
const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };
function harness(read) {
  const tasks = new Map(); const values = [], errors = [], delays = []; let number = 0;
  const loop = new PollLoop({ read, value: value => values.push(value), error: value => errors.push(value), setTimer(callback, delay) { delays.push(delay); tasks.set(++number, callback); return number; }, clearTimer(id) { tasks.delete(id); } });
  return { loop, values, errors, delays, next: async () => { const task = tasks.entries().next().value; assert.ok(task); tasks.delete(task[0]); task[1](); await flush(); }, tasks };
}
test('one unfinished GET prevents overlapping polls and schedules only after completion', async () => {
  let resolve; let calls = 0; const h = harness(() => { calls++; return new Promise(done => { resolve = done; }); }); h.loop.start(); h.loop.refresh(); h.loop.refresh(); assert.equal(calls, 1); assert.equal(h.tasks.size, 0); resolve({ status: 'running' }); await flush(); assert.equal(h.tasks.size, 1); assert.equal(h.delays[0], 0); h.loop.stop();
});
test('network failures back off 2/4/8/15s, recovery resets and hidden pages slow down', async () => {
  let fail = true; const h = harness(async () => { if (fail) throw Error('offline'); return { status: 'running' }; }); h.loop.start(); await flush(); for (let i = 0; i < 4; i++) await h.next(); assert.deepEqual(h.delays, [2000, 4000, 8000, 15000, 15000]); fail = false; await h.next(); assert.equal(h.delays.at(-1), 2000); h.loop.hidden = () => true; await h.next(); assert.equal(h.delays.at(-1), 15000); h.loop.stop();
});
test('all terminal server states stop without inferring a verdict', async () => { for (const status of ['completed', 'failed', 'imported']) { const h = harness(async () => ({ status })); h.loop.start(); await flush(); assert.equal(h.loop.active, false); assert.equal(h.tasks.size, 0); assert.deepEqual(h.values, [{ status }]); } });
test('permanent 401/403/404/422 errors stop GET polling', async () => { for (const status of [401, 403, 404, 422]) { const h = harness(async () => { throw Object.assign(Error(), { status }); }); h.loop.start(); await flush(); assert.equal(h.tasks.size, 0); assert.equal(h.errors.length, 1); } });
test('stop invalidates late response even if fetch ignores abort', async () => { let resolve; let signal; const h = harness(input => { signal = input; return new Promise(done => { resolve = done; }); }); h.loop.start(); h.loop.stop(); assert.equal(signal.aborted, true); resolve({ status: 'completed' }); await flush(); assert.deepEqual(h.values, []); assert.equal(h.tasks.size, 0); });
test('restart cannot be overwritten by a stale request from a different run', async () => { const resolves = []; const h = harness(() => new Promise(done => resolves.push(done))); h.loop.start(); h.loop.start(); resolves[1]({ status: 'running', id: 'current' }); await flush(); resolves[0]({ status: 'completed', id: 'old' }); await flush(); assert.deepEqual(h.values, [{ status: 'running', id: 'current' }]); h.loop.stop(); });
