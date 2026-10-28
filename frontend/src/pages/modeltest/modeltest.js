import { compileAgents } from './modeltestAgents.js';
import { EXTRA_STAGES, GENERAL, registration, MODEL_STATUS } from './modeltestCatalog.js';

export const CONTRACT = 'modeltest.v1';
export const FILE_TYPES = {
  image: ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'],
  video: ['.mp4', '.mov', '.webm', '.mkv', '.avi', '.m4v'],
  document: ['.pdf', '.docx', '.doc', '.hwp', '.hwpx', '.txt', '.md'],
  reference: ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'],
};
export const FILE_LIMITS = { image: 20 * 1024 ** 2, video: 500 * 1024 ** 2, document: 20 * 1024 ** 2, reference: 20 * 1024 ** 2 };
export const STAGES = [
  { id: 'conditions', title: '문서에서 조건 추출', description: '발주서·계약서 → 검수 조건', icon: 'file' },
  { id: 'logo_extract', title: '로고 추출', description: '프레임 이미지 → 로고 영역', icon: 'search', video: true },
  { id: 'text_extract', title: '문구 추출', description: '이미지 → 문구와 위치', icon: 'list' },
  { id: 'visual_extract', title: '시각 요소 추출', description: '이미지 → 객체·색상·배치', icon: 'image' },
  { id: 'logo_similarity', title: '로고 이미지 유사도', description: '로고 크롭 ↔ 모든 원본 로고 · SigLIP2', icon: 'search', video: true, workflowOnly: true, needs: 'logo_extract' },
  { id: 'candidate_filter', title: '후보 필터', description: '각 로고별 최소 유사도 → 상위 N개', icon: 'list', video: true, workflowOnly: true, needs: 'logo_similarity' },
  { id: 'logo_compare', title: '원본 로고와 대조', description: '추출한 로고 ↔ 레퍼런스 이미지', icon: 'search', video: true, needs: 'logo_extract' },
  { id: 'text_compare', title: '조건과 문구 대조', description: '검수 조건 ↔ 추출한 문구', icon: 'check', needs: 'text_extract' },
  { id: 'visual_compare', title: '조건과 시각 요소 대조', description: '검수 조건 ↔ 추출한 시각 요소', icon: 'check', needs: 'visual_extract' },
  { id: 'text_generate', title: '모델 응답 생성', description: '선행 단계 결과와 지시 → 모델 응답', icon: 'file', agentOnly: true },
  ...EXTRA_STAGES,
];
export const STATUS_LABELS = { queued: '대기', running: '실행 중', completed: '완료', failed: '실패', cancelled: '취소', skipped: '제외' };
export const TERMINAL = ['completed', 'failed', 'cancelled'];
export function preparationLabel(value) {
  return ({ connecting: 'SSH 연결 중', uploading: 'Pod에 파일 전송 중', starting: 'Pod 실행 준비 중', ready: '검수 시작 중' })[value?.phase] || '실행 준비 중';
}
export const SIGLIP2 = 'google/siglip2-base-patch16-224';
export const MODEL_OPTIONS = [
  { id: 'PaddlePaddle/PaddleOCR-VL-1.6-GGUF', stages: ['text_extract'], description: 'OCR 문구 추출 전용. 첫 실행에 llama.cpp CUDA 빌드와 GGUF·mmproj 다운로드가 필요합니다. Pod에 git·C++ 컴파일러·nvcc가 있어야 합니다.' },
  { id: 'google/owlv2-base-patch16', stages: ['logo_extract'], description: '로고 영역을 찾아 좌표와 탐지 점수를 반환합니다.' },
  { id: SIGLIP2, stages: ['visual_extract', 'logo_similarity'], description: '시각 요소에서는 후보 문구, 로고 유사도에서는 원본 이미지와 대조합니다.' },
  { id: 'vismatch/xfeat', stages: ['logo_compare'], description: '잘라낸 로고와 원본의 특징점을 대조합니다. 특징점이 부족하면 판단을 보류합니다.' },
  { id: 'Qwen/Qwen3.5-4B', stages: ['conditions', 'logo_extract', 'text_extract', 'visual_extract', 'logo_compare', 'text_compare', 'visual_compare', 'text_generate'], description: '문서·이미지를 읽고 문구, 시각 설명, 조건 대조 결과를 생성합니다.' },
];

