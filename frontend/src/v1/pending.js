import { id, KEY, revision, object } from './contracts.js';
export const STORAGE_KEY = 'cc.v1.pending.v1';
const keys = (value, allowed) => object(value) && Object.keys(value).sort().join(',') === [...allowed].sort().join(',');
export function validEntry(entry) {
  if (!object(entry) || entry.version !== 1 || !id(entry.actor_id) || !id(entry.project_id) || !KEY.test(entry.key || '') || !object(entry.body)) return false;
  if (!keys(entry, ['version', 'actor_id', 'project_id', 'operation', 'key', 'body', 'source_run_id'])) return false;
  if (entry.operation === 'confirm_criteria') return entry.source_run_id === null && keys(entry.body, ['draft_id', 'expected_revision']) && id(entry.body.draft_id) && revision(entry.body.expected_revision);
  if (entry.operation === 'create_review') return entry.source_run_id === null && keys(entry.body, ['project_id', 'image_id', 'criteria_version_id']) && Object.values(entry.body).every(id) && entry.body.project_id === entry.project_id;
  if (entry.operation === 'retry_review') return id(entry.source_run_id) && keys(entry.body, []);
  return false;
}
export function scope(entry) { return `${entry.actor_id}:${entry.operation}:${entry.project_id}:${entry.operation === 'confirm_criteria' ? entry.body.draft_id : entry.operation === 'retry_review' ? entry.source_run_id : entry.body.criteria_version_id}`; }
export class PendingStore {
  constructor(storage = null, random = () => crypto.randomUUID()) {
    this.storage = storage; this.random = random; this.entries = []; this.persistent = !!storage;
    try { const value = JSON.parse(storage?.getItem(STORAGE_KEY) || '[]'); if (Array.isArray(value) && value.length <= 20 && value.every(validEntry)) this.entries = value; else this.flush(); }
    catch { this.persistent = false; }
  }
  flush() { try { if (!this.storage) throw new Error(); if (this.entries.length) this.storage.setItem(STORAGE_KEY, JSON.stringify(this.entries)); else this.storage.removeItem(STORAGE_KEY); } catch { this.persistent = false; } }
  actor(actor) { this.entries = this.entries.filter(entry => entry.actor_id === actor); this.flush(); }
  clearAll() { this.entries = []; this.flush(); }
  all(actor, project = null) { return this.entries.filter(entry => entry.actor_id === actor && (!project || entry.project_id === project)).map(entry => structuredClone(entry)); }
  prepare(actor, operation, project, body, source = null) {
    const candidate = { version: 1, actor_id: actor, operation, project_id: project, body: structuredClone(body), key: this.random(), source_run_id: source };
    if (!validEntry(candidate)) throw new Error('Invalid pending request');
    const existing = this.entries.find(entry => scope(entry) === scope(candidate));
    if (existing) {
      if (JSON.stringify(existing.body, Object.keys(existing.body).sort()) !== JSON.stringify(candidate.body, Object.keys(candidate.body).sort())) throw new Error('이전에 보낸 요청의 결과를 먼저 확인해 주세요. 새 키로 자동 제출하지 않습니다.');
      return structuredClone(existing);
    }
    if (this.entries.length >= 20) throw new Error('미확인 요청이 많습니다. 기존 요청을 먼저 확인해 주세요.');
    this.entries.push(candidate); this.flush(); return structuredClone(candidate);
  }
  complete(entry) { this.entries = this.entries.filter(row => row.key !== entry.key || row.actor_id !== entry.actor_id || row.operation !== entry.operation); this.flush(); }
}
export function browserPending() { try { return new PendingStore(window.sessionStorage); } catch { return new PendingStore(); } }
