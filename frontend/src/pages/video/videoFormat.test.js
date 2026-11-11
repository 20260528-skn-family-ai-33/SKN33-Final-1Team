import test from 'node:test';
import assert from 'node:assert/strict';
import { parseRequirements } from '../review/workflow.js';
import {
  coverageNote, criteriaPayload, diffChars, filmCells, filterItems, formatTime, formatVideoUploadLimit, frameId, frameProgress, judgmentItems, MiB, planTimes, progressSteps, scanState,
} from './videoFormat.js';

test('video upload labels preserve the server limit in bytes', () => {
  assert.equal(formatVideoUploadLimit(undefined), '');
  assert.equal(formatVideoUploadLimit(100 * MiB), '100 MiB');
  assert.equal(formatVideoUploadLimit(1024 * MiB), '1 GiB');
  assert.equal(formatVideoUploadLimit(1500 * MiB), '1500 MiB');
});

test('video criteria payload drops screen-only fields that the server rejects', () => {
  const payload = criteriaPayload(parseRequirements('행사명: 김천김밥축제\n로고가 보여야 합니다'));
  assert.deepEqual(payload.map(item => Object.keys(item).sort()), [['id', 'source', 'text', 'type'], ['id', 'source', 'text', 'type']]);
  assert.deepEqual(payload.map(item => item.type), ['information', 'visual']);
});

test('time labels never overflow the tenth digit', () => {
  assert.deepEqual([5963, 29800, 3000, 65200].map(formatTime), ['0:05.9', '0:29.8', '0:03', '1:05.2']);
});

test('diff marks only the differing characters', () => {
  assert.deepEqual(diffChars('할인 70%', '할인 50%').filter(([, d]) => d).map(([c]) => c), ['5']);
});

test('progress steps follow the server stage and stop at the failed stage', () => {
  const statuses = job => progressSteps(job).map(step => step.status);
  assert.deepEqual(statuses({ status: 'running', stage: 'ocr' }),
    ['completed', 'completed', 'completed', 'active', 'pending', 'pending', 'pending', 'pending']);
  // 로고 대조·재생 변환처럼 화면에 따로 없는 단계는 가까운 단계로 묶는다.
  assert.equal(progressSteps({ status: 'running', stage: 'logo' }).find(step => step.status === 'active').key, 'review');
  assert.equal(progressSteps({ status: 'running', stage: 'transcode' }).find(step => step.status === 'active').key, 'double_check');
  assert.deepEqual(statuses({ status: 'failed', stage: 'failed', failed_stage: 'segmenting' }),
    ['completed', 'completed', 'completed', 'completed', 'completed', 'failed', 'pending', 'pending']);
  assert.ok(progressSteps({ status: 'completed', stage: 'completed' }).every(step => step.status === 'completed'));
});

test('frame counts come only from the server detail text', () => {
  assert.deepEqual(frameProgress('프레임 12 / 30'), { done: 12, total: 30 });
  assert.deepEqual(frameProgress('프레임 31 / 30'), { done: 30, total: 30 });
  assert.deepEqual(frameProgress('프레임 42장'), { done: 0, total: 42 });
  assert.equal(frameProgress('대기 2건'), null);
  assert.equal(frameProgress(null), null);
});

test('the sampling plan mirrors the server plan_times rule', () => {
  const plan = planTimes(12000, true);
  assert.equal(plan.length, 36);
  assert.deepEqual(plan.slice(0, 3).map(item => [item.time_ms, item.sampling]), [[0, 'base'], [200, 'head'], [400, 'head']]);
  assert.deepEqual(plan.at(-1), { time_ms: 11800, sampling: 'tail', frame_id: 't011800' });
  // 자체 광고는 시작·끝 0.2초 슬롯 없이 1초 격자만 뽑는다.
  assert.deepEqual(planTimes(12000, false).map(item => item.time_ms), Array.from({ length: 12 }, (_, i) => i * 1000));
  // 0.2초를 다 채우지 못하는 끝 슬롯은 계획에 없다(30.1초 영상의 30초 슬롯).
  assert.equal(planTimes(30100, false).at(-1).time_ms, 29000);
  assert.equal(planTimes(30200, false).at(-1).time_ms, 30000);
  assert.deepEqual(planTimes(150, true).map(item => item.time_ms), [0]);
});

test('coverage note mentions 0.2-second edges only when they were sampled', () => {
  assert.equal(coverageNote({ inspected_ms: [0, 200, 1000], note: '1초 간격 표본 검사 · 시작·끝 3초는 0.2초 간격' }), '1초 간격 표본 검사 · 시작·끝 3초는 0.2초 간격');
  assert.equal(coverageNote({ inspected_ms: [0, 1000, 2000], note: '1초 간격 표본 검사 · 시작·끝 3초는 0.2초 간격' }), '1초 간격 표본 검사');
  assert.equal(coverageNote(undefined), '');
});

