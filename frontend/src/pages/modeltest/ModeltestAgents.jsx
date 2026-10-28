import React, { useLayoutEffect, useRef, useState } from 'react';
import { STAGES, MODEL_OPTIONS, modelOptions, parseModel, FILTER_SCOPES, SIGLIP2, SIGLIP2_MODELS } from './modeltest.js';
import { agentTemplate, compileAgents, newAgent, newAgentStep, cleanAgentBindings, removeAgentNode, duplicateAgentStep } from './modeltestAgents.js';
import { nodePosition } from './modeltestBoard.js';
import ModeltestPrompt from './ModeltestPrompt.jsx';
import ModeltestHelp from './ModeltestHelp.jsx';
import ModeltestInspector, { useInspector } from './ModeltestInspector.jsx';
import { WorkflowGraph } from './ModeltestWorkflow.jsx';
import { GENERAL, EXTRA_STAGES, INPUTS, acceptsInput, modelChoice, registration } from './modeltestCatalog.js';

function preventNameSubmit(event) { if (event.key === 'Enter') event.preventDefault(); }

export default function ModeltestAgents({ flow, state, update, disabled, health, onUndo, canUndo, onRedo, canRedo, confirmAction }) {
  const board = state.agentWorkflow; const [agentId, setAgentId] = useState(''); const [stepId, setStepId] = useState(''); const [newKind, setNewKind] = useState('text_generate');
  const [inspectorOpen, setInspectorOpen] = useInspector('creative-checker-agent-inspector');
  const selectStep = id => { setStepId(id); setInspectorOpen(true); };
  const selectAgent = id => { setAgentId(id); setStepId(''); setInspectorOpen(true); };
  const agent = board.agents.find(item => item.id === agentId) || board.agents[0];
  const step = agent?.nodes.find(item => item.id === stepId) || agent?.nodes[0];
  const agentName = useRef(null); const stepName = useRef(null); const [renameTarget, setRenameTarget] = useState('');
  useLayoutEffect(() => {
    if (!renameTarget) return;
    const input = renameTarget === 'step' ? stepName.current : agentName.current;
    input?.focus(); input?.select(); setRenameTarget('');
  }, [renameTarget, agent?.id, step?.id]);
  const kinds = STAGES.filter(kind => flow === 'video' || !kind.video);
  const change = patch => update({ agentWorkflow: patch.edges ? cleanAgentBindings({ ...board, ...patch }) : { ...board, ...patch } });
  const changeAgent = patch => { const next = { ...board, agents: board.agents.map(item => item.id === agent.id ? { ...item, ...patch } : item) }; update({ agentWorkflow: patch.edges ? cleanAgentBindings(next) : next }); };
  const changeStep = patch => change({ agents: board.agents.map(item => ({ ...item, nodes: item.nodes.map(node => node.id === step.id ? { ...node, ...patch } : ((patch.model !== undefined && patch.model !== step.model) || (patch.modelFingerprint !== undefined && patch.modelFingerprint !== step.modelFingerprint)) && node.kind === 'candidate_filter' ? { ...node, threshold: '' } : node) })) });
  let error = ''; try { compileAgents(flow, state, health); } catch (cause) { error = cause.message; }
  let modelId = ''; try { modelId = parseModel(step?.model || '').model_id; } catch { /* An unfinished model ID stays editable. */ }
  if (step?.model?.startsWith('registered:')) modelId = step.model;
  const registered = registration(step?.model, health);
  const models = modelOptions(health).filter(model => model.stages.includes(step?.kind));
  const profile = models.find(model => model.id === modelId);
  const modelWarning = registered?.reasons?.join(' · ') || (profile && (!MODEL_OPTIONS.some(model => model.id === profile.id) || profile.id === 'PaddlePaddle/PaddleOCR-VL-1.6-GGUF') ? profile.description : '');
  const inputRoles = INPUTS[step?.kind] ? Object.keys(INPUTS[step.kind]) : ({ logo_similarity: ['logo_extract'], candidate_filter: ['logo_similarity'], logo_compare: ['logo_extract', 'candidate_filter'], text_compare: ['text_extract', 'conditions'], visual_compare: ['visual_extract', 'conditions'] })[step?.kind] || [];
  function addAgent(point) { if (board.agents.length >= 32) return; const next = newAgent(); change({ agents: [...board.agents, next], ...(point ? { positions: { ...board.positions, [next.id]: point } } : {}) }); setAgentId(next.id); setStepId(''); }
  function removeAgent(id = agent.id) {
    const target = board.agents.find(item => item.id === id);
    if (!target || disabled) return;
    confirmAction({ title: '에이전트 삭제', message: `${target.name}와 내부 단계 ${target.nodes.length}개를 삭제할까요?`, confirmLabel: '삭제', danger: true }, () => { update({ agentWorkflow: removeAgentNode(board, id) }); setAgentId(''); setStepId(''); });
  }
  function addStep(kind = newKind, point) {
    if (board.agents.reduce((sum, item) => sum + item.nodes.length, 0) >= 64) return;
    const node = newAgentStep(kind); const previous = point ? null : step || agent.nodes.at(-1);
    changeAgent({ nodes: [...agent.nodes, node], edges: previous ? [...agent.edges, { source: previous.id, target: node.id }] : agent.edges, ...(point ? { positions: { ...agent.positions, [node.id]: point } } : {}) }); selectStep(node.id);
  }
  function removeStep(id = step.id) {
    update({ agentWorkflow: removeAgentNode(board, agent.id, id) }); setStepId('');
  }
  async function rename(id, internal = false) {
    if (disabled) return;
    if (document.fullscreenElement) await document.exitFullscreen();
    if (internal) selectStep(id); else selectAgent(id);
    setRenameTarget(internal ? 'step' : 'agent');
  }
  function applyTemplate() { update({ agentWorkflow: agentTemplate(flow) }); setAgentId(''); setStepId(''); }
  function duplicate(id) {
    if (board.agents.reduce((sum, item) => sum + item.nodes.length, 0) >= 64) return;
    const index = agent.nodes.findIndex(node => node.id === id); const node = duplicateAgentStep(agent.nodes[index]); const pos = nodePosition(agent.positions, id, index);
    changeAgent({ nodes: [...agent.nodes, node], positions: { ...agent.positions, [node.id]: { x: pos.x + 36, y: pos.y + 120 } } }); selectStep(node.id);
  }
  return <section className="mt-panel mt-agent-board" aria-label="에이전트 워크플로우 보드">
    <div className="mt-panel-heading"><div className="mt-title-help"><h2>에이전트 워크플로우 보드</h2><ModeltestHelp variant="dialog" label="에이전트 워크플로우 보드"><p>바깥 연결은 에이전트 순서, 내부 연결은 모델·필터 실행 순서입니다.</p><p>노드 제목을 드래그하거나 방향키로 옮기고, 출력 점과 입력 점을 연결하세요. 활성 에이전트의 내부 단계가 모두 끝나면 다음 에이전트가 시작합니다. 비활성 에이전트는 건너뛰어 연결을 이어갑니다.</p><p>A01~A08은 수정 가능한 초안입니다. 현재 모델 호출·필터 실행을 지원하며 검색·MCP 호출, 사람 승인, 자동 재검사 반복은 연결되지 않았습니다. A03과 A08은 기본 비활성입니다.</p></ModeltestHelp></div><span>{board.agents.length}개 에이전트</span></div>
    <div className="mt-workflow-body">
      <div className="mt-workflow-toolbar"><button type="button" className="btn secondary small" disabled={disabled || board.agents.length >= 32} onClick={() => addAgent()}>에이전트 추가</button>
        <button type="button" className="btn secondary small" disabled={disabled} onClick={() => { if (!board.agents.length) applyTemplate(); else confirmAction({ title: '템플릿 적용', message: '현재 보드를 A01~A08 템플릿으로 바꿀까요?', confirmLabel: '바꾸기', danger: false }, applyTemplate); }}>A01~A08 템플릿</button>
        <label>동시 실행 수<select aria-label="에이전트 보드 동시 실행 수" disabled={disabled} value={board.maxParallel} onChange={event => change({ maxParallel: Number(event.target.value) })}><option value={1}>1개</option><option value={2}>최대 2개</option></select></label>
        <button type="button" className="text-btn" disabled={disabled} onClick={() => change({ positions: {} })}>보드 위치 초기화</button>
      </div>
      {board.maxParallel === 2 && <p className="mt-hint">두 모델을 함께 올릴 GPU 메모리가 필요합니다. 병렬 실행 여부는 선행 연결과 남은 실행 슬롯에 따라 결정됩니다.</p>}
      <WorkflowGraph key={`${flow}-agents`} boardId={`${flow}-agents`} label="에이전트 보드" selectedNode={agent?.id} selectOnClick onUndo={onUndo} canUndo={canUndo} onRedo={onRedo} canRedo={canRedo}
        nodeActions={id => [{ label: '이름 변경', run: () => rename(id) }, { label: board.agents.find(item => item.id === id)?.enabled ? '비활성화' : '활성화', run: () => change({ agents: board.agents.map(item => item.id === id ? { ...item, enabled: !item.enabled } : item) }) }, { label: '에이전트 삭제', danger: true, run: () => removeAgent(id) }]}
        addActions={[{ label: '에이전트 추가', disabled: board.agents.length >= 32, run: addAgent }]}
        nodes={board.agents.map(item => ({ id: item.id, title: item.name || '이름 없음', description: `${item.enabled ? '활성' : '비활성'} · 내부 ${item.nodes.length}단계` }))}
        edges={board.edges} positions={board.positions} onChange={change} disabled={disabled} error={error} onConfigure={selectAgent} />
      {agent && <div className="mt-agent-detail">
        <div className="mt-workflow-toolbar"><label>에이전트 이름<input ref={agentName} aria-label="에이전트 이름" maxLength={100} value={agent.name} disabled={disabled} onKeyDown={preventNameSubmit} onChange={event => changeAgent({ name: event.target.value })} /></label>
          <label className="mt-toggle"><input type="checkbox" checked={agent.enabled} disabled={disabled} onChange={event => changeAgent({ enabled: event.target.checked })} />이 에이전트 실행</label>
          <button type="button" className="text-btn mt-error" disabled={disabled} onClick={() => removeAgent()}>에이전트 삭제</button></div>
        <div className="mt-workflow-toolbar"><label>추가할 단계<select aria-label="추가할 에이전트 단계" disabled={disabled} value={newKind} onChange={event => setNewKind(event.target.value)}>{kinds.map(kind => <option key={kind.id} value={kind.id}>{kind.title}</option>)}</select></label>
          <button type="button" className="btn secondary small" disabled={disabled || board.agents.reduce((sum, item) => sum + item.nodes.length, 0) >= 64} onClick={() => addStep()}>내부 단계 추가</button>
          <button type="button" className="text-btn" disabled={disabled} onClick={() => changeAgent({ positions: {} })}>내부 위치 초기화</button></div>
        <ModeltestInspector open={inspectorOpen} onToggle={() => setInspectorOpen(!inspectorOpen)} label="내부 단계 인스펙터" title={step ? `${step.name} 설정` : '단계 설정'} board={
        <WorkflowGraph key={`${flow}-${agent.id}`} selectedNode={step?.id} selectOnClick boardId={`${flow}-${agent.id}`} label={`${agent.name} 내부 단계 보드`} onUndo={onUndo} canUndo={canUndo} onRedo={onRedo} canRedo={canRedo}
          nodeActions={id => [{ label: '이름 변경', run: () => rename(id, true) }, { label: '복제', disabled: board.agents.reduce((sum, item) => sum + item.nodes.length, 0) >= 64, run: () => duplicate(id) }, { label: '단계 삭제', danger: true, run: () => removeStep(id) }]}
          addActions={kinds.map(kind => ({ label: `단계 추가: ${kind.title}`, disabled: board.agents.reduce((sum, item) => sum + item.nodes.length, 0) >= 64, run: point => addStep(kind.id, point) }))}
          nodes={agent.nodes.map(node => ({ id: node.id, title: node.name, description: node.kind === 'candidate_filter' ? '유사도 임계값 · 상위 후보 수' : node.model }))} edges={agent.edges} positions={agent.positions}
          onChange={changeAgent} disabled={disabled} onConfigure={selectStep} error={!agent.nodes.length ? '내부 단계를 추가해 주세요.' : ''} />}>
        {step ? <div className="mt-agent-settings" aria-label="내부 단계 설정">
          <label>단계 이름<input ref={stepName} aria-label="내부 단계 이름" disabled={disabled} maxLength={100} value={step.name} onKeyDown={preventNameSubmit} onChange={event => changeStep({ name: event.target.value })} /></label>
          <label>처리 유형<select aria-label="내부 단계 처리 유형" disabled={disabled} value={step.kind} onChange={event => changeStep({ ...newAgentStep(event.target.value), id: step.id })}>{kinds.map(kind => <option key={kind.id} value={kind.id}>{kind.title}</option>)}</select></label>
          {step.kind === 'candidate_filter' ? <><div className="mt-title-help mt-settings-wide"><strong>후보 필터</strong><ModeltestHelp label="내부 후보 필터"><p>선정 기준별로 최소 유사도를 적용한 뒤 상위 N개를 선택합니다. 제외된 쌍은 대조하지 않습니다. 팀원의 프레임별·영상 전체 범위는 별도 확인이 필요합니다.</p></ModeltestHelp></div>
            <label className="mt-settings-wide">선정 기준<select aria-label="내부 필터 선정 기준" disabled={disabled} value={step.filterScope || 'references_per_crop'} onChange={event => changeStep({ filterScope: event.target.value })}>{Object.entries(FILTER_SCOPES).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
            <label>최소 코사인 유사도<input aria-label="내부 필터 최소 유사도" type="number" min={-1} max={1} step="any" disabled={disabled} value={step.threshold} placeholder="비워두면 제한 없음" onChange={event => changeStep({ threshold: event.target.value })} /></label>
            <label>상위 후보 수<input aria-label="내부 필터 상위 후보 수" type="number" min={1} max={10} step={1} disabled={disabled} value={step.topK} placeholder="비워두면 모두" onChange={event => changeStep({ topK: event.target.value })} /></label>
            </>
             : !GENERAL.includes(step.kind) && <><div className="mt-title-help mt-settings-wide"><strong>모델 설정</strong></div><label>등록 모델<select aria-label="내부 단계 등록 모델" disabled={disabled} value={models.some(model => model.id === modelId) ? modelId : ''} onChange={event => changeStep(modelChoice(event.target.value, health))}><option value="">직접 입력 / 링크 붙여넣기</option>{models.map(model => <option key={model.id} value={model.id}>{model.label || model.id}</option>)}</select></label>
              <label>모델 ID 또는 Hugging Face 링크<input aria-label="내부 단계 모델" disabled={disabled} value={registered?.model_id || step.model} maxLength={512} onChange={event => changeStep(modelChoice(event.target.value, health))} /></label>
              <p className="mt-hint mt-settings-wide">{modelWarning || profile?.description || (modelId ? `${modelId} · 입력 형식 확인, 실행 호환성은 Pod에서 확인` : '모델 목록에서 선택하거나 Hugging Face 링크를 입력하세요.')}</p>
              {SIGLIP2_MODELS.includes(modelId) && step.kind === 'visual_extract' && <label className="mt-settings-wide">비교할 후보 문구<textarea aria-label="내부 단계 후보 문구" disabled={disabled} rows={3} maxLength={6432} value={step.labels} onChange={event => changeStep({ labels: event.target.value })} /></label>}
              {modelId === 'vismatch/xfeat' && step.imageSimilarity && <label className="mt-settings-wide">추가 유사도 계산<select aria-label="내부 XFeat 추가 유사도 모델" disabled={disabled} value={step.imageSimilarityModel || SIGLIP2} onChange={event => changeStep({ imageSimilarity: !!event.target.value, imageSimilarityModel: event.target.value || SIGLIP2 })}><option value="">추가 계산 해제 · 별도 유사도 단계 사용</option>{SIGLIP2_MODELS.map(id => <option key={id} value={id}>{id}</option>)}</select></label>}
              <ModeltestPrompt key={`${step.id}-${step.model}`} kind={step.kind} modelId={modelId} profile={registered} value={step} disabled={disabled} label="내부 단계 프롬프트" onChange={changeStep} />
            </>}
          {EXTRA_STAGES.some(kind => kind.id === step.kind) && <div className="mt-settings-wide"><div className="mt-title-help"><label htmlFor="mt-agent-input-json">입력 설정 · JSON</label><ModeltestHelp variant="dialog" label="내부 단계 입력 설정"><p>documents: 텍스트 목록, query: 질의·이미지 설명, image_source: media / references / crops, modality: text / image. 연결된 입력을 우선 사용합니다.</p><p>TF-IDF/LR은 classifier에 vocabulary·idf·coef·intercept·labels를 지정하세요.</p></ModeltestHelp></div><textarea id="mt-agent-input-json" aria-label="내부 단계 입력 설정" rows={6} disabled={disabled} value={step.paramsText || ''} onChange={event => changeStep({ paramsText: event.target.value })} placeholder={'{"documents":["후보 문서 1","후보 문서 2"],"query":"검색 질의","image_source":"media","modality":"text"}'} /></div>}
          {inputRoles.map(role => <label key={role}>{STAGES.find(kind => kind.id === role)?.title || role} 입력<select aria-label={`내부 단계 ${role} 입력`} disabled={disabled} value={step.bindings?.[role] || ''} onChange={event => changeStep({ bindings: { ...step.bindings, [role]: event.target.value } })}><option value="">자동 · 가장 가까운 선행 결과</option>
            {board.agents.filter(item => item.enabled).map(item => <optgroup key={item.id} label={item.name}>{item.nodes.filter(node => acceptsInput(step.kind, role, node.kind) && node.id !== step.id).map(node => <option key={node.id} value={node.id}>{node.name}</option>)}</optgroup>)}</select></label>)}
          <div className="mt-settings-wide"><button type="button" className="text-btn mt-error" disabled={disabled} onClick={() => removeStep()}>내부 단계 삭제</button></div>
        </div> : <p className="mt-empty">보드에서 단계를 선택하세요</p>}
        </ModeltestInspector>
      </div>}
    </div>
  </section>;
}
