import { STAGES, parseStageModel } from './modeltest.js';
import { EXTRA_STAGES, GENERAL, INPUTS, acceptsInput, paramsFor, registration } from './modeltestCatalog.js';
import { omitPosition } from './modeltestBoard.js';

export function cleanAgentBindings(board) {
  const owners = new Map(board.agents.flatMap(agent => agent.nodes.map(node => [node.id, agent])));
  let outer; const inner = new Map();
  try { outer = ancestry(board.agents.map(agent => agent.id), board.edges, '에이전트 연결'); } catch { /* The editor still shows cycle errors. */ }
  for (const agent of board.agents) {
    try { inner.set(agent.id, ancestry(agent.nodes.map(node => node.id), agent.edges, agent.name)); } catch { /* Retain existing references until the cycle is fixed. */ }
  }
  return { ...board, agents: board.agents.map(agent => ({ ...agent, nodes: agent.nodes.map(node => ({ ...node,
    bindings: Object.fromEntries(Object.entries(node.bindings || {}).filter(([, id]) => {
      if (!id) return true;
      const owner = owners.get(id); if (!owner || id === node.id) return false;
      return owner.id === agent.id ? !inner.has(agent.id) || inner.get(agent.id).get(node.id).has(id) : !outer || outer.get(agent.id).has(owner.id);
    })),
  })) })) };
}

export function removeAgentNode(board, agentId, nodeId) {
  const agents = nodeId ? board.agents.map(agent => agent.id !== agentId ? agent : { ...agent, nodes: agent.nodes.filter(node => node.id !== nodeId),
    edges: agent.edges.filter(edge => edge.source !== nodeId && edge.target !== nodeId), positions: omitPosition(agent.positions, nodeId) }) : board.agents.filter(agent => agent.id !== agentId);
  return cleanAgentBindings({ ...board, agents, ...(nodeId ? {} : { edges: board.edges.filter(edge => edge.source !== agentId && edge.target !== agentId), positions: omitPosition(board.positions, agentId) }) });
}

export function duplicateAgentStep(node) {
  return { ...structuredClone(node), id: `step-${crypto.randomUUID()}`, name: `${node.name} 복사`.slice(0, 100), bindings: {} };
}

export function newAgent(name = '새 에이전트') {
  return { id: `agent-${crypto.randomUUID()}`, name, enabled: true, nodes: [], edges: [], positions: {} };
}
export function newAgentStep(kind = 'text_generate') {
  const model = EXTRA_STAGES.some(stage => stage.id === kind) ? '' : ({ logo_extract: 'google/owlv2-base-patch16', logo_similarity: 'google/siglip2-base-patch16-224', logo_compare: 'vismatch/xfeat', candidate_filter: '' })[kind] ?? 'Qwen/Qwen3.5-4B';
  return { id: `step-${crypto.randomUUID()}`, kind, name: STAGES.find(stage => stage.id === kind)?.title || kind, model,
    labels: '', prompt: '', threshold: '', topK: '2', imageSimilarity: false, bindings: {}, paramsText: '' };
}

export function agentTemplate(flow) {
  const definitions = [
    ['A01 업무 총괄', ['text_generate'], '요청의 목적과 필요한 검수 내용을 정리하세요. 실제 승인이나 상태 변경을 수행했다고 주장하지 마세요.'],
    ['A02 문서·기준 분석', ['conditions']], ['A03 근거 검색', []],
    ['A04 문구 검수', ['text_extract', 'text_compare']],
    ['A05 시각 요소 검수', flow === 'video' ? ['logo_extract', 'logo_similarity', 'candidate_filter', 'logo_compare'] : ['visual_extract', 'visual_compare']],
    ['A06 근거·결과 검증', ['text_generate'], '앞선 검수 결과의 근거 부족, 충돌, 미실행 항목을 확인하고 확인 필요 목록을 작성하세요.'],
    ['A07 보고서 작성', ['text_generate'], '검수 결과와 확인 필요 항목을 요약하고 수정 안내 초안을 작성하세요.'],
    ['A08 수정·재검수 관리', ['text_generate'], '이전 결과를 바탕으로 수정 요청 초안을 작성하세요. 최종 승인이나 외부 발송을 수행하지 마세요.'],
  ];
  const agents = definitions.map(([name, kinds, prompt], index) => {
    const agent = newAgent(name); agent.nodes = kinds.map(newAgentStep); agent.enabled = ![2, 7].includes(index);
    if (prompt) agent.nodes[0].prompt = prompt;
    agent.edges = agent.nodes.slice(1).map((node, i) => ({ source: agent.nodes[i].id, target: node.id }));
    return agent;
  });
  const edges = [[0, 1], [1, 2], [2, 3], [2, 4], [3, 5], [4, 5], [5, 6], [6, 7]].map(([a, b]) => ({ source: agents[a].id, target: agents[b].id }));
  const layout = [[24, 24], [364, 24], [704, 24], [24, 224], [24, 424], [364, 324], [704, 324], [704, 524]];
  return { enabled: true, agents, edges, positions: Object.fromEntries(agents.map((agent, index) => [agent.id, { x: layout[index][0], y: layout[index][1] }])), maxParallel: 1 };
}