export function modelOptions(health) {
  return [...MODEL_OPTIONS, ...(health?.models || []).filter(model => !MODEL_OPTIONS.some(known => known.id === model.id)), ...(health?.registered || []).map(model => ({ id: `registered:${model.id}`, label: `${model.name} · ${MODEL_STATUS[model.status]}`, stages: model.tasks, description: [model.model_id, model.family, ...model.reasons].join(' · ') }))];
}

export function stagesFor(flow) { return STAGES.filter(stage => !stage.agentOnly && (flow === 'video' || !stage.video)); }
export function stageTitle(stage) { return stage?.name || STAGES.find(item => item.id === (stage?.kind || stage?.id))?.title || (stage?.id === 'frames' ? '프레임 이미지 추출' : stage?.id || ''); }
export function initialFlow(flow) {
  return {
    media: null, documents: [], references: [], requirements: '', requirementsOpen: false,
    sampling: { unit: 'frames', interval: '30', max_frames: '120' },
    workflow: { enabled: false, edges: [], maxParallel: 1, positions: {} },
    agentWorkflow: { enabled: false, agents: [], edges: [], positions: {}, maxParallel: 1 },
    stages: Object.fromEntries(stagesFor(flow).map(stage => [stage.id, {
      enabled: flow === 'image' || ['logo_extract', 'text_extract', 'visual_extract'].includes(stage.id), model: stage.id === 'logo_similarity' ? SIGLIP2 : '', labels: '', imageSimilarity: false, threshold: '', topK: '2',
    }])),
  };
}

export function workflowNodes(flow, state) {
  const nodes = stagesFor(flow).filter(stage => state.stages[stage.id].enabled && (!stage.workflowOnly || state.workflow.enabled));
  if (flow === 'video') nodes.splice(nodes[0]?.id === 'conditions' ? 1 : 0, 0, { id: 'frames', title: '프레임 이미지 추출', description: '영상 → 분석 프레임' });
  return nodes;
}

export function workflowError(flow, state) {
  if (!state.workflow.enabled) return '';
  const nodes = workflowNodes(flow, state); const parents = new Map(nodes.map(node => [node.id, new Set()]));
  if (![1, 2].includes(state.workflow.maxParallel)) return '동시 실행 수는 1 또는 2를 선택해 주세요.';
  for (const { source, target } of state.workflow.edges) {
    if (!parents.has(source) || !parents.has(target)) return '선택 해제한 단계의 연결을 삭제하거나 연결을 다시 배치해 주세요.';
    if (source === target || parents.get(target).has(source)) return '자기 연결 또는 중복 연결은 사용할 수 없습니다.';
    parents.get(target).add(source);
  }
  const ancestors = new Map(); const pending = new Map(parents);
  while (pending.size) {
    const ready = [...pending].filter(([, sources]) => [...sources].every(source => ancestors.has(source)));
    if (!ready.length) return '순환 연결이 있습니다. 되돌아오는 연결을 삭제해 주세요.';
    for (const [id, sources] of ready) {
      const all = new Set(sources); for (const source of sources) for (const parent of ancestors.get(source)) all.add(parent);
      ancestors.set(id, all); pending.delete(id);
    }
  }
  for (const node of nodes) {
    const required = node.needs ? [node.needs] : [];
    if (flow === 'video' && !['conditions', 'frames'].includes(node.id)) required.push('frames');
    if (['text_compare', 'visual_compare'].includes(node.id) && parents.has('conditions')) required.push('conditions');
    if (node.id === 'logo_compare' && parents.has('candidate_filter')) required.push('candidate_filter');
    const missing = required.filter(id => !ancestors.get(node.id).has(id));
    if (missing.length) return `${node.title}: ${missing.map(id => STAGES.find(stage => stage.id === id)?.title || '프레임 이미지 추출').join(', ')}에서 이어지는 연결이 필요합니다.`;
  }
  if (parents.has('candidate_filter') && !parents.has('logo_compare')) return '후보 필터를 원본 로고와 대조 단계에 연결해 주세요.';
  if (parents.has('logo_similarity') && state.stages.logo_compare.imageSimilarity) return '별도 유사도 노드를 사용할 때는 XFeat의 SigLIP2 추가 계산을 해제해 주세요.';
  return '';
}

