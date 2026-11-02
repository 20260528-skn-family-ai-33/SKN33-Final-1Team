import { useEffect, useRef, useState } from 'react';
import { initialFlow } from './modeltest.js';

const FIELDS = ['media', 'documents', 'references', 'requirements', 'requirementsOpen', 'sampling', 'stages', 'workflow', 'agentWorkflow', 'resize', 'referenceResize'];
const FLOWS = ['image', 'video', 'similarity'];
const initialFlows = () => Object.fromEntries(FLOWS.map(flow => [flow, initialFlow(flow)]));
let opening;
export function openDraftDB() {
  if (!opening) opening = new Promise((resolve, reject) => {
    const request = indexedDB.open('creative-checker-modeltest', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('draft');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => { opening = null; reject(request.error); };
    request.onblocked = () => reject(new Error('다른 탭의 저장소 연결을 닫고 다시 시도해 주세요.'));
  });
  return opening;
}

export function writeDraft(db, entries) {
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('draft', 'readwrite');
    const store = transaction.objectStore('draft');
    for (const [key, value] of entries) store.put(value, key);
    transaction.oncomplete = resolve;
    transaction.onabort = () => reject(transaction.error || new Error('저장이 취소됐습니다.'));
    transaction.onerror = () => reject(transaction.error);
  });
}

export function readDraft(db) {
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('draft', 'readonly'); const data = {};
    const keys = ['flow', 'run', ...FLOWS.flatMap(flow => FIELDS.map(field => `${flow}.${field}`))];
    for (const key of keys) {
      const request = transaction.objectStore('draft').get(key);
      request.onsuccess = () => { data[key] = request.result; };
    }
    transaction.oncomplete = () => resolve(data);
    transaction.onerror = () => reject(transaction.error);
  });
}

function restoredFlow(flow, data) {
  const state = initialFlow(flow);
  for (const field of FIELDS) {
    const value = data[`${flow}.${field}`];
    if (field === 'media' && value instanceof File) state.media = value;
    else if (['documents', 'references'].includes(field) && Array.isArray(value)) state[field] = value.filter(file => file instanceof File);
    else if (['requirements', 'requirementsOpen'].includes(field) && typeof value === typeof state[field]) state[field] = value;
    else if (field === 'sampling' && value && ['frames', 'seconds'].includes(value.unit)) state.sampling = { ...state.sampling, ...value };
    else if (['resize', 'referenceResize'].includes(field) && flow === 'similarity' && value && typeof value.width === 'string' && typeof value.height === 'string') state[field] = { width: value.width, height: value.height, mode: value.mode === 'percent' ? 'percent' : 'pixels', percent: typeof value.percent === 'string' ? value.percent : '' };
    else if (field === 'agentWorkflow' && value && Array.isArray(value.agents) && Array.isArray(value.edges) && [1, 2].includes(value.maxParallel)) state.agentWorkflow = { ...state.agentWorkflow, ...value };
    else if (field === 'workflow' && value && typeof value.enabled === 'boolean' && [1, 2].includes(value.maxParallel) && Array.isArray(value.edges)) {
      state.workflow = { enabled: value.enabled, maxParallel: value.maxParallel,
        edges: value.edges.filter(edge => edge && typeof edge.source === 'string' && typeof edge.target === 'string'), positions: value.positions || {} };
    }
    else if (field === 'stages' && value) {
      for (const id of Object.keys(state.stages)) {
        const stage = value[id];
        if (stage && typeof stage.enabled === 'boolean' && typeof stage.model === 'string') {
          state.stages[id] = { ...state.stages[id], enabled: stage.enabled, model: stage.model, modelFingerprint: typeof stage.modelFingerprint === 'string' ? stage.modelFingerprint : '', labels: typeof stage.labels === 'string' ? stage.labels : '', imageSimilarity: stage.imageSimilarity === true,
            imageSimilarityModel: typeof stage.imageSimilarityModel === 'string' ? stage.imageSimilarityModel : state.stages[id].imageSimilarityModel,
            filterScope: typeof stage.filterScope === 'string' ? stage.filterScope : 'references_per_crop',
            threshold: typeof stage.threshold === 'string' ? stage.threshold : '', topK: typeof stage.topK === 'string' ? stage.topK : '2', promptOverride: typeof stage.promptOverride === 'string' ? stage.promptOverride : null };
        }
      }
    }
  }
  return state;
}

export function useModeltestDraft() {
  const [flow, setFlow] = useState('image');
  const [flows, setFlows] = useState(initialFlows);
  const [ready, setReady] = useState(false); const [savedRun, setSavedRun] = useState(null);
  const [storage, setStorage] = useState('저장된 첨부·설정을 불러오는 중입니다.');
  const [saving, setSaving] = useState(false);
  const previous = useRef(null); const dbRef = useRef(null); const sequence = useRef(0);
  useEffect(() => {
    let live = true;
    openDraftDB().then(async db => {
      const data = await readDraft(db);
      if (!live) return;
      dbRef.current = db;
      const next = Object.fromEntries(FLOWS.map(flow => [flow, restoredFlow(flow, data)]));
      const nextFlow = FLOWS.includes(data.flow) ? data.flow : 'image';
      previous.current = { flow: nextFlow, flows: next };
      setFlow(nextFlow); setFlows(next); setSavedRun(data.run || null); setReady(true);
      setStorage('첨부·모델 설정은 이 브라우저에 자동 저장됩니다.');
    }).catch(() => {
      if (live) { setReady(true); setStorage('브라우저 저장소를 열지 못했습니다. 현재 입력은 새로고침하면 사라집니다.'); }
    });
    return () => { live = false; };
  }, []);
  useEffect(() => {
    if (!ready || !dbRef.current) return;
    const old = previous.current; const entries = [];
    if (old?.flow !== flow) entries.push(['flow', flow]);
    for (const name of FLOWS) for (const field of FIELDS) {
      // Store large File objects only when attachments change, not on every keystroke.
      if (old?.flows[name][field] !== flows[name][field]) entries.push([`${name}.${field}`, flows[name][field]]);
    }
    if (!entries.length) return;
    previous.current = { flow, flows }; const id = ++sequence.current; setSaving(true);
    writeDraft(dbRef.current, entries).then(() => {
      if (id === sequence.current) setStorage('첨부·모델 설정 저장 완료 · 이 브라우저에서 복원됩니다.');
    }).catch(() => {
      if (id === sequence.current) { previous.current = null; setStorage('저장 공간 부족 또는 브라우저 제한으로 저장하지 못했습니다. 새로고침하면 최근 변경이 사라질 수 있습니다.'); }
    }).finally(() => { if (id === sequence.current) setSaving(false); });
  }, [flow, flows, ready]);

  async function rememberRun(context, jobId = context.requestId) {
    const record = { transport: context.transport, endpoint: context.endpoint, requestId: context.requestId, jobId, manifest: context.manifest };
    setSavedRun(record);
    try { await writeDraft(await openDraftDB(), [['run', record]]); }
    catch { setStorage('실행 ID를 저장하지 못했습니다. 결과를 내려받거나 요청 ID를 따로 기록해 주세요.'); }
  }
  function clearDraft() {
    setFlows(initialFlows()); setFlow('image');
  }
  return { flow, setFlow, flows, setFlows, ready, savedRun, storage, saving, rememberRun, clearDraft };
}
