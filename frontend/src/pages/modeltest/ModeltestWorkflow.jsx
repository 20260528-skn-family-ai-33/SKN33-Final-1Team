import React, { useId, useRef, useState } from 'react';
import { STAGES, workflowNodes, workflowError, workflowPreset } from './modeltest.js';
import './modeltestWorkflow.css';

export function WorkflowGraph({ nodes, edges, positions, onChange, disabled, onConfigure, error = '' }) {
  const arrowId = useId().replace(/:/g, '');
  const canvas = useRef(null); const [drag, setDrag] = useState(null); const [source, setSource] = useState('');
  const [point, setPoint] = useState(null); const [notice, setNotice] = useState('');
  const workflow = { edges, positions }; const change = onChange;
  const maxY = Math.max(850, Math.ceil(nodes.length / 3) * 150);
  function position(id, index) {
    if (drag?.id === id) return drag;
    const saved = workflow.positions[id];
    return saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)
      ? { x: Math.max(0, Math.min(1000, saved.x)), y: Math.max(0, Math.min(maxY, saved.y)) }
      : { x: 24 + index % 3 * 340, y: 24 + Math.floor(index / 3) * 150 };
  }
  const placed = new Map(nodes.map((node, index) => [node.id, position(node.id, index)]));
  const title = id => nodes.find(node => node.id === id)?.title || STAGES.find(stage => stage.id === id)?.title || id;
  function connect(target) {
    if (!source || disabled) return;
    if (source === target || workflow.edges.some(edge => edge.source === source && edge.target === target)) setNotice('자기 연결 또는 중복 연결은 추가할 수 없습니다.');
    else { change({ edges: [...workflow.edges, { source, target }] }); setNotice(''); }
    setSource(''); setPoint(null);
  }
  function move(event) {
    if (drag) setDrag({ ...drag, x: Math.max(0, Math.min(1000, drag.originX + event.clientX - drag.clientX)), y: Math.max(0, Math.min(maxY, drag.originY + event.clientY - drag.clientY)) });
    if (source) { const rect = canvas.current.getBoundingClientRect(); setPoint({ x: event.clientX - rect.left, y: event.clientY - rect.top }); }
  }
  function endDrag() {
    if (drag) { change({ positions: { ...workflow.positions, [drag.id]: { x: drag.x, y: drag.y } } }); setDrag(null); }
  }
  function keyMove(event, id, pos) {
    const steps = { ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] };
    if (!steps[event.key] || disabled) return;
    event.preventDefault(); const [x, y] = steps[event.key];
    change({ positions: { ...workflow.positions, [id]: { x: Math.max(0, Math.min(1000, pos.x + x)), y: Math.max(0, Math.min(maxY, pos.y + y)) } } });
  }
  const curve = (from, to) => `M ${from.x} ${from.y} C ${from.x + 70} ${from.y}, ${to.x - 70} ${to.y}, ${to.x} ${to.y}`;
  return <>
      <p className="mt-workflow-notice" role="status">{notice || (source ? `${title(source)}의 출력을 연결할 입력 점을 선택하세요.` : error || '실행 가능한 연결입니다.')}{source && <button type="button" onClick={() => { setSource(''); setPoint(null); }}>연결 취소</button>}</p>
      <div className="mt-workflow-scroll"><div ref={canvas} className="mt-workflow-canvas" style={{ height: Math.max(340, ...[...placed.values()].map(pos => pos.y + 140)) }} onPointerMove={move} onPointerUp={endDrag} onPointerCancel={() => setDrag(null)}>
        <svg className="mt-workflow-wires" aria-hidden="true"><defs><marker id={arrowId} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="currentColor" /></marker></defs>
          {workflow.edges.map((edge, index) => { const a = placed.get(edge.source); const b = placed.get(edge.target); return a && b && <path key={index} d={curve({ x: a.x + 250, y: a.y + 48 }, { x: b.x, y: b.y + 48 })} markerEnd={`url(#${arrowId})`} />; })}
          {source && point && placed.has(source) && <path className="is-connecting" d={curve({ x: placed.get(source).x + 250, y: placed.get(source).y + 48 }, point)} />}
        </svg>
        {nodes.map(node => { const pos = placed.get(node.id); return <div className="mt-workflow-node" key={node.id} style={{ left: pos.x, top: pos.y }} data-node={node.id}>
          <button type="button" className="mt-node-port mt-node-input" disabled={disabled} aria-label={`${node.title} 입력 연결`} onPointerUp={() => connect(node.id)} onClick={event => { if (event.detail === 0) connect(node.id); }} />
          <button type="button" className="mt-node-drag" disabled={disabled} aria-label={`${node.title} 노드 이동`} onKeyDown={event => keyMove(event, node.id, pos)}
            onPointerDown={event => { if (event.button !== 0) return; event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); setDrag({ id: node.id, ...pos, originX: pos.x, originY: pos.y, clientX: event.clientX, clientY: event.clientY }); }}>{node.title}</button>
          <small>{node.id === 'frames' ? '영상 프레임 샘플링' : node.id === 'candidate_filter' ? '최소 유사도 · 상위 후보 수' : node.description || '단계를 설정하세요'}</small>
          <button type="button" className="text-btn" onClick={() => onConfigure(node.id)}>설정</button>
          <button type="button" className={`mt-node-port mt-node-output ${source === node.id ? 'is-selected' : ''}`} disabled={disabled} aria-label={`${node.title} 출력 연결`}
            onPointerDown={event => { if (event.button === 0) { event.preventDefault(); setSource(node.id); setNotice(''); setPoint(null); } }} onClick={event => { if (event.detail === 0) { setSource(node.id); setNotice(''); } }} />
        </div>; })}
      </div></div>
      <ul className="mt-workflow-edges" aria-label="워크플로우 연결 목록">{workflow.edges.map((edge, index) => <li key={`${edge.source}-${edge.target}`}><span>{title(edge.source)} → {title(edge.target)}</span>
        <button type="button" disabled={disabled} aria-label={`${title(edge.source)}에서 ${title(edge.target)} 연결 삭제`} onClick={() => change({ edges: workflow.edges.filter((_, i) => i !== index) })}>삭제</button></li>)}</ul>
      {!workflow.edges.length && <p className="mt-hint">연결이 없는 단계는 동시에 실행 가능한 시작 단계가 됩니다. 필수 입력 연결은 실행 전에 검사합니다.</p>}
  </>;
}

export default function ModeltestWorkflow({ flow, state, update, disabled }) {
  const workflow = state.workflow;
  const nodes = workflowNodes(flow, state).map(node => ({ ...node, description: state.stages[node.id]?.model || node.description }));
  const change = patch => update({ workflow: { ...workflow, ...patch } });
  const error = workflowError(flow, state);
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
      <p className="mt-hint">노드 제목을 드래그하거나 방향키로 이동하세요. 출력 점에서 입력 점으로 드래그하거나, 두 점을 차례로 눌러 연결합니다. 모델·필터 값은 각 노드의 설정에서 변경합니다.</p>
      {workflow.maxParallel === 2 && <p className="mt-hint">선행 단계가 끝난 노드를 최대 2개 동시에 실행합니다. 두 모델을 함께 올릴 GPU 메모리가 필요하며, 부족하면 실행이 실패할 수 있습니다.</p>}
      <WorkflowGraph nodes={nodes} edges={workflow.edges} positions={workflow.positions} onChange={change} disabled={disabled} error={error}
        onConfigure={id => document.getElementById(`mt-stage-${id}`)?.scrollIntoView({ behavior: 'auto', block: 'center' })} />
    </div>}
  </section>;
}