export function workflowPreset(flow, state, preset = 'serial') {
  const stages = { ...state.stages };
  if (flow === 'video' && preset !== 'serial') {
    stages.logo_extract = { ...stages.logo_extract, enabled: true, model: stages.logo_extract.model || 'google/owlv2-base-patch16' };
    stages.logo_similarity = { ...stages.logo_similarity, enabled: true, model: SIGLIP2 };
    stages.candidate_filter = { ...stages.candidate_filter, enabled: preset === 'filtered', topK: stages.candidate_filter.topK || '2' };
    stages.logo_compare = { ...stages.logo_compare, enabled: true, model: 'vismatch/xfeat', imageSimilarity: false };
  }
  const next = { ...state, stages, workflow: { ...state.workflow, enabled: true, maxParallel: preset === 'parallel' ? 2 : 1 } };
  const nodes = workflowNodes(flow, next); let edges;
  if (preset === 'serial') edges = nodes.slice(1).map((node, index) => ({ source: nodes[index].id, target: node.id }));
  else {
    const ids = new Set(nodes.map(node => node.id)); edges = [];
    const add = (source, target) => { if (ids.has(source) && ids.has(target)) edges.push({ source, target }); };
    for (const id of ['logo_extract', 'text_extract', 'visual_extract']) add('frames', id);
    add('logo_extract', 'logo_similarity');
    if (preset === 'filtered') { add('logo_similarity', 'candidate_filter'); add('candidate_filter', 'logo_compare'); }
    else add('logo_extract', 'logo_compare');
    for (const id of ['text', 'visual']) { add(`${id}_extract`, `${id}_compare`); add('conditions', `${id}_compare`); }
  }
  next.workflow.edges = edges;
  return next;
}

export function parseModel(value) {
  let id = value.trim(); let revision;
  if (/^https?:\/\//i.test(id)) {
    let url;
    try { url = new URL(id); } catch { throw new Error('올바른 Hugging Face 모델 링크를 입력해 주세요.'); }
    if (url.protocol !== 'https:' || !['huggingface.co', 'hf.co'].includes(url.hostname) || url.username || url.password || url.port) {
      throw new Error('https://huggingface.co/소유자/모델명 링크를 입력해 주세요.');
    }
    const parts = url.pathname.replace(/\/$/, '').split('/').slice(1);
    if (parts.length === 4 && parts[2] === 'tree') {
      try { revision = decodeURIComponent(parts[3]); } catch { throw new Error('모델 revision을 확인해 주세요.'); }
      if (!/^[\w./-]{1,128}$/.test(revision)) throw new Error('모델 revision을 확인해 주세요.');
    } else if (parts.length !== 2) throw new Error('모델 저장소 또는 /tree/revision 링크를 입력해 주세요.');
    id = parts.slice(0, 2).join('/');
  }
  const parts = id.split('/');
  if (parts.length !== 2) throw new Error('혼동을 방지하기 위해 소유자/모델명을 함께 입력해 주세요.');
  if (['datasets', 'spaces'].includes(parts[0]) || parts.some(part => !/^[\w](?:[\w.-]{0,94}[\w])?$/.test(part) || part.includes('..') || part.includes('--') || part.endsWith('.git'))) {
    throw new Error('Hugging Face 모델 저장소의 소유자/모델명을 확인해 주세요.');
  }
  return { model_id: id, ...(revision ? { revision } : {}) };
}