function ancestry(ids, edges, label) {
  const parents = new Map(ids.map(id => [id, new Set()]));
  if (parents.size !== ids.length || !Array.isArray(edges)) throw new Error(`${label}: 식별자나 연결 목록을 확인해 주세요.`);
  for (const edge of edges) {
    if (!edge || !parents.has(edge.source) || !parents.has(edge.target) || edge.source === edge.target || parents.get(edge.target).has(edge.source)) throw new Error(`${label}: 존재하지 않는 단계·자기 연결·중복 연결입니다.`);
    parents.get(edge.target).add(edge.source);
  }
  const result = new Map(); const pending = new Map(parents);
  while (pending.size) {
    const ready = [...pending].filter(([, sources]) => [...sources].every(source => result.has(source)));
    if (!ready.length) throw new Error(`${label}: 순환 연결은 사용할 수 없습니다.`);
    for (const [id, sources] of ready) {
      const all = new Set(sources); for (const source of sources) for (const ancestor of result.get(source)) all.add(ancestor);
      result.set(id, all); pending.delete(id);
    }
  }
  return result;
}

export function compileAgents(flow, state, health) {
  const board = state.agentWorkflow;
  if (![1, 2].includes(board.maxParallel) || !board.agents.length || board.agents.length > 32) throw new Error('에이전트 1~32개와 동시 실행 수 1 또는 2를 설정해 주세요.');
  const agentAncestors = ancestry(board.agents.map(agent => agent.id), board.edges, '에이전트 연결');
  const agents = board.agents.filter(agent => agent.enabled); const stages = []; const edges = [];
  if (!agents.length) throw new Error('실행할 에이전트를 하나 이상 활성화해 주세요.');
  const boundaries = new Map();
  for (const agent of agents) {
    if (!agent.name.trim() || agent.name.length > 100 || !agent.nodes.length) throw new Error(`${agent.name || '에이전트'}: 이름과 실행할 단계를 추가해 주세요.`);
    ancestry(agent.nodes.map(node => node.id), agent.edges, agent.name);
    edges.push(...agent.edges);
    boundaries.set(agent.id, { roots: agent.nodes.filter(node => !agent.edges.some(edge => edge.target === node.id)), leaves: agent.nodes.filter(node => !agent.edges.some(edge => edge.source === node.id)) });
    for (const node of agent.nodes) {
      const kind = STAGES.find(stage => stage.id === node.kind);
      if (!kind || (flow === 'image' && kind.video)) throw new Error(`${agent.name}: 현재 검수 유형에서 사용할 수 없는 단계입니다.`);
      if (health && !health.stages.includes(node.kind)) throw new Error(`${agent.name}: 연결된 Pod가 ${kind.title} 단계를 지원하지 않습니다.`);
      if (!node.name.trim() || node.name.length > 100 || (node.prompt || '').length > 8000) throw new Error(`${agent.name}: 단계 이름은 1~100자, 모델 지시는 8,000자 이하여야 합니다.`);
      stages.push({ id: node.id, kind: node.kind, name: node.name.trim(), agent_id: agent.id, inputs: {},
        ...parseStageModel(node.kind, node, health), ...(node.paramsText?.trim() ? { params: paramsFor(node) } : {}), ...(node.kind !== 'candidate_filter' && !GENERAL.includes(node.kind) && node.prompt?.trim() ? { prompt: node.prompt.trim() } : {}) });
    }
  }
  if (stages.length > 64) throw new Error('한 번에 실행할 내부 단계는 최대 64개입니다.');
  for (const agent of agents) {
    const predecessors = agents.filter(other => agentAncestors.get(agent.id).has(other.id));
    const nearest = predecessors.filter(other => !predecessors.some(next => agentAncestors.get(next.id).has(other.id)));
    for (const before of nearest) for (const leaf of boundaries.get(before.id).leaves) for (const root of boundaries.get(agent.id).roots) edges.push({ source: leaf.id, target: root.id });
  }
  const ids = stages.map(stage => stage.id);
  if (flow === 'video') { ids.push('frames'); for (const stage of stages) if (!['conditions', 'text_generate'].includes(stage.kind)) edges.push({ source: 'frames', target: stage.id }); }
  const ancestors = ancestry(ids, edges, '모델 단계 연결');
  const original = new Map(agents.flatMap(agent => agent.nodes).map(node => [node.id, node]));
  const required = { logo_similarity: ['logo_extract'], candidate_filter: ['logo_similarity'], logo_compare: ['logo_extract'], text_compare: ['text_extract'], visual_compare: ['visual_extract'] };
  for (const stage of stages) {
    const roles = new Set(INPUTS[stage.kind] ? Object.keys(INPUTS[stage.kind]) : required[stage.kind] || []);
    if (['text_compare', 'visual_compare'].includes(stage.kind)) roles.add('conditions');
    if (stage.kind === 'logo_compare') roles.add('candidate_filter');
    for (const role of roles) {
      const candidates = stages.filter(source => acceptsInput(stage.kind, role, source.kind) && ancestors.get(stage.id).has(source.id));
      const closest = candidates.filter(source => !candidates.some(next => ancestors.get(next.id).has(source.id)));
      const selected = original.get(stage.id).bindings?.[role];
      if (selected) {
        if (!candidates.some(source => source.id === selected)) throw new Error(`${stage.name}: 선택한 ${STAGES.find(item => item.id === role)?.title} 입력이 선행 연결에 없습니다.`);
        stage.inputs[role] = selected;
      } else if (closest.length === 1) stage.inputs[role] = closest[0].id;
      else if (closest.length > 1) throw new Error(`${stage.name}: 같은 종류의 입력이 여러 개입니다. 단계 설정에서 사용할 입력을 지정해 주세요.`);
      else if ((required[stage.kind] || []).includes(role) || (role === 'conditions' && !state.requirements.trim()) || ['left', 'right', 'ocr', 'logo_extract'].includes(role) || (role === 'images' && stage.params?.image_source === 'crops')) throw new Error(`${stage.name}: ${STAGES.find(item => item.id === role)?.title || role}에서 이어지는 연결이 필요합니다.`);
    }
    if (stage.kind === 'vector_compare') {
      const inputs = ['left', 'right'].map(role => stages.find(item => item.id === stage.inputs[role]));
      if (!inputs[0]?.model_fingerprint || inputs[0].model_fingerprint !== inputs[1]?.model_fingerprint) throw new Error('벡터 비교에는 같은 모델·revision·처리 설정이 필요합니다.');
    }
    if (stage.kind === 'document_classify') {
      const source = original.get(stage.inputs.ocr);
      if (!['paddle_ocr', 'easyocr'].includes(registration(source?.model, health)?.family)) throw new Error('문서 분류에는 단어·좌표를 제공하는 PP-OCR 또는 EasyOCR 노드를 연결해 주세요.');
      if (stage.params?.image_source && stage.params.image_source !== 'media') throw new Error('문서 분류는 OCR과 같은 원본 이미지를 사용해야 합니다.');
    }
    if (stage.kind === 'rerank' && stage.params?.modality === 'image' && !registration(original.get(stage.id)?.model, health)?.modalities?.includes('image')) throw new Error('이 재정렬 모델은 이미지 후보를 지원하지 않습니다.');
  }
  if (stages.some(stage => stage.kind === 'conditions') && !state.documents.length) throw new Error('문서에서 조건을 추출하려면 발주서·계약서를 첨부해 주세요.');
  if (stages.some(stage => ['logo_compare', 'logo_similarity', 'phash', 'orb', 'sift', 'disk_lightglue'].includes(stage.kind)) && !state.references.length) throw new Error('로고 대조에는 원본 로고 레퍼런스가 필요합니다.');
  if (health && health.workflow?.agents !== true) throw new Error('현재 연결은 에이전트 보드를 지원하지 않습니다. 백엔드 재시작 후 Pod에 다시 연결해 주세요.');
  return { stages, workflow: { edges, max_parallel: board.maxParallel, agents: agents.map(({ id, name }) => ({ id, name: name.trim() })) } };
}
