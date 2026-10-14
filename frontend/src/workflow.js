export const initialState = {
  step: 'input', projectName: '', requirements: '', file: null,
  criteria: [], snapshot: null, version: 0, review: null,
};

export function parseRequirements(value) {
  return value.split(/\r?\n/).map(line => line.trim()).filter(Boolean).map((text, i) => ({
    id: `criterion-${i + 1}`, text, source: text, selected: true,
    type: /로고|색상|가독성|잘리|이미지|그림|배치|시각|캐릭터/.test(text) ? 'visual' : 'information',
  }));
}

export function canConfirm(state) {
  const selected = state.criteria.filter(item => item.selected);
  return Boolean(state.file && selected.length && selected.every(item => item.text.trim() && item.text.trim().length <= 500 && item.source.length <= 500) && !state.snapshot);
}

export function canRun(state) {
  return Boolean(state.file && state.snapshot?.criteria.length && state.snapshot.version === state.version);
}

export function workflowReducer(state, action) {
  switch (action.type) {
    case 'input':
      return { ...state, [action.field]: action.value, criteria: [], snapshot: null, review: null };
    case 'sample':
      return { ...state, step: 'input', projectName: action.projectName, requirements: action.requirements,
        file: action.file, criteria: [], snapshot: null, review: null };
    case 'prepare':
      if (!state.file || !state.requirements.trim()) return state;
      return { ...state, step: 'criteria', criteria: state.criteria.length ? state.criteria : parseRequirements(state.requirements) };
    case 'update':
      if (state.snapshot) return state;
      return { ...state, review: null, criteria: state.criteria.map(item => item.id === action.id ? { ...item, ...action.patch } : item) };
    case 'select':
      if (state.snapshot) return state;
      return { ...state, review: null, criteria: state.criteria.map(item => ({ ...item, selected: action.selected })) };
    case 'add':
      if (state.snapshot || !action.text.trim()) return state;
      return { ...state, review: null, criteria: [...state.criteria, { id: action.id, text: action.text.trim(), type: action.category, source: '직접 추가', selected: true }] };
    case 'confirm': {
      if (!canConfirm(state)) return state;
      const version = state.version + 1;
      const criteria = state.criteria.filter(item => item.selected).map(({ id, text, type, source }) => ({ id, text: text.trim(), type, source }));
      return { ...state, version, review: null, snapshot: { version, criteria, projectName: state.projectName } };
    }
    case 'unlock':
      return { ...state, step: 'criteria', snapshot: null, review: null };
    case 'run':
      if (!canRun(state)) return state;
      return { ...state, step: 'running', review: action.review };
    case 'progress':
      if (!state.snapshot || (state.review?.id && state.review.id !== action.review.id)) return state;
      return { ...state, review: action.review, step: action.review.status === 'completed' ? 'results' : 'running' };
    case 'navigate':
      if (state.step === 'running' && ['queued', 'running', 'submitting'].includes(state.review?.status)) return state;
      return { ...state, step: action.step };
    case 'reset': return { ...initialState };
    default: return state;
  }
}

export function evidenceBoxStyle(bbox, width, height) {
  if (!Array.isArray(bbox) || bbox.length !== 4 || !width || !height || !bbox.every(Number.isFinite)) return null;
  const x1 = Math.min(width, Math.max(0, bbox[0]));
  const y1 = Math.min(height, Math.max(0, bbox[1]));
  const x2 = Math.min(width, Math.max(x1, bbox[2]));
  const y2 = Math.min(height, Math.max(y1, bbox[3]));
  if (x1 === x2 || y1 === y2) return null;
  return { left: `${x1 / width * 100}%`, top: `${y1 / height * 100}%`, width: `${(x2 - x1) / width * 100}%`, height: `${(y2 - y1) / height * 100}%` };
}
