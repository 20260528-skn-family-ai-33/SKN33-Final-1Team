import assert from 'node:assert/strict';
import test from 'node:test';
import { mergeDocumentRequirements } from './documentRequirements.js';
import { initialState, workflowReducer, parseRequirements } from './workflow.js';

const document = { conditions: [
  { text: '브랜드: 온결 또는 ONGYEOL 중 하나', type: 'information', source: '브랜드는 온결 또는 ONGYEOL 중 하나를 표기한다.', page: 2 },
  { text: '분위기: 차분하고 따뜻한 가을 분위기', type: 'visual', source: '차분하고 따뜻한 가을 분위기로 제작한다.', page: 2 },
] };

test('기존 입력을 유지하고 동일 조건은 중복 추가하지 않는다', () => {
  const result = mergeDocumentRequirements('직접 입력: 유지\n' + document.conditions[0].text, document, '요구사항.pdf');
  assert.equal(result.addedCount, 1);
  assert.ok(result.requirements.startsWith('직접 입력: 유지\n브랜드: 온결 또는 ONGYEOL 중 하나'));
  assert.equal(result.sources[0].type, 'visual');
});

test('PDF 출처와 LLM 유형이 기준 확정까지 유지되고 기존 확정은 해제된다', () => {
  const merged = mergeDocumentRequirements('', document, '요구사항.pdf');
  let state = workflowReducer({ ...initialState, file: { name: 'poster.png' }, snapshot: { version: 1 }, review: { id: 'old' } }, { type: 'document', ...merged });
  assert.equal(state.snapshot, null); assert.equal(state.review, null);
  state = workflowReducer(state, { type: 'prepare' });
  state = workflowReducer(state, { type: 'confirm' });
  assert.equal(state.snapshot.criteria[1].type, 'visual');
  assert.match(state.snapshot.criteria[1].source, /요구사항.pdf · 2페이지/);
  const edited = parseRequirements('분위기: 직접 수정한 조건', state.documentSources)[0];
  assert.equal(edited.source, '분위기: 직접 수정한 조건');
  assert.deepEqual(workflowReducer(state, { type: 'reset' }).documentSources, []);
});

test('한도 초과와 잘못된 응답은 기존 입력을 대체하지 않는다', () => {
  assert.throws(() => mergeDocumentRequirements('a'.repeat(5990), document, 'a.pdf'), /6,000/);
  assert.throws(() => mergeDocumentRequirements(Array.from({ length: 100 }, (_, i) => `항목: ${i}`).join('\n'), document, 'a.pdf'), /100/);
  assert.throws(() => mergeDocumentRequirements('원래 문구', { conditions: [] }, 'a.pdf'));
  assert.throws(() => mergeDocumentRequirements('', { conditions: [{ ...document.conditions[0], text: '둘\n셋' }] }, 'a.pdf'));
});

test('Paddle로 읽은 출처를 PDF 내장 원문과 구분한다', () => {
  const result = mergeDocumentRequirements('', { conditions: [{ ...document.conditions[0], source_kind: 'paddleocr' }] }, 'scan.pdf');
  const rows = parseRequirements(result.requirements, result.sources);
  assert.match(rows[0].source, /scan.pdf · 2페이지 · OCR 판독문/);
});

test('페이지 이미지와 OCR을 함께 읽은 출처를 기준 확인 화면까지 보존한다', () => {
  const result = mergeDocumentRequirements('', { conditions: [{ ...document.conditions[0], source_kind: 'pdf_page+paddleocr' }] }, 'scan.pdf');
  assert.match(parseRequirements(result.requirements, result.sources)[0].source, /scan.pdf · 2페이지 · PDF 이미지 \+ OCR 판독문/);
});

test('PDF 기대값과 판단 설명은 확정 요청까지 분리해 보존하고 편집 시 이전 구조를 제거한다', () => {
  const item = { text: '시간: 17:30',
    type: 'information', label: '행사 시간', requirement: '17:30', level: '필수', inspection: '공백 차이는 허용한다.',
    source: 'R03 필수 | 행사 시간 | 17:30 | 공백 차이는 허용한다.', page: 2 };
  const merged = mergeDocumentRequirements('', { conditions: [item] }, '새계약서.pdf');
  let state = workflowReducer({ ...initialState, file: { name: 'poster.jpg' } }, { type: 'document', ...merged });
  state = workflowReducer(state, { type: 'prepare' });
  state = workflowReducer(state, { type: 'confirm' });
  assert.equal(state.snapshot.criteria[0].document_condition.requirement, '17:30');
  assert.equal(state.snapshot.criteria[0].document_condition.inspection, '공백 차이는 허용한다.');
  state = workflowReducer(state, { type: 'unlock' });
  state = workflowReducer(state, { type: 'update', id: 'criterion-1', patch: { text: '행사 시간: 18:30' } });
  state = workflowReducer(state, { type: 'confirm' });
  assert.equal(state.snapshot.criteria[0].document_condition, undefined);
});

test('병합한 PDF 조건의 모든 페이지 근거와 긴 보완 설명을 확정까지 보존한다', () => {
  const item = { text: '필수 고지: 시즌 한정', type: 'information', label: '필수 고지', requirement: '시즌 한정',
    level: '필수', inspection: '문구 누락 불가 / ' + '가독성 기준 '.repeat(40), page: 1, source: '필수 고지 | 시즌 한정',
    source_references: [
      { source_id: 'p1-s1', page: 1, source: '필수 고지 | 시즌 한정' },
      { source_id: 'p2-s3', page: 2, source: 'IMG-06 | 필수 고지 | 시즌 한정' },
    ] };
  const merged = mergeDocumentRequirements('', { conditions: [item] }, 'brief.pdf');
  let state = workflowReducer({ ...initialState, file: { name: 'poster.png' } }, { type: 'document', ...merged });
  state = workflowReducer(state, { type: 'prepare' });
  state = workflowReducer(state, { type: 'confirm' });
  const actual = state.snapshot.criteria[0];
  assert.equal(actual.document_condition.inspection, item.inspection);
  assert.deepEqual(actual.document_condition.sources, item.source_references);
  assert.match(actual.source, /1·2페이지/);
});
