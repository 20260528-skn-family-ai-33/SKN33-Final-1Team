import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { CODES, POLISH_NOTICE, buildActions, buildReportContext, fill, itemLabel, kstMinute, locationHint, particle, polishedActions, posterMarks } from './reportBrief.js';

const template = JSON.parse(readFileSync(new URL('../../../../backend/a07_reporter/report_template.json', import.meta.url), 'utf8'));
const example = template.poster.example;

test('코드표는 팀 템플릿 JSON과 같다', () => {
  for (const key of ['verdict', 'observation', 'field_label', 'issue_reason', 'reason_priority']) assert.deepEqual(CODES[key], template.codes[key], key);
  const { _unknown, ...limitations } = template.codes.limitation_ko;
  assert.deepEqual(CODES.limitation_ko, limitations);
  for (const [key, rule] of Object.entries(CODES.action_rules)) assert.equal(rule, template.codes.action_rules[key], key);
});

test('템플릿 예시의 고칠 것·확인할 것을 그대로 만든다', () => {
  assert.deepEqual(buildActions(example.items).map(({ criterion_ids, ...action }) => action), example.actions);
  assert.deepEqual(buildActions(example.items).at(-1).criterion_ids, ['c-illustration', 'c-file-format', 'c-color-mode']);
});

test('조사는 앞 글자 받침으로 고른다', () => {
  for (const [before, marker, chosen] of [['행사 장소', '을(를)', '를'], ['행사명', '을(를)', '을'], ['‘일원’', '(으)로', '으로'],
    ['‘서울’', '(으)로', '로'], ['‘3’', '(으)로', '으로'], ['‘JPEG’', '와(과)', '와'], ['‘행사명’', '와(과)', '과'], ['', '이(가)', '이(가)']]) {
    assert.equal(particle(before, marker), chosen, before + marker);
  }
  assert.equal(fill(template.codes.action_rules.value_mismatch, { label: '장소', expected: '수원시청' }), '장소를 ‘수원시청’으로 고쳐 주세요.');
});

test('위치와 시각은 백엔드 규칙과 같다', () => {
  assert.equal(locationHint([0.12, 0.35, 0.78, 0.82]), '가운데');
  assert.equal(locationHint([0.08, 0.08, 0.88, 0.24]), '위쪽');
  assert.equal(locationHint([0.7, 0.7, 1, 1]), '오른쪽 아래');
  assert.equal(locationHint([0, 0, 1, 1]), '전체');
  assert.equal(kstMinute('2026-11-09T05:20:31Z'), '2026-11-09 14:20');
  assert.equal(kstMinute(null), null);
});

test('항목 이름은 presentation.label → field 이름 → 기준 문장 앞 20자', () => {
  assert.equal(itemLabel({ details: { field: 'venue', presentation: { label: '행사 장소', value: 'x' } } }), '행사 장소');
  assert.equal(itemLabel({ details: { field: 'venue' } }), '장소');
  assert.equal(itemLabel({ details: { field: 'unspecified' } }, { criterion_text: '우천 시   운영 안내를 포스터 하단에 꼭 넣어 주세요' }), '우천 시 운영 안내를 포스터 하단에…');
});

const run = {
  context: { run_id: 'run-1', asset_version_id: 'poster-v1', criteria_version_id: 'criteria-v0' },
  synthetic: true, baseline: { criteria_version_id: 'criteria-v1' },
  criteria: { criteria: [
    { criterion_id: 'venue', text: '장소는 고색역 상부공원 일원', details: { field: 'venue', presentation: { label: '행사 장소', value: '고색역 상부공원 일원' } } },
    { criterion_id: 'date', text: '행사일 11월 12일', details: { field: 'event_date' } },
    { criterion_id: 'name', text: '행사명', details: { field: 'event_name' } },
  ] },
  evidence: { status: 'stale', warnings: ['Approved-criteria context only; no PostgreSQL/Neo4j relationship lookup was executed.'], evidence: [
    { evidence_id: 'e1', kind: 'current_requirement', criterion_ids: ['venue', 'name'], source: { page: 1 } },
    { evidence_id: 'e2', kind: 'historical_context', criterion_ids: ['date'], source: { page: 9 } },
    { evidence_id: 'e3', kind: 'current_requirement', criterion_ids: ['date'], source: { page: 2 } }] },
  validated: { verdict: 'fail', limitations: ['Code validation only; visual accuracy and source authority are not independently verified.'], items: [
    { criterion_id: 'name', criterion_text: '행사명', required: false, expected: '축제', observed: '축제', observation_status: 'observed', verdict: 'pass', issues: [], image: { asset_version_id: 'poster-v1', bbox: [0, 0, 1, 0.2] } },
    { criterion_id: 'date', criterion_text: '행사일', required: true, expected: '11월 12일', observed: '11.12', observation_status: 'observed', verdict: 'review', issues: ['date_comparison_review_required'] },
    { criterion_id: 'venue', criterion_text: '장소', required: true, expected: '고색역 상부공원 일원', observed: '고색역 상부공원', observation_status: 'observed', verdict: 'fail', issues: ['value_mismatch'], image: { asset_version_id: 'poster-v0', bbox: [0.4, 0.8, 0.6, 1] } }] },
  step_records: [{ role: 'A06', finished_at: '2026-11-09T05:20:00Z' }, { role: 'A07', finished_at: '2026-11-09T05:21:00Z' }],
  approvals: [{ decision: 'approve_changes', human_reviews: [{ criterion_id: 'date', verdict: 'pass', observed: '포스터 날짜를 대조함', reference: '상단', reason: '같은 날', checked_asset: true }] }],
  report: { summary: '포스터 검수 보고서', change_requests: ['수정 [venue] 장소를 고쳐 주세요.'] },
};