test('the magnifier position reflects reported work, not elapsed time', () => {
  assert.deepEqual(scanState({ status: 'queued', stage: 'queued', progress: 5 }), { phase: 'waiting', position: 0, readUpTo: -1, lensTime: null, frames: null, framesReady: false });
  assert.equal(scanState({ status: 'running', stage: 'extracting', progress: 5 }).phase, 'preparing');
  assert.equal(scanState({ status: 'running', stage: 'scene_detection', stage_detail: '프레임 30장' }).framesReady, true);
  assert.equal(scanState({ status: 'running', stage: 'ocr', stage_detail: '프레임 15 / 60', progress: 33 }).position, .25);
  // 상세 문구가 없으면 서버 진행률(15→88)로만 위치를 정한다.
  assert.equal(scanState({ status: 'running', stage: 'ocr', progress: 51.5 }).position, .5);
  assert.deepEqual([scanState({ status: 'running', stage: 'segmenting' }).phase, scanState({ status: 'running', stage: 'review' }).phase], ['organizing', 'judging']);
  const failed = scanState({ status: 'failed', stage: 'failed', failed_stage: 'ocr', stage_detail: '프레임 3 / 12', progress: 33 });
  assert.equal(failed.phase, 'failed');
  assert.equal(failed.position, .25);
  assert.equal(scanState({ status: 'completed' }).phase, 'done');
});

test('read frames map onto the time-ordered sampling plan, including dense edge slots', () => {
  const media = { duration_ms: 12000, width: 540, height: 960 };
  // 36장 계획의 앞 15장은 시작 3초(0.2초 간격)라서, 5장을 읽었으면 아직 1초 근처를 읽는 중이다.
  const early = scanState({ status: 'running', stage: 'ocr', stage_detail: '프레임 5 / 36', media });
  assert.deepEqual([early.readUpTo, early.lensTime], [800, 1000]);
  const half = scanState({ status: 'running', stage: 'ocr', stage_detail: '프레임 18 / 36', media });
  assert.deepEqual([half.readUpTo, half.lensTime], [5000, 6000]);
  const self = scanState({ status: 'running', stage: 'ocr', stage_detail: '프레임 6 / 12', media, disclosure_input: { kind: 'self' } });
  assert.deepEqual([self.readUpTo, self.lensTime], [5000, 6000]);
  assert.equal(scanState({ status: 'running', stage: 'review', media }).readUpTo, Infinity);
  assert.equal(scanState({ status: 'running', stage: 'scene_detection', media }).readUpTo, -1);
});

test('film cells use real one-second sample frame ids and fit the strip', () => {
  const portrait = filmCells({ width: 1080, height: 1920, duration_ms: 30000 });
  assert.equal(portrait.width, 36);
  assert.equal(portrait.cells.length, 17);
  assert.equal(portrait.cells[0].frame_id, 't000000');
  assert.equal(portrait.cells.at(-1).frame_id, 't029000');
  assert.ok(portrait.cells.every(cell => cell.time_ms % 1000 === 0 && cell.time_ms < 30000));
  assert.equal(filmCells({ width: 1920, height: 1080, duration_ms: 60000 }).cells.length, 6);
  assert.deepEqual(filmCells({ width: 720, height: 1280, duration_ms: 4200 }).cells.map(cell => cell.frame_id), ['t000000', 't001000', 't002000', 't003000', 't004000']);
  // 끝에 0.2초가 다 남지 않으면 서버가 그 초의 프레임을 뽑지 않으므로 칸도 만들지 않는다.
  for (const [duration, last] of [[4100, 't003000'], [15100, 't014000'], [30150, 't029000'], [30200, 't030000']]) {
    assert.equal(filmCells({ width: 1080, height: 1920, duration_ms: duration }).cells.at(-1).frame_id, last);
  }
  assert.equal(frameId(5963.4), 't005963');
});

test('judgment items keep criteria, whole-video checks and disclosure in one filterable list', () => {
  const job = { review: { results: [
    { criterion_id: 'c2', status: 'pass', whole_video: true },
    { criterion_id: 'c1', status: 'fail' },
  ] }, disclosure: { status: 'review' } };
  const items = judgmentItems(job);
  assert.deepEqual(items.map(item => [item.key, item.group]), [['criterion:c1', 'criteria'], ['criterion:c2', 'whole'], ['disclosure', 'disclosure']]);
  assert.deepEqual(filterItems(items, 'review').map(item => item.key), ['disclosure']);
  assert.equal(filterItems(items, 'all').length, 3);
  assert.deepEqual(judgmentItems({}), []);
});