export function parseStageModel(stageId, value, health) {
  if (GENERAL.includes(stageId)) return {};
  if (stageId === 'candidate_filter') {
    const threshold = value.threshold?.trim() ? Number(value.threshold) : null;
    const top_k = value.topK?.trim() ? Number(value.topK) : null;
    if (threshold === null && top_k === null) throw new Error('최소 유사도 또는 상위 후보 수를 입력해 주세요.');
    if (threshold !== null && (!Number.isFinite(threshold) || threshold < -1 || threshold > 1)) throw new Error('최소 코사인 유사도는 -1~1 사이여야 합니다.');
    if (top_k !== null && (!Number.isInteger(top_k) || top_k < 1 || top_k > 10)) throw new Error('상위 후보 수는 1~10 사이의 정수여야 합니다.');
    return { ...(threshold !== null ? { threshold } : {}), ...(top_k !== null ? { top_k } : {}) };
  }
  if (value.model.startsWith('registered:')) {
    const model = registration(value.model, health);
    if (!model) throw new Error('등록 모델이 삭제됐거나 목록을 아직 읽지 못했습니다. 모델 목록을 새로 읽어 주세요.');
    if (!['unverified', 'verified'].includes(model.status)) throw new Error(model.reasons.join(' · '));
    if (!model.tasks.includes(stageId)) throw new Error('등록 모델이 지원하지 않는 작업 유형입니다.');
    if (value.modelFingerprint && value.modelFingerprint !== model.fingerprint) throw new Error('등록 모델 설정이 바뀌었습니다. 모델을 다시 선택하고 임계값을 확인해 주세요.');
    if (health.ready && health.registry_version !== 1) throw new Error('등록 모델 실행을 지원하는 SSH 실행기에 다시 연결해 주세요.');
    return { model_id: model.model_id, revision: model.resolved_revision, registered_model: model.id, model_fingerprint: model.fingerprint };
  }
  const parsed = parseModel(value.model);
  if (stageId === 'logo_similarity' && parsed.model_id !== SIGLIP2) throw new Error('로고 이미지 유사도에는 SigLIP2를 선택해 주세요.');
  const profile = modelOptions(health).find(model => model.id === parsed.model_id);
  if (!profile) throw new Error('Hugging Face 모델 추가에서 이 모델의 실행 계열을 먼저 등록해 주세요.');
  if (profile && !profile.stages.includes(stageId)) {
    const titles = profile.stages.map(id => STAGES.find(stage => stage.id === id)?.title || id);
    throw new Error(`이 모델은 ${titles.join(', ')} 단계에서 사용해 주세요.`);
  }
  if (parsed.model_id === SIGLIP2 && stageId === 'visual_extract') {
    const labels = (value.labels || '').split(/\r?\n/).map(label => label.trim()).filter(Boolean);
    if (!labels.length || labels.length > 32 || labels.some(label => label.length > 200)) throw new Error('비교할 후보 문구를 한 줄에 하나씩, 1~32개 입력해 주세요. 문구당 최대 200자입니다.');
    if (new Set(labels).size !== labels.length) throw new Error('후보 문구가 중복됐습니다.');
    return { ...parsed, labels };
  }
  return { ...parsed, ...(stageId === 'logo_compare' && parsed.model_id === 'vismatch/xfeat' && value.imageSimilarity ? { image_similarity: true } : {}) };
}

export function parseEndpoint(value) {
  let url;
  try { url = new URL(value.trim()); } catch { throw new Error('Pod의 HTTP 서비스 주소를 입력해 주세요.'); }
  const pod = /^[a-z0-9-]+\.proxy\.runpod\.net$/.test(url.hostname) && url.protocol === 'https:' && !url.port;
  const local = ['localhost', '127.0.0.1'].includes(url.hostname) && ['http:', 'https:'].includes(url.protocol);
  if ((!pod && !local) || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) {
    throw new Error('RunPod HTTPS 프록시 주소 또는 로컬 HTTP 주소만 입력해 주세요. 경로·쿼리·인증정보는 제외합니다.');
  }
  return url.origin;
}