test('실행 기록으로 템플릿 context를 채운다', () => {
  const context = buildReportContext(run, { projectName: '축제 포스터' });
  assert.deepEqual([context.title, context.checked_at, context.project.name, context.target, context.criteria],
    ['포스터 검수 보고서', '2026-11-09 14:20', '축제 포스터', { name: null, version: 'poster-v1' }, { document: null, version: 'criteria-v1' }]);
  assert.deepEqual(context.verdict, { code: 'fail', label: '불충족' });
  assert.deepEqual(context.counts, { required: 2, pass: 0, fail: 1, review: 1, optional: 1 });
  assert.deepEqual(context.items.map(item => [item.criterion_id, item.label, item.reason, item.source, item.location]), [
    ['venue', '행사 장소', '값이 기준과 다름', 'PDF 1쪽', null],   // 다른 포스터 버전의 위치는 쓰지 않는다
    ['date', '행사일', '날짜·시간 표기 확인', 'PDF 2쪽', null],   // 과거 기록 쪽수는 빼고 현재 기준만
    ['name', '행사명', '', 'PDF 1쪽', '위쪽']]);
  assert.deepEqual(context.items[1].human, { verdict: { code: 'pass', label: '충족' }, note: '포스터 날짜를 대조함 · 사유 같은 날' });
  assert.deepEqual(context.actions, [
    { kind: '수정', label: '행사 장소', time: null, text: '행사 장소를 ‘고색역 상부공원 일원’으로 고쳐 주세요.' },
    { kind: '확인', label: '행사일', time: null, text: '행사일이 기준과 같은 날짜·시간인지 확인해 주세요.' }]);
  assert.deepEqual(context.notes, ['자동 검수 초안입니다. 담당자가 확인하기 전에는 승인이 아닙니다.', '합성 시험 데이터로 만든 보고서입니다.',
    '코드 검증만 했습니다. 이미지 인식 정확도와 기준 출처는 따로 검증하지 않았습니다.', '기준 근거가 최신 상태가 아닙니다.',
    '승인된 기준만 근거로 썼고 PostgreSQL·Neo4j 관계 조회는 하지 않았습니다.']);
});

test('A07이 Qwen 할 일을 쓴 경우에만 그 문장을 쓴다', () => {
  const report = { summary: `포스터 검수 보고서\n안내:\n- ${POLISH_NOTICE}`,
    change_requests: ['수정 [venue] 장소 문구를 기준대로 바꿔 주세요.', '확인 [a,b] 묶은 줄', '수정 요청 [old] 예전 형식'] };
  assert.deepEqual(polishedActions(report), { venue: '장소 문구를 기준대로 바꿔 주세요.' });
  assert.deepEqual(polishedActions({ ...report, summary: '포스터 검수 보고서' }), {});
  const context = buildReportContext({ ...run, report }, {});
  assert.equal(context.actions[0].text, '장소 문구를 기준대로 바꿔 주세요.');
  assert.ok(context.notes.includes(POLISH_NOTICE));
});

test('A06 판정 칸이 없으면 실행 기록의 machine_verdict를 쓴다', () => {
  const { verdict, ...validated } = run.validated;
  assert.deepEqual(buildReportContext({ ...run, validated, machine_verdict: 'review' }).verdict, { code: 'review', label: '확인 필요' });
});

test('위치 보기는 불충족·확인 필요만, 현재 포스터의 위치 상자를 쓰고 번호는 결과 순서를 따른다', () => {
  const marked = { ...run, validated: { ...run.validated, items: run.validated.items.map(item =>
    item.criterion_id === 'venue' ? { ...item, image: { asset_version_id: 'poster-v1', bbox: [0.4, 0.8, 0.6, 1] } }
      : item.criterion_id === 'date' ? { ...item, image: { asset_version_id: 'poster-v1', bbox: [0.5, 0.2, 0.3, 0.4] } } : item) } };
  const { marks, unlocated } = posterMarks(marked, buildReportContext(marked).items);
  assert.deepEqual(marks, [{ number: '01', criterion_id: 'venue', code: 'fail', label: '행사 장소', bbox: [0.4, 0.8, 0.6, 1] }]);
  assert.deepEqual(unlocated, [{ number: '02', criterion_id: 'date', code: 'review', label: '행사일' }]);   // 뒤집힌 상자는 위치 없음
  // 다른 포스터 버전의 상자와 위치가 없는 항목은 그리지 않고, 충족 항목은 상자가 있어도 빼며, JSON(context)에는 넣지 않는다
  const original = posterMarks(run, buildReportContext(run).items);
  assert.deepEqual([original.marks, original.unlocated.map(item => item.number)], [[], ['01', '02']]);
  assert.ok(!('marks' in buildReportContext(marked)));
});
