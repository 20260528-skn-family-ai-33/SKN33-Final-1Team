import React from 'react';
import { stagesFor, workflowNodes, workflowError, workflowPreset } from './modeltest.js';
import { excludeStage } from './modeltestBoard.js';
import WorkflowGraph from './WorkflowGraph.jsx';
import './modeltestWorkflow.css';

export { WorkflowGraph };

export default function ModeltestWorkflow({ flow, state, update, disabled, onUndo, canUndo, onRedo, canRedo }) {
  const workflow = state.workflow;
  const nodes = workflowNodes(flow, state).map(node => ({ ...node, description: state.stages[node.id]?.model || node.description }));
  const change = patch => update({ workflow: { ...workflow, ...patch } });
  const error = workflowError(flow, state);
  function add(id, point) {
    update({ stages: { ...state.stages, [id]: { ...state.stages[id], enabled: true }, ...(id === 'logo_similarity' ? { logo_compare: { ...state.stages.logo_compare, imageSimilarity: false } } : {}) },
      workflow: { ...workflow, positions: { ...workflow.positions, [id]: point } } });
  }
  return <section className="mt-panel mt-workflow-editor" aria-label="검수 워크플로우 편집기">
    <div className="mt-panel-heading"><div><h2>워크플로우</h2><p>단계를 이동하고 연결해 실행 순서와 분기를 설정합니다.</p></div>
      <label className="mt-toggle"><input type="checkbox" checked={workflow.enabled} disabled={disabled}
        onChange={event => { if (event.target.checked && !workflow.edges.length) update(workflowPreset(flow, state)); else change({ enabled: event.target.checked }); }} />편집 사용</label>
    </div>
    {workflow.enabled && <div className="mt-workflow-body">
      <div className="mt-workflow-toolbar">
        <button type="button" className="btn secondary small" disabled={disabled} onClick={() => { update(workflowPreset(flow, state)); }}>현재 단계 순차 연결</button>
        {flow === 'video' && <><button type="button" className="btn secondary small" disabled={disabled} onClick={() => { update(workflowPreset(flow, state, 'filtered')); }}>SigLIP2 → 필터 → XFeat</button>
          <button type="button" className="btn secondary small" disabled={disabled} onClick={() => { update(workflowPreset(flow, state, 'parallel')); }}>SigLIP2 · XFeat 병렬</button></>}
        <label>동시 실행 수<select aria-label="워크플로우 동시 실행 수" disabled={disabled} value={workflow.maxParallel} onChange={event => change({ maxParallel: Number(event.target.value) })}><option value={1}>1개</option><option value={2}>최대 2개</option></select></label>
        <button type="button" className="text-btn" disabled={disabled} onClick={() => change({ positions: {} })}>위치 초기화</button>
      </div>
      <p className="mt-hint">출력 점에서 입력 점으로 드래그하거나 두 점을 차례로 눌러 연결합니다. 노드·연결선·빈 배경을 우클릭하면 편집 메뉴가 열립니다.</p>
      {workflow.maxParallel === 2 && <p className="mt-hint">선행 단계가 끝난 노드를 최대 2개 동시에 실행합니다. 두 모델을 함께 올릴 GPU 메모리가 필요하며, 부족하면 실행이 실패할 수 있습니다.</p>}
      <WorkflowGraph key={`${flow}-models`} boardId={`${flow}-models`} label="모델 단계 보드" nodes={nodes} edges={workflow.edges} positions={workflow.positions} onChange={change} disabled={disabled} error={error} onUndo={onUndo} canUndo={canUndo} onRedo={onRedo} canRedo={canRedo}
        nodeActions={id => [{ label: id === 'frames' ? '필수 프레임 단계 · 제외 불가' : '이 단계 제외', danger: true, disabled: id === 'frames', run: () => update(excludeStage(state, id)) }]}
        addActions={stagesFor(flow).filter(stage => !state.stages[stage.id].enabled).map(stage => ({ label: `단계 추가: ${stage.title}`, run: point => add(stage.id, point) }))}
        onConfigure={id => document.getElementById(`mt-stage-${id}`)?.scrollIntoView({ behavior: 'auto', block: 'center' })} />
    </div>}
  </section>;
}