export function allowedTypes(kind, health) { return health ? FILE_TYPES[kind].filter(ext => health.formats[kind].includes(ext)) : FILE_TYPES[kind]; }
export function fileLimit(kind, health) { return Math.min(FILE_LIMITS[kind], health?.limits?.[`${kind}_bytes`] ?? FILE_LIMITS[kind]); }
export function formatBytes(bytes) { return `${(bytes / 1024 ** 2).toFixed(bytes >= 10 * 1024 ** 2 ? 0 : 1)} MiB`; }
export function fileError(file, kind, health) {
  if (!file || !Number.isFinite(file.size) || file.size <= 0) return '빈 파일은 첨부할 수 없습니다.';
  const extension = `.${file.name.split('.').pop().toLowerCase()}`;
  if (!allowedTypes(kind, health).includes(extension)) return `${file.name}: ${health ? '연결된 Pod에서 지원하지 않는' : '지원하지 않는'} 파일 형식입니다.`;
  if (file.size > fileLimit(kind, health)) return `${file.name}: 파일당 ${formatBytes(fileLimit(kind, health))} 이하로 첨부해 주세요.`;
  return '';
}

export function validateHealth(body) {
  const bad = () => { throw new Error('모델 테스트 API 규격이 맞지 않습니다. Pod에 modeltest.v1 API가 필요합니다.'); };
  if (!body || body.contract !== CONTRACT || typeof body.ready !== 'boolean' || !Array.isArray(body.stages) || !body.stages.every(id => typeof id === 'string')) bad();
  for (const kind of Object.keys(FILE_TYPES)) {
    if (!Array.isArray(body.formats?.[kind]) || !body.formats[kind].every(ext => typeof ext === 'string') || !Number.isSafeInteger(body.limits?.[`${kind}_bytes`]) || body.limits[`${kind}_bytes`] < 0) bad();
  }
  if (!Number.isSafeInteger(body.limits?.total_bytes) || body.limits.total_bytes <= 0) bad();
  if (body.models !== undefined && (!Array.isArray(body.models) || body.models.some(model => typeof model?.id !== 'string' || !Array.isArray(model.stages) || !model.stages.every(id => typeof id === 'string')))) bad();
  return body;
}

export function validateRun(body, expectedId, manifest) {
  if (!body || body.contract !== CONTRACT || typeof body.id !== 'string' || !/^[\w-]{1,128}$/.test(body.id) || (expectedId && body.id !== expectedId) || !['queued', 'running', ...TERMINAL].includes(body.status)
    || !Array.isArray(body.stages) || body.stages.some(stage => !stage || typeof stage.id !== 'string' || !Object.hasOwn(STATUS_LABELS, stage.status))
    || new Set(body.stages.map(stage => stage.id)).size !== body.stages.length) {
    throw new Error('Pod의 실행 응답을 해석할 수 없습니다. API 규격과 작업 ID를 확인해 주세요.');
  }
  if (manifest) {
    const expected = [...manifest.stages.map(stage => stage.id), ...(manifest.flow === 'video' && manifest.purpose !== 'prepare' ? ['frames'] : [])];
    if (body.stages.length !== expected.length || body.stages.some(stage => !expected.includes(stage.id))
      || (body.status === 'completed' && body.stages.some(stage => stage.status !== 'completed'))) {
      throw new Error('요청한 단계와 Pod의 응답 단계가 다릅니다. 완료 상태와 단계별 결과를 확인해 주세요.');
    }
  }
  return body;
}

