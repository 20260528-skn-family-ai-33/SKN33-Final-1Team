import React, { useState } from 'react';
import { STAGES, modelOptions, parseModel } from './modeltest.js';
import { agentTemplate, compileAgents, newAgent, newAgentStep } from './modeltestAgents.js';
import { WorkflowGraph } from './ModeltestWorkflow.jsx';

export default function ModeltestAgents({ flow, state, update, disabled, health }) {
  const board = state.agentWorkflow; const [agentId, setAgentId] = useState(''); const [stepId, setStepId] = useState(''); const [newKind, setNewKind] = useState('text_generate');
  const agent = board.agents.find(item => item.id === agentId) || board.agents[0];
  const step = agent?.nodes.find(item => item.id === stepId) || agent?.nodes[0];
  const kinds = STAGES.filter(kind => flow === 'video' || !kind.video);
  const change = patch => update({ agentWorkflow: { ...board, ...patch } });
  const changeAgent = patch => change({ agents: board.agents.map(item => item.id === agent.id ? { ...item, ...patch } : item) });
  const changeStep = patch => changeAgent({ nodes: agent.nodes.map(item => item.id === step.id ? { ...item, ...patch } : item) });
  let error = ''; try { compileAgents(flow, state, health); } catch (cause) { error = cause.message; }
  let modelId = ''; try { modelId = parseModel(step?.model || '').model_id; } catch { /* An unfinished model ID stays editable. */ }
  const models = modelOptions(health).filter(model => model.stages.includes(step?.kind));
  const inputRoles = ({ logo_similarity: ['logo_extract'], candidate_filter: ['logo_similarity'], logo_compare: ['logo_extract', 'candidate_filter'], text_compare: ['text_extract', 'conditions'], visual_compare: ['visual_extract', 'conditions'] })[step?.kind] || [];
  function addAgent() { const next = newAgent(); change({ agents: [...board.agents, next] }); setAgentId(next.id); setStepId(''); }
  function removeAgent() {
    if (!window.confirm(`${agent.name}와 내부 단계 ${agent.nodes.length}개를 삭제할까요?`)) return;
    change({ agents: board.agents.filter(item => item.id !== agent.id), edges: board.edges.filter(edge => edge.source !== agent.id && edge.target !== agent.id) }); setAgentId(''); setStepId('');
  }
  function addStep() {
    const node = newAgentStep(newKind); const previous = step || agent.nodes.at(-1);
    changeAgent({ nodes: [...agent.nodes, node], edges: previous ? [...agent.edges, { source: previous.id, target: node.id }] : agent.edges }); setStepId(node.id);
  }
  function removeStep() {
    changeAgent({ nodes: agent.nodes.filter(item => item.id !== step.id), edges: agent.edges.filter(edge => edge.source !== step.id && edge.target !== step.id) }); setStepId('');
  }
  return <section className="mt-panel mt-agent-board" aria-label="에이전트 워크플로우 보드">
    <div className="mt-panel-heading"><div><h2>에이전트 워크플로우 보드</h2><p>바깥 연결은 에이전트 순서, 내부 연결은 모델·필터 실행 순서입니다.</p></div><span>{board.agents.length}개 에이전트</span></div>
    <div className="mt-workflow-body">
      <div className="mt-workflow-toolbar"><button type="button" className="btn secondary small" disabled={disabled || board.agents.length >= 32} onClick={addAgent}>에이전트 추가</button>
        <button type="button" className="btn secondary small" disabled={disabled} onClick={() => { if (!board.agents.length || window.confirm('현재 보드를 A01~A08 템플릿으로 바꿀까요?')) { update({ agentWorkflow: agentTemplate(flow) }); setAgentId(''); setStepId(''); } }}>A01~A08 템플릿</button>
        <label>동시 실행 수<select aria-label="에이전트 보드 동시 실행 수" disabled={disabled} value={board.maxParallel} onChange={event => change({ maxParallel: Number(event.target.value) })}><option value={1}>1개</option><option value={2}>최대 2개</option></select></label>
        <button type="button" className="text-btn" disabled={disabled} onClick={() => change({ positions: {} })}>보드 위치 초기화</button>
      </div>
      <p className="mt-hint">노드 제목을 드래그하거나 방향키로 옮기고, 출력 점과 입력 점을 연결하세요. 활성 에이전트의 내부 단계가 모두 끝나면 다음 에이전트가 시작합니다. 비활성 에이전트는 건너뛰어 연결을 이어갑니다.</p>
      <p className="mt-hint">A01~A08은 수정 가능한 초안입니다. 현재 모델 호출·필터 실행을 지원하며 검색·MCP 호출, 사람 승인, 자동 재검사 반복은 연결되지 않았습니다. A03과 A08은 기본 비활성입니다.</p>
      {board.maxParallel === 2 && <p className="mt-hint">두 모델을 함께 올릴 GPU 메모리가 필요합니다. 병렬 실행 여부는 선행 연결과 남은 실행 슬롯에 따라 결정됩니다.</p>}
      <WorkflowGraph nodes={board.agents.map(item => ({ id: item.id, title: item.name || '이름 없음', description: `${item.enabled ? '활성' : '비활성'} · 내부 ${item.nodes.length}단계` }))}
        edges={board.edges} positions={board.positions} onChange={change} disabled={disabled} error={error} onConfigure={id => { setAgentId(id); setStepId(''); }} />
      {agent && <div className="mt-agent-detail">
        <div className="mt-agent-tabs" aria-label="편집할 에이전트">{board.agents.map(item => <button type="button" key={item.id} aria-pressed={item.id === agent.id} onClick={() => { setAgentId(item.id); setStepId(''); }}>{item.name || '이름 없음'}</button>)}</div>
        <div className="mt-workflow-toolbar"><label>에이전트 이름<input aria-label="에이전트 이름" maxLength={100} value={agent.name} disabled={disabled} onChange={event => changeAgent({ name: event.target.value })} /></label>
          <label className="mt-toggle"><input type="checkbox" checked={agent.enabled} disabled={disabled} onChange={event => changeAgent({ enabled: event.target.checked })} />이 에이전트 실행</label>
          <button type="button" className="text-btn mt-error" disabled={disabled} onClick={removeAgent}>에이전트 삭제</button></div>
        <div className="mt-workflow-toolbar"><label>추가할 단계<select aria-label="추가할 에이전트 단계" disabled={disabled} value={newKind} onChange={event => setNewKind(event.target.value)}>{kinds.map(kind => <option key={kind.id} value={kind.id}>{kind.title}</option>)}</select></label>
          <button type="button" className="btn secondary small" disabled={disabled || board.agents.reduce((sum, item) => sum + item.nodes.length, 0) >= 64} onClick={addStep}>내부 단계 추가</button>
          <button type="button" className="text-btn" disabled={disabled} onClick={() => changeAgent({ positions: {} })}>내부 위치 초기화</button></div>
        <WorkflowGraph nodes={agent.nodes.map(node => ({ id: node.id, title: node.name, description: node.kind === 'candidate_filter' ? '유사도 임계값 · 상위 후보 수' : node.model }))} edges={agent.edges} positions={agent.positions}
          onChange={changeAgent} disabled={disabled} onConfigure={setStepId} error={!agent.nodes.length ? '내부 단계를 추가해 주세요.' : ''} />
        <div className="mt-agent-tabs" aria-label="편집할 내부 단계">{agent.nodes.map(node => <button type="button" key={node.id} aria-pressed={step?.id === node.id} onClick={() => setStepId(node.id)}>{node.name}</button>)}</div>
        {step && <div className="mt-agent-settings" aria-label="내부 단계 설정">
          <label>단계 이름<input aria-label="내부 단계 이름" disabled={disabled} maxLength={100} value={step.name} onChange={event => changeStep({ name: event.target.value })} /></label>
          <label>처리 유형<select aria-label="내부 단계 처리 유형" disabled={disabled} value={step.kind} onChange={event => changeStep({ ...newAgentStep(event.target.value), id: step.id })}>{kinds.map(kind => <option key={kind.id} value={kind.id}>{kind.title}</option>)}</select></label>
          {step.kind === 'candidate_filter' ? <><label>최소 코사인 유사도<input aria-label="내부 필터 최소 유사도" type="number" min={-1} max={1} step="any" disabled={disabled} value={step.threshold} placeholder="비워두면 제한 없음" onChange={event => changeStep({ threshold: event.target.value })} /></label>
            <label>상위 후보 수<input aria-label="내부 필터 상위 후보 수" type="number" min={1} max={10} step={1} disabled={disabled} value={step.topK} placeholder="비워두면 모두" onChange={event => changeStep({ topK: event.target.value })} /></label>
            <p className="mt-hint mt-settings-wide">각 로고의 레퍼런스 후보에 최소 유사도를 적용한 뒤 상위 N개를 선택합니다. 후보가 없으면 다음 로고 대조의 모델 호출을 생략합니다.</p></>
            : <><label>등록 모델<select aria-label="내부 단계 등록 모델" disabled={disabled} value={models.some(model => model.id === modelId) ? modelId : ''} onChange={event => changeStep({ model: event.target.value })}><option value="">직접 입력 / 링크 붙여넣기</option>{models.map(model => <option key={model.id} value={model.id}>{model.id}</option>)}</select></label>
              <label>모델 ID 또는 Hugging Face 링크<input aria-label="내부 단계 모델" disabled={disabled} value={step.model} maxLength={512} onChange={event => changeStep({ model: event.target.value })} /></label>
              {models.find(model => model.id === modelId)?.description && <p className="mt-hint mt-settings-wide">{models.find(model => model.id === modelId).description}</p>}
              {modelId === 'google/siglip2-base-patch16-224' && step.kind === 'visual_extract' && <label className="mt-settings-wide">비교할 후보 문구<textarea aria-label="내부 단계 후보 문구" disabled={disabled} rows={3} maxLength={6432} value={step.labels} onChange={event => changeStep({ labels: event.target.value })} /></label>}
              {!['google/owlv2-base-patch16', 'google/siglip2-base-patch16-224', 'vismatch/xfeat', 'PaddlePaddle/PaddleOCR-VL-1.6-GGUF'].includes(modelId) && <label className="mt-settings-wide">모델 지시 · 선택<textarea aria-label="내부 단계 모델 지시" rows={4} disabled={disabled} maxLength={8000} value={step.prompt} onChange={event => changeStep({ prompt: event.target.value })} /><small>단계의 기본 작업 지시와 입력 자료에 추가됩니다. 응답 생성 단계는 직접 연결된 선행 단계의 결과와 입력한 검수 조건을 받습니다.</small></label>}
            </>}
          {inputRoles.map(role => <label key={role}>{STAGES.find(kind => kind.id === role)?.title} 입력<select aria-label={`내부 단계 ${role} 입력`} disabled={disabled} value={step.bindings?.[role] || ''} onChange={event => changeStep({ bindings: { ...step.bindings, [role]: event.target.value } })}><option value="">자동 · 가장 가까운 선행 결과</option>
            {board.agents.filter(item => item.enabled).map(item => <optgroup key={item.id} label={item.name}>{item.nodes.filter(node => node.kind === role && node.id !== step.id).map(node => <option key={node.id} value={node.id}>{node.name}</option>)}</optgroup>)}</select></label>)}
          <div className="mt-settings-wide"><button type="button" className="text-btn mt-error" disabled={disabled} onClick={removeStep}>내부 단계 삭제</button></div>
        </div>}
      </div>}
    </div>
  </section>;
}
