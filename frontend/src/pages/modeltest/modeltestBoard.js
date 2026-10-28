export const DEFAULT_VIEW = { x: 0, y: 0, zoom: 1, height: 400 };
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
export function boardView(value) {
  const result = { ...DEFAULT_VIEW };
  for (const key of Object.keys(result)) if (Number.isFinite(value?.[key])) result[key] = value[key];
  result.zoom = clamp(result.zoom, .05, 2); result.height = clamp(result.height, 280, 1000);
  return result;
}
export const boardPoint = (view, point) => ({ x: (point.x - view.x) / view.zoom, y: (point.y - view.y) / view.zoom });
export function zoomAt(view, zoom, point) {
  const world = boardPoint(view, point); const next = boardView({ ...view, zoom });
  return { ...next, x: point.x - world.x * next.zoom, y: point.y - world.y * next.zoom };
}
export function nodePosition(positions, id, index) {
  const saved = positions?.[id];
  return Number.isFinite(saved?.x) && Number.isFinite(saved?.y) ? saved : { x: 24 + index % 3 * 340, y: 24 + Math.floor(index / 3) * 150 };
}
export function fitBoard(view, positions, width, height) {
  if (!positions.length || !width || !height) return { ...view, x: 0, y: 0, zoom: 1 };
  const left = Math.min(...positions.map(pos => pos.x)) - 12; const top = Math.min(...positions.map(pos => pos.y));
  const right = Math.max(...positions.map(pos => pos.x)) + 262; const bottom = Math.max(...positions.map(pos => pos.y)) + 96;
  const zoom = clamp(Math.min((width - 48) / (right - left), (height - 48) / (bottom - top)), .05, 2);
  return { ...view, zoom, x: (width - (right - left) * zoom) / 2 - left * zoom, y: (height - (bottom - top) * zoom) / 2 - top * zoom };
}
export function omitPosition(positions, id) { const next = { ...positions }; delete next[id]; return next; }
export function excludeStage(state, id) {
  if (id === 'frames' || !state.stages[id]) return {};
  return { stages: { ...state.stages, [id]: { ...state.stages[id], enabled: false } }, workflow: { ...state.workflow,
    positions: omitPosition(state.workflow.positions, id), edges: state.workflow.edges.filter(edge => edge.source !== id && edge.target !== id) } };
}
export function rememberBoard(history, state) {
  history.push({ stages: state.stages, workflow: state.workflow, agentWorkflow: state.agentWorkflow });
  if (history.length > 50) history.shift();
}