export function formErrors(flow, state, health) {
  const errors = [];
  if (!state.media) errors.push(flow === 'image' ? '검수할 이미지를 첨부해 주세요.' : '검수할 영상을 첨부해 주세요.');
  if (flow === 'image' && !state.documents.length) errors.push('이미지 검수에는 발주서·계약서 등 문서가 필요합니다.');
  const files = [[state.media, flow], ...state.documents.map(file => [file, 'document']), ...state.references.map(file => [file, 'reference'])].filter(([file]) => file);
  files.forEach(([file, kind]) => { const error = fileError(file, kind, health); if (error) errors.push(error); });
  if (state.documents.length > 10 || state.references.length > 10) errors.push('문서와 레퍼런스는 각각 최대 10개까지 첨부할 수 있습니다.');
  if (health && files.reduce((sum, [file]) => sum + file.size, 0) > health.limits.total_bytes) errors.push(`첨부 파일 합계는 ${formatBytes(health.limits.total_bytes)} 이하여야 합니다.`);
  const agentMode = state.agentWorkflow?.enabled;
  if (agentMode) { try { compileAgents(flow, state, health); } catch (error) { errors.push(error.message); } }
  const enabled = agentMode ? [] : stagesFor(flow).filter(stage => state.stages[stage.id].enabled && (!stage.workflowOnly || state.workflow.enabled));
  if (!agentMode && !enabled.length) errors.push('실행할 모델 단계를 하나 이상 선택해 주세요.');
  for (const stage of enabled) {
    if (stage.workflowOnly && !state.workflow.enabled) errors.push(`${stage.title}: 워크플로우 편집을 활성화해 주세요.`);
    try { parseStageModel(stage.id, state.stages[stage.id], health); } catch (error) { errors.push(`${stage.title}: ${error.message}`); }
    if (health && !health.stages.includes(stage.id)) errors.push(`${stage.title}: 연결된 Pod가 지원하지 않습니다.`);
    if (stage.id === 'conditions' && !state.documents.length) errors.push('조건 추출을 실행하려면 문서를 첨부해 주세요.');
    if (stage.needs && !state.stages[stage.needs].enabled) errors.push(`${stage.title}: 먼저 추출 단계도 선택해 주세요.`);
    if (['text_compare', 'visual_compare'].includes(stage.id) && !state.stages.conditions.enabled && !state.requirements.trim()) errors.push(`${stage.title}: 문서 조건 추출을 선택하거나 조건을 직접 입력해 주세요.`);
    if (['logo_compare', 'logo_similarity'].includes(stage.id) && !state.references.length) errors.push('로고 대조에는 원본 로고 레퍼런스가 필요합니다.');
  }
  if (!agentMode && state.workflow.enabled) {
    const graphError = workflowError(flow, state); if (graphError) errors.push(graphError);
    if (health && health.workflow?.version !== 1) errors.push('현재 연결은 워크플로우를 지원하지 않습니다. 백엔드 재시작 후 Pod에 다시 연결해 주세요.');
  }
  if (state.requirements.length > 12000) errors.push('직접 입력한 조건은 12,000자 이하여야 합니다.');
  if (flow === 'video') {
    const { unit, interval, max_frames: maxFrames } = state.sampling;
    const n = Number(interval); const max = Number(maxFrames);
    if (!['frames', 'seconds'].includes(unit) || !Number.isFinite(n) || n <= 0 || n > 3600 || (unit === 'frames' && !Number.isInteger(n))) errors.push('프레임 간격은 1~3,600의 정수, 시간 간격은 0 초과~3,600초로 입력해 주세요.');
    if (!Number.isInteger(max) || max < 1 || max > 1000) errors.push('최대 추출 수는 1~1,000장으로 입력해 주세요.');
  }
  return errors;
}

function fileInfo(file) { return { name: file.name, size: file.size, type: file.type }; }
export function makeManifest(flow, state, health) {
  const execution = state.agentWorkflow?.enabled ? compileAgents(flow, state, health) : {
    ...(state.workflow.enabled ? { workflow: { edges: state.workflow.edges, max_parallel: state.workflow.maxParallel } } : {}),
    stages: stagesFor(flow).filter(stage => state.stages[stage.id].enabled && (!stage.workflowOnly || state.workflow.enabled)).map(stage => ({ id: stage.id, ...parseStageModel(stage.id, state.stages[stage.id], health) })),
  };
  return {
    contract: CONTRACT, flow, requirements: state.requirements.trim(),
    ...execution,
    ...(flow === 'video' ? { sampling: { unit: state.sampling.unit, interval: Number(state.sampling.interval), max_frames: Number(state.sampling.max_frames) } } : {}),
    files: { media: fileInfo(state.media), documents: state.documents.map(fileInfo), references: state.references.map(fileInfo) },
  };
}
export function makeFormData(manifest, state) {
  const body = new FormData(); body.append('config', JSON.stringify(manifest)); if (state.media) body.append('media', state.media);
  state.documents.forEach(file => body.append('documents', file));
  state.references.forEach(file => body.append('references', file));
  return body;
}

