import test from 'node:test';
import assert from 'node:assert/strict';
import { boardPoint, boardView, zoomAt, fitBoard, nodePosition, excludeStage, rememberBoard } from './modeltestBoard.js';
import { cleanAgentBindings, removeAgentNode, duplicateAgentStep } from './modeltestAgents.js';
import { initialFlow, workflowPreset } from './modeltest.js';

test('zoom preserves the world point under the cursor and sanitizes persisted view', () => {
  const view = { x: -120, y: 50, zoom: .5, height: 400 }; const point = { x: 370, y: 210 };
  assert.deepEqual(boardPoint(zoomAt(view, 1.7, point), point), boardPoint(view, point));
  assert.deepEqual(boardView({ x: NaN, y: Infinity, zoom: 20, height: -200 }), { x: 0, y: 0, zoom: 2, height: 280 });
  assert.equal(zoomAt(view, .001, point).zoom, .05);
});

test('fit includes ports and every node, including negative positions and 64 default nodes', () => {
  const positions = Array.from({ length: 64 }, (_, i) => nodePosition({}, String(i), i)); positions[0] = { x: -200, y: -120 };
  const view = fitBoard(boardView(), positions, 800, 400);
  for (const pos of positions) {
    assert.ok((pos.x - 12) * view.zoom + view.x >= 23.9);
    assert.ok((pos.x + 262) * view.zoom + view.x <= 776.1);
    assert.ok(pos.y * view.zoom + view.y >= 23.9);
    assert.ok((pos.y + 96) * view.zoom + view.y <= 376.1);
  }
  assert.deepEqual(nodePosition({ a: { x: -5, y: 2000 } }, 'a', 0), { x: -5, y: 2000 });
});

function board() {
  return { positions: { a: { x: 1, y: 2 } }, edges: [{ source: 'a', target: 'b' }], agents: [
    { id: 'a', name: 'A', enabled: true, positions: { one: { x: 24, y: 24 } }, nodes: [{ id: 'one', name: 'OCR', model: 'model', prompt: 'prompt', bindings: {} }, { id: 'two', bindings: { text: 'one' } }], edges: [{ source: 'one', target: 'two' }] },
    { id: 'b', name: 'B', enabled: true, positions: {}, nodes: [{ id: 'three', bindings: { text: 'two' } }], edges: [] },
  ] };
}

test('delete cleans connected wires, positions and references across agents without mutating history', () => {
  const original = board(); const removed = removeAgentNode(original, 'a', 'two');
  assert.equal(original.agents[0].nodes.length, 2); assert.equal(original.agents[1].nodes[0].bindings.text, 'two');
  assert.deepEqual(removed.agents[0].edges, []); assert.deepEqual(removed.agents[1].nodes[0].bindings, {});
  const all = removeAgentNode(original, 'a'); assert.deepEqual(all.positions, {}); assert.deepEqual(all.edges, []);
  assert.deepEqual(all.agents[0].nodes[0].bindings, {});
});

test('disconnect removes only unreachable bindings and keeps alternative paths', () => {
  const original = board();
  const disconnected = cleanAgentBindings({ ...original, edges: [] });
  assert.deepEqual(disconnected.agents[1].nodes[0].bindings, {});
  assert.deepEqual(disconnected.agents[0].nodes[1].bindings, { text: 'one' });
  const alternate = { ...original, agents: [...original.agents, { id: 'c', name: 'C', enabled: false, nodes: [], edges: [] }], edges: [{ source: 'a', target: 'c' }, { source: 'c', target: 'b' }] };
  assert.deepEqual(cleanAgentBindings(alternate).agents[1].nodes[0].bindings, { text: 'two' });
  const internal = structuredClone(original); internal.agents[0].edges = [];
  assert.deepEqual(cleanAgentBindings(internal).agents[0].nodes[1].bindings, {});
});

test('duplicate copies model settings without input bindings and receives a new ID', () => {
  const node = { ...board().agents[0].nodes[0], bindings: { text: 'prior' }, nested: { labels: ['A'] } };
  const copy = duplicateAgentStep(node);
  assert.notEqual(copy.id, node.id); assert.equal(copy.name, 'OCR 복사'); assert.equal(copy.model, node.model); assert.equal(copy.prompt, node.prompt); assert.deepEqual(copy.bindings, {});
  copy.nested.labels.push('B'); assert.deepEqual(node.nested.labels, ['A']);
});

test('stage exclusion protects frames and undo restores settings without attachments or results', () => {
  const state = { ...initialFlow('video'), media: { name: 'video' } };
  Object.assign(state, workflowPreset('video', state, 'filtered'));
  const history = []; rememberBoard(history, state);
  assert.deepEqual(excludeStage(state, 'frames'), {});
  const next = { ...state, ...excludeStage(state, 'logo_extract') };
  assert.equal(next.stages.logo_extract.enabled, false); assert.ok(!next.workflow.edges.some(edge => edge.source === 'logo_extract' || edge.target === 'logo_extract'));
  const replacement = { name: 'replacement' }; const restored = { ...next, media: replacement, ...history.pop() };
  assert.equal(restored.media, replacement); assert.equal(restored.stages.logo_extract.enabled, true); assert.deepEqual(restored.workflow.edges, state.workflow.edges);
  for (let i = 0; i < 60; i++) rememberBoard(history, state);
  assert.equal(history.length, 50); assert.equal('media' in history[0], false);
});