export async function podRequest(endpoint, path, { token = '', signal, timeout = 20000, ...options } = {}) {
  const deadline = AbortSignal.timeout(timeout);
  let response;
  try {
    response = await fetch(`${parseEndpoint(endpoint)}/api/modeltest${path}`, {
      ...options, signal: signal ? AbortSignal.any([signal, deadline]) : deadline,
      credentials: 'omit', redirect: 'error', referrerPolicy: 'no-referrer',
      headers: { Accept: 'application/json', ...options.headers, ...(token.trim() ? { Authorization: `Bearer ${token.trim()}` } : {}) },
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new Error(deadline.aborted ? 'Pod 응답 시간이 초과됐습니다.' : 'Pod에 연결하지 못했습니다. 주소, 실행 상태와 CORS 허용 출처를 확인해 주세요.');
  }
  let body;
  try { body = await response.json(); } catch { throw new Error(`Pod가 JSON 응답을 반환하지 않았습니다. (HTTP ${response.status})`); }
  if (!response.ok) {
    const error = new Error(response.status === 401 || response.status === 403 ? 'Pod 인증 토큰 또는 접근 권한을 확인해 주세요.' : `Pod 요청이 실패했습니다. (HTTP ${response.status})`);
    error.status = response.status; throw error;
  }
  return body;
}

export async function sshRequest(path, { signal, timeout = 20000, ...options } = {}) {
  if (!/^\/api\/modeltest\/ssh\/(?:connections(?:\/[\w-]+(?:\/(?:health|prepare|runs)(?:\/[\w-]+(?:\/cancel|\/previews\/[\w.-]+)?)?)?)?|models(?:\/(?:inspect|[\w-]+))?)$/.test(path)) throw new Error('SSH 요청 경로가 올바르지 않습니다.');
  const deadline = AbortSignal.timeout(timeout); const combined = signal ? AbortSignal.any([signal, deadline]) : deadline;
  const headers = { Accept: 'application/json', 'X-Modeltest-Local': '1', ...options.headers };
  if (['POST', 'PUT', 'DELETE'].includes(options.method) && import.meta.env && import.meta.env.VITE_API_PROFILE === 'v1') {
    const { csrf } = await import('../../v1/api.js');
    headers['X-CSRFToken'] = (await csrf(combined)).csrf_token;
  }
  let response;
  try { response = await fetch(path, { ...options, headers, credentials: 'same-origin', signal: combined, redirect: 'error', cache: 'no-store' }); }
  catch (error) {
    if (signal?.aborted) throw error;
    throw new Error(deadline.aborted ? 'SSH 요청 시간이 초과됐습니다. 접수한 작업은 같은 요청으로 확인해 주세요.' : '노트북의 백엔드에 연결하지 못했습니다. 로컬 서버 실행 상태를 확인해 주세요.');
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(typeof body?.detail === 'string' ? body.detail : response.status === 404 ? '로컬 백엔드를 최신 코드로 실행해 주세요. SSH 기능은 로컬 개발 환경에서만 사용할 수 있습니다.' : `SSH 요청이 실패했습니다. (HTTP ${response.status})`);
    error.status = response.status; throw error;
  }
  if (!body) throw new Error('로컬 SSH 서버 응답을 확인하지 못했습니다.');
  return body;
}

export function modeltestRequest(connection, path, options = {}) {
  return connection.transport === 'ssh' ? sshRequest(`${connection.endpoint}${path}`, options) : podRequest(connection.endpoint, path, { token: connection.token, ...options });
}
