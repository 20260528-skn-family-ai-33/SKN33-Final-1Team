#!/usr/bin/env node
/**
 * Browser-visible test API. No production imports, database, inference, writes,
 * outbound requests, or real-backend fallback. Everything is process memory.
 * Response shapes/data originate in ../v1/fixtures.mjs and the existing E2Es.
 * Legacy video shapes follow backend/app/video/*.py around one synthetic script.
 */
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { basename, extname } from 'node:path';
import { pathToFileURL } from 'node:url';
import { fixtures, ids, uid, wrap, page as pageDto } from '../v1/fixtures.mjs';

const MAX_BODY = 25 * 1024 * 1024;
const CSRF = 'visual-fixture-csrf';
const NOTICE = '시각 QA용 합성 응답입니다. 실제 모델·DB·업무 승인과 연결되지 않습니다.';
const clone = value => structuredClone(value);
const timestamp = () => new Date().toISOString();
const fail = (code, status = 400, detail) => Object.assign(new Error(code), { code, status, detail });
const isLoopback = host => ['127.0.0.1', 'localhost', '[::1]', '::1'].includes(host);
const get = (map, id) => { const value = map.get(id); if (!value) throw fail('RESOURCE_NOT_FOUND', 404); return value; };
const nonempty = value => typeof value === 'string' && value.trim().length > 0;

function bodyJSON(buffer) {
  try { const value = JSON.parse(buffer.toString('utf8')); if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(); return value; }
  catch { throw fail('INVALID_REQUEST', 422); }
}
async function readBody(request) {
  const chunks = []; let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY) throw fail('PAYLOAD_TOO_LARGE', 413);
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}
async function formData(buffer, contentType) {
  if (!contentType?.startsWith('multipart/form-data;')) throw fail('INVALID_MULTIPART', 422);
  try {
    // Constructing a Request only parses the in-memory body; it sends no request.
    return await new Request('http://127.0.0.1/fixture-parser', {
      method: 'POST', headers: { 'content-type': contentType }, body: buffer,
    }).formData();
  } catch { throw fail('INVALID_MULTIPART', 422); }
}
function selectedItems(body) {
  if (!nonempty(body.raw_requirements) || !Array.isArray(body.items) || body.items.length > 100
    || body.items.some(item => !nonempty(item.id) || typeof item.text !== 'string'
      || !['information', 'visual'].includes(item.type) || !nonempty(item.source) || typeof item.selected !== 'boolean')
    || new Set(body.items.map(item => item.id)).size !== body.items.length) throw fail('INVALID_REQUEST', 422);
  return { raw_requirements: body.raw_requirements, items: clone(body.items), source_documents: Array.isArray(body.source_documents) ? clone(body.source_documents) : null };
}

// ---------------------------------------------------------------------------
// Legacy video review fixture (/api/video-reviews, /api/video-logos).
// One fixed synthetic short-form script stands in for every upload. Field names
// and rules follow backend/app/video (review.py, frames.py, tracks.py, judge.py,
// disclosure.py, logos/routes.py); nothing is decoded, recognised or stored.
// ---------------------------------------------------------------------------
const VIDEO_NOTICE = '시각 QA용 합성 동영상 결과입니다. 실제 영상 디코딩·OCR·판정과 연결되지 않습니다.';
const VIDEO_SCRIPT = { width: 540, height: 960, duration_ms: 12000, fps: 30 };
const TOP_LINE = [184, 84, 356, 114], CAPTION_LINE = [117, 818, 423, 852];
// misreads: what the synthetic OCR returns on that 1 s frame instead of the cue text
// (gives tracks variants, and the last caption a vote without majority).
const VIDEO_CUES = [
  { text: '유료 광고 포함', start_ms: 0, end_ms: 3000, bbox_xyxy: TOP_LINE, role: 'subtitle', misreads: { 2000: '유료 광고 포합' } },
  { text: '예시 자막 첫 번째 줄', start_ms: 0, end_ms: 4000, bbox_xyxy: CAPTION_LINE, role: 'subtitle' },
  { text: '배경 글자 예시', start_ms: 2000, end_ms: 7000, bbox_xyxy: [60, 420, 245, 452], role: 'background' },
  { text: '예시 자막 두 번째 줄', start_ms: 4000, end_ms: 8000, bbox_xyxy: CAPTION_LINE, role: 'subtitle', misreads: { 6000: '예시 자막 두 번쨰 줄' } },
  { text: '작은 글자 예시', start_ms: 5000, end_ms: 10000, bbox_xyxy: [420, 150, 512, 166], role: 'other' },
  { text: '예시 자막 세 번째 줄', start_ms: 8000, end_ms: 12000, bbox_xyxy: CAPTION_LINE, role: 'subtitle', misreads: { 9000: '예시 자막 세 번쨰 줄', 10000: '예시 자막 세 번째 졸' } },
  { text: '유료 광고 포함', start_ms: 9000, end_ms: 12000, bbox_xyxy: TOP_LINE, role: 'subtitle' },
];
const VIDEO_PROGRESS = { queued: 5, extracting: 5, scene_detection: 10, ocr: 15, classifying: 88, segmenting: 90, review: 93, double_check: 97, completed: 100 };
// Time-based progression on read (ms per stage, ~26 s in total), like runView below.
const VIDEO_STAGES = [['queued', 2000], ['extracting', 2000], ['scene_detection', 2000], ['ocr', 14000],
  ['classifying', 1500], ['segmenting', 1500], ['review', 2000], ['double_check', 1000]];
const VIDEO_OCR_MS = 14000, VIDEO_FAIL_MS = 6000 + 0.4 * VIDEO_OCR_MS; // a '실패' project stops 40% into ocr
const VIDEO_LIMITS = { upload_bytes: 500 * 1024 * 1024, duration_seconds: 180, short_side_min: 360, long_side_max: 3840, fps_max: 120, active_jobs: 3, formats: ['mp4', 'mov', 'webm', 'avi'] };
const VIDEO_SAMPLING = '1초 간격 표본 검사 · 시작·끝 3초는 0.2초 간격';
const VIDEO_OCR_FAILURE = '프레임 OCR 실행에 실패했습니다. Paddle 전용 환경과 모델 준비 상태를 확인한 뒤 다시 실행해 주세요.';
const VIDEO_FAIL_WARNING = "시각 QA 합성 실패 사례입니다. 프로젝트 이름에 '실패'가 있어 OCR 단계에서 멈추도록 만든 응답입니다.";
const VIDEO_NO_CLIP = '시각 QA 합성 응답에는 재생 파일이 없습니다.';
export const VIDEO_SEED_IDS = { completed: '5eed0000-0000-4000-8000-000000000001', failed: '5eed0000-0000-4000-8000-000000000002' };
const VIDEO_CODECS = { mov: 'h264', matroska: 'vp9', avi: 'mpeg4' };
const LOGO_VIDEO_SUFFIXES = new Set(['.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v']);
const LOGO_LIST_KEYS = ['id', 'title', 'status', 'stage', 'progress', 'stage_detail', 'error', 'warnings', 'compare_status', 'compare_progress', 'review_id', 'created_at'];
const SEVERITY = { pass: 'info', review: 'warn', fail: 'block' }, REVIEWER = { pass: 'auto', fail: 'auto', review: 'manager' };
const MISMATCH_NOTE = "기준과 한 글자라도 다르게 읽힌 화면 문구가 있어 '불일치'로 사람 확인이 필요합니다.";
const REASONS = {
  pass: '합성 예시: 기준 문구가 자막 트랙에서 확인된 것으로 꾸민 충족 사례입니다. 실제 OCR·판정 결과가 아닙니다. (검사한 시점 기준 충족 — 1초 간격 표본)',
  variants: `합성 예시: 기준 문구가 자막 트랙에 있다고 가정했지만 프레임마다 다르게 읽힌 문구가 있습니다. ${MISMATCH_NOTE}`,
  fail: '합성 예시: 화면 문구가 기준과 다르게 읽힌 것으로 꾸민 위반 사례입니다. 실제 OCR·판정 결과가 아닙니다.',
  mismatch: `합성 예시: 기준 문구와 표기(띄어쓰기·대소문자 등)가 다르게 읽힌 화면 문구가 있다고 가정한 사례입니다. ${MISMATCH_NOTE}`,
  whole: '합성 예시: 비교할 화면 문구를 특정하지 않고 영상 전체에서 담당자가 확인하도록 남긴 사례입니다.',
  visual: '합성 예시: 로고·색상·배치·잘림 등 시각 조건은 OCR로 판정하지 않습니다. 영상 전체에서 담당자가 확인해 주세요.',
};
const DISCLOSURE_NOTE = "'3초'·'15자'·'중간 한 번 이상'은 내부 기준이며 법령의 숫자 기준이 아닙니다.";
const ALLOWED = [/#\s*광고/, /#\s*협찬/, /유료\s*광고/];
const QUOTES = /["“「『]([^"”」』]+)["”」』]|'([^']+)'/g;
const SVG_FONT = "'Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR',sans-serif";

const iso = ms => new Date(ms).toISOString().replace('Z', '000+00:00'); // Python isoformat() shape
const frameName = ms => `t${String(ms).padStart(6, '0')}`;
const squash = text => String(text ?? '').normalize('NFC').replace(/\s+/g, '');
const round4 = value => Math.round(value * 10000) / 10000;
const mean = values => values.reduce((sum, value) => sum + value, 0) / values.length;
const median = values => { const sorted = [...values].sort((a, b) => a - b), mid = sorted.length >> 1; return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2; };
const within = (text, min, max) => typeof text === 'string' && Array.from(text).length >= min && Array.from(text).length <= max;
const pick = (value, keys) => Object.fromEntries(keys.map(key => [key, value[key] ?? null]));
const isUuid = value => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
const xml = text => String(text).replace(/[<>&"']/g, ch => `&#${ch.charCodeAt(0)};`);
function noise(seed) { // deterministic 0..1
  let x = Math.imul((seed >>> 0) ^ 0x9e3779b9, 0x85ebca6b) >>> 0;
  x = Math.imul(x ^ (x >>> 13), 0xc2b2ae35) >>> 0;
  return ((x ^ (x >>> 16)) >>> 0) / 4294967296;
}
function jamo(text) {
  const out = [];
  for (const ch of text) {
    const code = ch.codePointAt(0) - 0xAC00;
    if (code >= 0 && code < 11172) {
      out.push(String.fromCharCode(0x1100 + Math.floor(code / 588)), String.fromCharCode(0x1161 + Math.floor((code % 588) / 28)));
      if (code % 28) out.push(String.fromCharCode(0x11A7 + code % 28));
    } else out.push(ch);
  }
  return out;
}
function editDistance(a, b) {
  let previous = Array.from({ length: b.length + 1 }, (_, index) => index);
  for (let i = 1; i <= a.length; i++) {
    const current = [i];
    for (let j = 1; j <= b.length; j++) current.push(Math.min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a[i - 1] !== b[j - 1])));
    previous = current;
  }
  return previous[b.length];
}
function agreement(a, b) {
  const ja = jamo(squash(a)), jb = jamo(squash(b)), longest = Math.max(ja.length, jb.length);
  return longest ? 1 - editDistance(ja, jb) / longest : 1;
}

// frames.plan_times: 1 s grid over [0, duration) + 0.2 s slots in the first and last 3 s (not for kind 'self').
function videoPlan(edges) {
  const duration = VIDEO_SCRIPT.duration_ms, end = Math.max(duration - 200 + 1, 1), times = new Map();
  for (let t = 0; t < end; t += 1000) times.set(t, 'base');
  if (edges) {
    for (let t = 0; t < Math.min(3000, end); t += 200) if (!times.has(t)) times.set(t, 'head');
    for (let t = Math.ceil(Math.max(0, duration - 3000) / 200) * 200; t < end; t += 200) if (!times.has(t)) times.set(t, 'tail');
  }
  return [...times].sort((a, b) => a[0] - b[0]).map(([time_ms, sampling]) => ({ frame_id: frameName(time_ms), time_ms, sampling }));
}
// Synthetic OCR records for one frame, then the rule_fallback role columns (role/kept/reason).
function frameLines(frameId, ms) {
  return VIDEO_CUES.map((cue, index) => ({ cue, index })).filter(({ cue }) => cue.start_ms <= ms && ms < cue.end_ms)
    .sort((a, b) => a.cue.bbox_xyxy[1] - b.cue.bbox_xyxy[1] || a.cue.bbox_xyxy[0] - b.cue.bbox_xyxy[0])
    .map(({ cue, index }, order) => {
      const a = noise(index * 100003 + ms), b = noise(index * 7919 + ms + 1), misread = cue.misreads?.[ms];
      const [x1, y1, x2, y2] = cue.bbox_xyxy, dx = Math.round(a * 4) - 2, dy = Math.round(b * 2) - 1, kept = cue.role === 'subtitle';
      const confidence = round4(misread ? 0.68 + 0.08 * a : kept ? 0.93 + 0.06 * a : cue.role === 'background' ? 0.62 + 0.2 * a : 0.31 + 0.15 * a);
      return { cue: index, line_id: `${frameId}:paddle_${String(order).padStart(3, '0')}`, text: misread || cue.text, confidence,
        bbox_xyxy: [x1 + dx, y1 + dy, x2 + dx, y2 + dy], role: cue.role, kept,
        reason: kept ? '신뢰도 하한 통과' : cue.role === 'background' ? '배경 글자로 제외' : '신뢰도 < 0.5 제외' };
    });
}
// tracks._finish: vote the text, keep variants, end = last 1 s reading + 1 s.
function finishTrack(number, readings) {
  const votes = new Map(), roles = new Map();
  for (const reading of readings) votes.set(reading.text.trim(), (votes.get(reading.text.trim()) || 0) + 1);
  for (const reading of readings) roles.set(reading.role, (roles.get(reading.role) || 0) + 1);
  const confidence = text => mean(readings.filter(reading => reading.text.trim() === text).map(reading => reading.confidence));
  const [text, count] = [...votes].sort((a, b) => b[1] - a[1] || confidence(b[0]) - confidence(a[0]))[0];
  const agreeing = readings.filter(reading => reading.text.trim() === text);
  const best = agreeing.reduce((top, reading) => (reading.confidence > top.confidence ? reading : top));
  return {
    id: `T${String(number).padStart(3, '0')}`, text, start_ms: readings[0].time_ms,
    end_ms: Math.min(VIDEO_SCRIPT.duration_ms, readings.at(-1).time_ms + 1000),
    confidence: round4(median(agreeing.map(reading => reading.confidence))), bbox_xyxy: best.bbox_xyxy, rep_reading_frame: best.frame_id,
    requires_review: count * 2 <= readings.length, vote: { count, total: readings.length }, role: [...roles].sort((a, b) => b[1] - a[1])[0][0],
    readings: readings.map(reading => ({ frame_id: reading.frame_id, time_ms: reading.time_ms, text: reading.text, confidence: reading.confidence, bbox_xyxy: reading.bbox_xyxy })),
    variants: readings.filter(reading => reading.text.trim() !== text).map(reading => ({ text: reading.text, frame_id: reading.frame_id, time_ms: reading.time_ms })),
  };
}
// One track per kept cue, from its 1 s frames only (the backend groups by IoU + text; the script keeps cues apart).
function videoTracks(frames) {
  const base = frames.filter(frame => frame.sampling === 'base');
  return VIDEO_CUES.map((cue, index) => base.flatMap(frame => frame.lines.filter(line => line.cue === index && line.kept)
    .map(line => ({ frame_id: frame.frame_id, time_ms: frame.time_ms, text: line.text, confidence: line.confidence, bbox_xyxy: line.bbox_xyxy, role: line.role }))))
    .filter(readings => readings.length)
    .sort((a, b) => a[0].time_ms - b[0].time_ms || a[0].bbox_xyxy[1] - b[0].bbox_xyxy[1])
    .map((readings, index) => finishTrack(index + 1, readings));
}
// tracks.build_segments + choose_representatives (no scene cuts; no pixels, so stroke sharpness counts as 0).
function videoSegments(tracks, frames) {
  const duration = VIDEO_SCRIPT.duration_ms, base = frames.filter(frame => frame.sampling === 'base'), byId = new Map(tracks.map(track => [track.id, track]));
  const edges = [...new Set([0, duration, ...tracks.flatMap(track => [track.start_ms, track.end_ms])])].filter(edge => edge >= 0 && edge <= duration).sort((a, b) => a - b);
  const segments = [];
  for (let index = 0; index + 1 < edges.length; index++) {
    const start = edges[index], end = edges[index + 1];
    const visible = tracks.filter(track => track.start_ms <= start && start < track.end_ms).map(track => track.id);
    const frameIds = base.filter(frame => start <= frame.time_ms && frame.time_ms < end).map(frame => frame.frame_id);
    const last = segments.at(-1);
    if (last && last.track_ids.join() === visible.join()) { last.end_ms = end; last.frame_ids.push(...frameIds); continue; }
    segments.push({ start_ms: start, end_ms: end, track_ids: visible, frame_ids: frameIds });
  }
  segments.forEach((segment, index) => {
    segment.id = `S${String(index + 1).padStart(2, '0')}`;
    const candidates = segment.frame_ids.length > 2 ? segment.frame_ids.slice(1, -1) : segment.frame_ids, middle = Math.floor(candidates.length / 2);
    const scored = candidates.map(frameId => {
      const readings = segment.track_ids.flatMap(id => byId.get(id).readings.filter(reading => reading.frame_id === frameId).map(reading => [byId.get(id), reading]));
      return { frameId, confidence: readings.length ? mean(readings.map(([, reading]) => reading.confidence)) : 0,
        agree: readings.length ? mean(readings.map(([track, reading]) => agreement(reading.text, track.text))) : 0 };
    });
    const ranked = scored.some(item => item.confidence) ? [...scored].sort((a, b) => (b.confidence + b.agree) - (a.confidence + a.agree))
      : [...scored].sort((a, b) => Math.abs(candidates.indexOf(a.frameId) - middle) - Math.abs(candidates.indexOf(b.frameId) - middle));
    segment.rep_order = ranked.map(item => item.frameId);
    segment.rep_frame_id = ranked[0]?.frameId ?? null;
  });
  return segments;
}
// frames.coverage: inspected points + every open gap between them (and after the last point).
function videoCoverage(frames) {
  const inspected = frames.map(frame => frame.time_ms), duration = VIDEO_SCRIPT.duration_ms;
  const uninspected = inspected.slice(1).map((end, index) => [inspected[index], end]).filter(([start, end]) => end - start > 0);
  if (inspected.length && inspected.at(-1) < duration) uninspected.push([inspected.at(-1), duration]);
  return { duration_ms: duration, inspected_ms: inspected, uninspected, intervals_ms: { base: 1000, edge: 200, edge_window: 3000 }, note: VIDEO_SAMPLING };
}
function videoAnalysis(edges) {
  const { width, height } = VIDEO_SCRIPT;
  const internal = videoPlan(edges).map(item => ({ ...item, lines: frameLines(item.frame_id, item.time_ms) }));
  const tracks = videoTracks(internal);
  const frames = internal.map(frame => ({ frame_id: frame.frame_id, time_ms: frame.time_ms, sampling: frame.sampling, width, height,
    ocr_status: 'ok', same_as: null, lines: frame.lines.map(({ cue, ...line }) => line) }));
  const lines = frames.flatMap(frame => frame.lines.map(line => ({ ...line, frame_id: frame.frame_id, time_ms: frame.time_ms, sampling: frame.sampling })));
  const keys = new Map();
  for (const track of tracks) keys.set(squash(track.text), [...(keys.get(squash(track.text)) || []), track.id]);
  return { frames, lines, tracks, segments: videoSegments(tracks, frames), coverage: videoCoverage(frames), meta: new Map(frames.map(frame => [frame.frame_id, frame])),
    text_groups: [...keys].filter(([, trackIds]) => trackIds.length > 1).map(([key, trackIds]) => ({ key, track_ids: trackIds })) };
}

// Synthetic mismatch text: a part of the criterion with one character / spacing / case changed.
const DIGIT_SWAP = { 0: '8', 1: '7', 2: '7', 3: '8', 4: '9', 5: '6', 6: '5', 7: '1', 8: '3', 9: '4' };
const VOWEL_SWAP = { 0: 2, 2: 0, 1: 3, 3: 1, 4: 6, 6: 4, 5: 7, 7: 5, 8: 12, 12: 8, 13: 17, 17: 13, 18: 19, 19: 18, 20: 19, 9: 8, 10: 8, 11: 8, 14: 13, 15: 13, 16: 13 };
function expectedPart(text) {
  const quoted = [...text.matchAll(QUOTES)].map(match => (match[1] || match[2]).trim()).filter(Boolean);
  if (quoted.length) return quoted[0];
  const labelled = /^[^:：]*[:：](.*)$/s.exec(text);
  if (labelled?.[1].trim()) return labelled[1].trim();
  const words = text.trim().split(/\s+/), digit = words.findIndex(word => /\d/.test(word));
  return (digit >= 0 ? words.slice(Math.max(0, digit - 1), digit + 1) : words.slice(0, 2)).join(' ');
}
function oneCharacter(expected) {
  const chars = Array.from(expected);
  let index = chars.findIndex(ch => /[0-9]/.test(ch));
  if (index >= 0) chars[index] = DIGIT_SWAP[chars[index]];
  else if ((index = chars.findLastIndex(ch => ch.codePointAt(0) >= 0xAC00 && ch.codePointAt(0) < 0xAC00 + 11172)) >= 0) {
    const code = chars[index].codePointAt(0) - 0xAC00, vowel = Math.floor((code % 588) / 28);
    chars[index] = String.fromCharCode(0xAC00 + code + (VOWEL_SWAP[vowel] - vowel) * 28);
  } else if ((index = chars.findLastIndex(ch => /[a-z]/i.test(ch))) >= 0) {
    const lower = chars[index].toLowerCase(), next = String.fromCharCode((lower.charCodeAt(0) - 97 + 1) % 26 + 97);
    chars[index] = chars[index] === lower ? next : next.toUpperCase();
  } else return null;
  return chars.join('');
}
function caseVariant(expected) {
  const index = expected.search(/[a-z]/i);
  if (index < 0) return null;
  const ch = expected[index];
  return expected.slice(0, index) + (ch === ch.toLowerCase() ? ch.toUpperCase() : ch.toLowerCase()) + expected.slice(index + 1);
}
const spacingVariant = expected => (/\s/.test(expected) ? expected.replace(/\s+/, '') : expected.length >= 2 ? `${expected[0]} ${expected.slice(1)}` : null);
function pickTrack(criterion, index, tracks) {
  const flat = squash(criterion.text), quoted = [...criterion.text.matchAll(QUOTES)].map(match => squash(match[1] || match[2]));
  const named = tracks.find(track => flat.includes(squash(track.text)) || quoted.some(text => text.length >= 2 && squash(track.text).includes(text)));
  if (named) return named;
  if (/광고|협찬|표시/.test(criterion.text)) return tracks.find(track => track.text.includes('광고')) || tracks[0];
  const captions = tracks.filter(track => !track.text.includes('광고'));
  return captions.length ? captions[index % captions.length] : tracks[index % tracks.length];
}
// judge._expand: each evidence track becomes one entry per segment it is visible in.
function expandEvidence(trackIds, analysis) {
  const out = [];
  for (const trackId of trackIds) {
    const track = analysis.tracks.find(item => item.id === trackId);
    if (!track) continue;
    for (const segment of analysis.segments) {
      if (!segment.track_ids.includes(track.id)) continue;
      const inside = track.readings.filter(reading => segment.start_ms <= reading.time_ms && reading.time_ms < segment.end_ms);
      if (!inside.length) continue;
      const reading = inside.find(item => item.frame_id === segment.rep_frame_id) || inside[0], frame = analysis.meta.get(reading.frame_id);
      out.push({ track_id: track.id, segment_id: segment.id, start_ms: segment.start_ms, end_ms: segment.end_ms, frame_id: reading.frame_id,
        time_ms: reading.time_ms, width: frame.width, height: frame.height, bbox_xyxy: reading.bbox_xyxy, text: reading.text });
    }
  }
  return out;
}
// Deterministic mix by position: pass, fail (+one_character), whole-video review, review (+spacing/case); visual = whole video.
function videoReview(criteria, analysis) {
  if (!criteria.length) return { results: [], mismatch: [], calls: 0 };
  const mismatch = [];
  const results = criteria.map((criterion, index) => {
    const slot = criterion.type === 'visual' ? 'whole' : ['pass', 'fail', 'whole', 'mismatch'][index % 4];
    const result = { criterion_id: criterion.id, criterion: criterion.text, type: criterion.type, status: 'review', observed_text: '', reason: '', evidence: [] };
    const own = [];
    if (slot === 'whole') result.reason = criterion.type === 'visual' ? REASONS.visual : REASONS.whole;
    else {
      const track = pickTrack(criterion, index, analysis.tracks);
      result.evidence = [{ text: track.text, bbox_xyxy: track.bbox_xyxy, ocr_id: track.id, confidence: track.confidence, recognition_engine: 'paddleocr' }];
      result.observed_text = track.text;
      if (track.variants.length) own.push({ criterion_id: criterion.id, track_id: track.id, kind: 'variants', expected: null, observed: track.text,
        variants: [...new Set(track.variants.map(variant => variant.text))].sort() });
      if (slot !== 'pass') {
        const expected = expectedPart(criterion.text);
        const [kind, observed] = slot === 'fail' ? ['one_character', oneCharacter(expected)]
          : caseVariant(expected) ? ['case', caseVariant(expected)] : ['spacing', spacingVariant(expected)];
        if (observed && squash(expected).length >= 2) own.push({ criterion_id: criterion.id, track_id: track.id, kind, expected, observed });
      }
      // judge.judge: a pass with any mismatch is lowered to review; a fail stays a fail.
      result.status = slot === 'fail' ? 'fail' : own.length ? 'review' : 'pass';
      result.reason = slot === 'fail' ? REASONS.fail : result.status === 'pass' ? REASONS.pass : slot === 'pass' ? REASONS.variants : REASONS.mismatch;
    }
    mismatch.push(...own);
    const evidenceIds = result.evidence.map(item => item.ocr_id);
    result.mismatch = own;
    result.evidence_segments = expandEvidence([...evidenceIds, ...own.map(item => item.track_id).filter(id => !evidenceIds.includes(id))], analysis);
    result.whole_video = result.status === 'review' && !result.evidence_segments.length;
    result.severity = SEVERITY[result.status];
    result.reviewer = REVIEWER[result.status];
    return result;
  });
  return { results, mismatch, calls: 1 };
}
function phraseClass(text) {
  const value = String(text || '').normalize('NFC');
  if (/^\s*협찬\s*[:：]/.test(value) || /^\s*협찬\s*사(?![\p{L}\p{N}_])/u.test(value)) return 'excluded';
  if (ALLOWED.some(pattern => pattern.test(value))) return 'allowed';
  if ([/^\s*체험단\s*$/, /^\s*#?\s*AD\s*$/i, /^\s*#?\s*sponsor(ed)?\s*$/i].some(pattern => pattern.test(value))) return 'disallowed';
  if (/광고|협찬|체험단|(?<![\p{L}\p{N}_])AD(?![\p{L}\p{N}_])|sponsor/iu.test(value)) return 'ambiguous';
  return 'none';
}
// disclosure.judge_disclosure over the script's OCR lines (12 s, so never the short-video branch).
function videoDisclosure(info, analysis) {
  if (info.kind === 'self') return { applicable: false, status: 'not_applicable', severity: 'info', reviewer: 'auto',
    reason: '광고주 자체 광고로 입력되어 추천·보증 표시 대상이 아닙니다.', rules: {}, notes: [DISCLOSURE_NOTE] };
  const duration = VIDEO_SCRIPT.duration_ms, capped = info.kind === 'unknown', allowedIn = text => ALLOWED.some(pattern => pattern.test(text));
  const hits = analysis.lines.map(line => ({ frame_id: line.frame_id, time_ms: line.time_ms, text: line.text, bbox_xyxy: line.bbox_xyxy, class: phraseClass(line.text) }))
    .filter(hit => hit.class !== 'none');
  const allowed = hits.filter(hit => hit.class === 'allowed'), disallowed = hits.filter(hit => hit.class === 'disallowed');
  const window = (start, end, baseOnly = false) => {
    const frameIds = new Set(analysis.frames.filter(frame => start <= frame.time_ms && frame.time_ms < end && (!baseOnly || frame.sampling === 'base')).map(frame => frame.frame_id));
    const evidence = allowed.filter(hit => frameIds.has(hit.frame_id));
    return { status: evidence.length ? 'pass' : 'review', evidence: evidence.slice(0, 5), frames: frameIds.size, ocr_ok: frameIds.size > 0 };
  };
  const rules = { R0: capped ? { status: 'review', note: "영상 성격이 '모름'이라 모든 결과를 최대 '확인 필요'로 둡니다." } : { status: 'pass', note: '적용 대상 확정' } };
  rules.R1 = { status: disallowed.length && !allowed.length ? 'fail' : allowed.length ? 'pass' : 'review',
    counts: Object.fromEntries(['allowed', 'disallowed', 'ambiguous', 'excluded'].map(kind => [kind, hits.filter(hit => hit.class === kind).length])),
    evidence: (disallowed.length ? disallowed : hits.filter(hit => hit.class === 'ambiguous')).slice(0, 5),
    note: "불허 문구만 있으면 위반 후보, 엔딩의 '협찬: ○○' 크레딧은 표시 문구로 보지 않습니다." };
  const title = info.title.normalize('NFC'), head = Array.from(title).slice(0, 15).join('');
  rules.R2 = { status: allowedIn(head) ? 'pass' : 'review',
    note: '제목 앞 15자 안의 허용 문구' + (!allowedIn(head) && allowedIn(title) ? ' — 뒤쪽에만 있어 모바일에서 잘릴 수 있습니다.' : '') };
  rules.R3a = { ...window(0, 3000), note: '[0, 3초) 0.2초 프레임' };
  rules.R3b = { ...window(duration - 3000, duration), note: '[끝−3초, 끝) 0.2초 프레임' };
  const middle = window(3000, duration - 3000, true);
  rules.R3c = { ...middle, note: '중간 1초 프레임에서 한 번 이상' + (middle.evidence.length ? '' : ' — 중간 반복 미확인') };
  const captionAllowed = allowedIn(info.caption);
  rules.R4 = { status: captionAllowed && !allowed.length && rules.R2.status !== 'pass' ? 'review' : 'pass', note: '캡션에만 허용 문구가 있으면 확인 필요' };
  rules.R5 = { status: 'review', note: '글자 크기·대비·노출 시간(명확한 인식)은 후속 — 담당자 확인' };
  rules.R6 = { status: 'review', note: 'AI 가상인물 등장 여부는 후속 — 담당자 확인' };
  const screenOk = info.celebrity_exposure ? rules.R3a.status === 'pass' && rules.R3b.status === 'pass'
    : ['R3a', 'R3b', 'R3c'].every(key => ['pass', 'skipped'].includes(rules[key].status));
  const textTime = rules.R2.status === 'pass' || screenOk ? 'pass' : 'review';
  const headTailOk = rules.R3a.ocr_ok && rules.R3b.ocr_ok, stated = Boolean(info.title || info.caption);
  const noTrace = !allowed.length && !allowedIn(title) && !captionAllowed, ambiguous = hits.some(hit => hit.class === 'ambiguous');
  let status = 'review', reason;
  if (!capped && stated && headTailOk && noTrace && !ambiguous) { status = 'fail'; reason = '적용 대상 영상인데 제목·캡션·화면 어디에도 허용 표시 문구가 없습니다.'; }
  else if (ambiguous && noTrace && !capped && stated && headTailOk) reason = "허용 표시 문구는 없지만 모호한 표시(예: '광고'·'협찬')가 있어 자동 위반으로 확정하지 않고 확인 필요로 둡니다.";
  else reason = textTime === 'pass' ? '표본에서 표시 문구·시간은 확인했지만, 시인성(R5)·가상인물(R6)이 남아 전체 광고 적합은 확인 필요입니다.'
    : '필수 표시를 모두 확인하지 못했습니다. 중간 반복 미확인 등은 위반으로 올리지 않습니다.';
  // Fixture-only rule so the blocking state can be inspected: no '광고' in title/tag shows as fail.
  if (!capped && status !== 'fail' && !`${info.title}\n${info.caption}`.includes('광고')) {
    status = 'fail';
    reason = "합성 예시: 게시물 제목·태그에 '광고' 표시가 없어 위반으로 표시한 시각 QA 사례입니다. 실제 판정은 화면에 허용 표시 문구가 있으면 위반으로 확정하지 않습니다.";
  }
  return { applicable: true, text_time_status: textTime, status, severity: status === 'fail' ? 'block' : 'warn', reviewer: status === 'fail' ? 'auto' : 'manager',
    reason, rules, notes: [DISCLOSURE_NOTE, '표본 문구 확인과 표시 규정 충족은 다른 상태입니다.'] };
}
// Frame image: neutral gradient and a light disc drifting with time, the cues visible at that time at
// their bbox, and a corner label. Colours follow the clip renderer that reads /__fixture/video-script.
function frameSvg(ms) {
  const { width, height, duration_ms: duration } = VIDEO_SCRIPT, phase = ms / duration;
  const rgb = values => `rgb(${values.map(value => Math.round(value)).join(',')})`;
  const cues = VIDEO_CUES.filter(cue => cue.start_ms <= ms && ms < cue.end_ms).map(cue => {
    const [x1, y1, x2, y2] = cue.bbox_xyxy, h = y2 - y1, size = Math.max(14, Math.floor(h * 0.62));
    if (cue.role === 'background') return `<text x="${x1}" y="${Math.round(y1 + size * 0.9)}" font-size="${size}" fill="rgb(90,90,86)">${xml(cue.text)}</text>`;
    return `<rect x="${x1 - 6}" y="${y1 - 4}" width="${x2 - x1 + 12}" height="${h + 8}" rx="8" fill="rgb(23,23,23)"/>`
      + `<text x="${(x1 + x2) / 2}" y="${Math.round(y1 + h * 0.12 + size * 0.9)}" font-size="${size}" font-weight="700" fill="#ffffff" text-anchor="middle">${xml(cue.text)}</text>`;
  }).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family="${SVG_FONT}">`
    + `<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${rgb([236 - 40 * phase, 232 - 10 * phase, 224 + 10 * phase])}"/>`
    + `<stop offset="1" stop-color="${rgb([120 + 50 * phase, 140, 150 - 30 * phase])}"/></linearGradient></defs>`
    + `<rect width="${width}" height="${height}" fill="url(#bg)"/>`
    + `<circle cx="${Math.round(width * (0.2 + 0.6 * phase))}" cy="${Math.round(height * 0.38)}" r="70" fill="rgb(250,250,246)"/>`
    + `${cues}<text x="16" y="${height - 16}" font-size="18" fill="rgb(40,40,38)">${(ms / 1000).toFixed(2).padStart(5, '0')}s · 합성 프레임</text></svg>`;
}

// Streams a multipart body: text fields are kept (1 MiB each), file parts are only
// counted, sniffed (first 16 bytes) and hashed, then discarded. Nothing is stored.
async function streamMultipart(request, fileLimit) {
  const match = /^multipart\/form-data\s*;.*?\bboundary=(?:"([^"]+)"|([^\s;]+))/i.exec(String(request.headers['content-type'] || ''));
  const invalid = () => fail('INVALID_MULTIPART', 422);
  const opening = Buffer.from(`--${match?.[1] || match?.[2]}`), delimiter = Buffer.from(`\r\n--${match?.[1] || match?.[2]}`), blank = Buffer.from('\r\n\r\n');
  const fields = new Map(), files = new Map();
  let pending = Buffer.alloc(0), state = 'start', part = null, parts = 0, error = match ? null : invalid();
  const write = bytes => {
    if (!bytes.length) return;
    if (!part.file) {
      part.size += bytes.length;
      if (part.size > 1024 * 1024) throw fail('INVALID_REQUEST', 422);
      part.chunks.push(bytes); return;
    }
    const limit = fileLimit(part.name);
    part.file.size += bytes.length;
    if (part.file.size > limit.bytes) throw fail('PAYLOAD_TOO_LARGE', 413, limit.message);
    if (part.file.head.length < 16) part.file.head = Buffer.concat([part.file.head, bytes.subarray(0, 16 - part.file.head.length)]);
    part.hash.update(bytes);
  };
  const open = text => {
    const headers = new Map(text.split('\r\n').filter(line => line.includes(':')).map(line => [line.slice(0, line.indexOf(':')).trim().toLowerCase(), line.slice(line.indexOf(':') + 1).trim()]));
    const disposition = headers.get('content-disposition') || '';
    const name = /;\s*name="([^"]*)"/i.exec(disposition)?.[1], filename = /;\s*filename="([^"]*)"/i.exec(disposition)?.[1];
    if (!/^form-data\b/i.test(disposition) || name === undefined || ++parts > 64) throw invalid();
    part = filename === undefined ? { name, size: 0, chunks: [] }
      : { name, hash: createHash('sha256'), file: { filename, type: headers.get('content-type') || '', size: 0, head: Buffer.alloc(0) } };
  };
  const close = () => {
    if (part.file) files.set(part.name, [...(files.get(part.name) || []), { ...part.file, sha256: part.hash.digest('hex') }]);
    else fields.set(part.name, [...(fields.get(part.name) || []), Buffer.concat(part.chunks).toString('utf8')]);
    part = null;
  };
  const step = () => {
    for (;;) {
      if (state === 'start') {
        const at = pending.indexOf(opening);
        if (at < 0) { if (pending.length > 4096) throw invalid(); return; }
        if (pending.length < at + opening.length + 2) return;
        const tail = pending.toString('latin1', at + opening.length, at + opening.length + 2);
        pending = pending.subarray(at + opening.length + 2);
        if (tail === '--') { state = 'done'; return; }
        if (tail !== '\r\n') throw invalid();
        state = 'head';
      } else if (state === 'head') {
        const at = pending.indexOf(blank);
        if (at < 0) { if (pending.length > 16384) throw invalid(); return; }
        open(pending.toString('utf8', 0, at));
        pending = pending.subarray(at + blank.length); state = 'body';
      } else if (state === 'body') {
        const at = pending.indexOf(delimiter);
        if (at < 0) { // keep a possible partial delimiter for the next chunk
          const safe = pending.length - (delimiter.length - 1);
          if (safe > 0) { write(pending.subarray(0, safe)); pending = pending.subarray(safe); }
          return;
        }
        write(pending.subarray(0, at)); pending = pending.subarray(at);
        if (pending.length < delimiter.length + 2) return;
        const tail = pending.toString('latin1', delimiter.length, delimiter.length + 2);
        pending = pending.subarray(delimiter.length + 2); close();
        if (tail === '--') { state = 'done'; return; }
        if (tail !== '\r\n') throw invalid();
        state = 'head';
      } else return;
    }
  };
  for await (const chunk of request) { // after an error the rest is drained, not kept
    if (error || state === 'done') continue;
    pending = pending.length ? Buffer.concat([pending, chunk]) : chunk;
    try { step(); } catch (failure) { error = failure; pending = Buffer.alloc(0); }
  }
  if (error) throw error;
  if (state !== 'done') throw invalid();
  return { field: name => fields.get(name)?.[0], file: name => files.get(name)?.[0], files: name => files.get(name) || [] };
}
function sniffContainer(head) { // frames.sniff_container
  if (head.length >= 12 && ['ftyp', 'moov', 'mdat', 'wide', 'free', 'skip'].includes(head.toString('latin1', 4, 8))) return 'mov';
  if (head.length >= 4 && head.subarray(0, 4).equals(Buffer.from([0x1a, 0x45, 0xdf, 0xa3]))) return 'matroska';
  if (head.toString('latin1', 0, 4) === 'RIFF' && head.toString('latin1', 8, 12) === 'AVI ') return 'avi';
  return null;
}
const isImage = head => head.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))
  || (head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff) || head.toString('latin1', 0, 4) === 'GIF8'
  || (head.toString('latin1', 0, 4) === 'RIFF' && head.toString('latin1', 8, 12) === 'WEBP') || head.toString('latin1', 0, 2) === 'BM';
const videoMedia = (container, bytes, sha256) => ({ codec: VIDEO_CODECS[container], width: VIDEO_SCRIPT.width, height: VIDEO_SCRIPT.height, fps: VIDEO_SCRIPT.fps,
  rotation: 0, hdr: false, duration_ms: VIDEO_SCRIPT.duration_ms, container_duration_ms: VIDEO_SCRIPT.duration_ms, container, bytes, sha256 });
function videoCriteria(raw) { // criteria_contracts.Criterion (extra keys forbidden, strings stripped) + unique ids
  const invalid = () => fail('INVALID_REQUEST', 422, '검수 기준 또는 광고 입력 형식을 확인해 주세요.');
  let parsed;
  try { parsed = raw.length <= 250000 ? JSON.parse(raw) : null; } catch { throw invalid(); }
  if (!Array.isArray(parsed) || parsed.length > 100) throw invalid();
  const keys = new Set(['id', 'text', 'type', 'source', 'document_condition']), strip = value => (typeof value === 'string' ? value.trim() : null);
  const frozen = parsed.map(item => {
    if (!item || typeof item !== 'object' || Array.isArray(item) || Object.keys(item).some(key => !keys.has(key))) throw invalid();
    const id = strip(item.id), text = strip(item.text), source = item.source === undefined ? 'user' : strip(item.source), condition = item.document_condition ?? null;
    if (!within(id, 1, 100) || !within(text, 1, 500) || !within(source, 1, 500) || !['information', 'visual'].includes(item.type)) throw invalid();
    if (condition !== null && (typeof condition !== 'object' || Array.isArray(condition)
      || Object.keys(condition).some(key => !['label', 'requirement', 'level', 'inspection', 'sources'].includes(key))
      || !within(strip(condition.label), 1, 80) || !within(strip(condition.requirement), 1, 400) || !['필수', '권장', '선택', '명시 없음'].includes(condition.level)
      || !within(strip(condition.inspection), 0, 1200) || (condition.sources !== undefined && !Array.isArray(condition.sources)))) throw invalid();
    const kept = condition && { ...clone(condition) };
    if (kept && !kept.sources?.length) delete kept.sources;
    return { id, text, type: item.type, source, document_condition: kept };
  });
  if (new Set(frozen.map(item => item.id)).size !== frozen.length) throw invalid();
  return frozen;
}
function videoDisclosureInput(raw) { // disclosure.validate_input
  let value;
  try { value = JSON.parse(raw); } catch { throw fail('INVALID_REQUEST', 422, '검수 기준 또는 광고 입력 형식을 확인해 주세요.'); }
  if (!value || (Array.isArray(value) && !value.length)) value = {};
  const kind = value.kind === undefined ? 'unknown' : value.kind, text = (item, size) => Array.from(String(item || '')).slice(0, size).join('');
  if (typeof value !== 'object' || Array.isArray(value) || !['self', 'endorsement', 'partial', 'unknown'].includes(kind)) throw fail('INVALID_REQUEST', 422, '검수 기준 또는 광고 입력 형식을 확인해 주세요.');
  return { kind, celebrity_exposure: Boolean(value.celebrity_exposure ?? false), title: text(value.title, 500), caption: text(value.caption, 5000), platform: text(value.platform, 50) };
}
async function loadClip(path) {
  const data = await readFile(path), format = extname(path).slice(1).toLowerCase();
  return { data, format, type: `video/${format}`, container: format === 'webm' ? 'matroska' : 'mov', sha256: createHash('sha256').update(data).digest('hex') };
}

function createVideoFixture(clip) {
  const analyses = new Map([[true, videoAnalysis(true)], [false, videoAnalysis(false)]]);
  const jobs = new Map(), logoJobs = new Map();
  const elapsedOf = (entry, now) => { const age = Math.max(0, now - entry.createdMs); return entry.failAt === null ? age : Math.min(age, entry.failAt); };
  const failed = (entry, now) => entry.failAt !== null && now - entry.createdMs >= entry.failAt;
  function stageAt(elapsed) {
    let start = 0;
    for (const [stage, span] of VIDEO_STAGES) { if (elapsed < start + span) return { stage, start }; start += span; }
    return { stage: 'completed', start };
  }
  const active = (entry, now) => !failed(entry, now) && stageAt(elapsedOf(entry, now)).stage !== 'completed';
  const activeCount = now => [...jobs.values()].filter(entry => active(entry, now)).length;
  const runtime = total => ({ engine: 'paddleocr', pipeline: 'visual-http-fixture', device: 'fixture', load_seconds: 1.2, seconds_per_frame: 0.4, ocr_frames: total, ocr_success_ratio: 1 });

  function addJob({ id = randomUUID(), createdMs, projectName, criteriaVersion = 1, criteria, disclosure, media, logoQuery = false }) {
    const entry = { id, createdMs, created_at: iso(createdMs), project_name: projectName, criteria_version: criteriaVersion, criteria, disclosure_input: disclosure,
      media, logo_query: logoQuery, waiting: activeCount(Date.now()), analysis: analyses.get(disclosure.kind !== 'self'),
      failAt: projectName.includes('실패') ? VIDEO_FAIL_MS : null, result: null };
    jobs.set(id, entry);
    return entry;
  }
  function completedFields(entry) { // review.Pipeline.run return value
    const { analysis } = entry, review = videoReview(entry.criteria, analysis), kept = analysis.lines.filter(line => line.kept).length;
    const count = status => review.results.filter(result => result.status === status).length;
    return {
      classifier: 'rule_fallback', frames: analysis.frames, coverage: analysis.coverage, scenes: { status: 'completed', cut_ms: [] },
      tracks: analysis.tracks, text_groups: analysis.text_groups, segments: analysis.segments, review,
      disclosure: videoDisclosure(entry.disclosure_input, analysis),
      logo_match: { status: 'skipped', reason: entry.logo_query ? '시각 QA 합성 응답은 지정 로고 대조를 실행하지 않습니다.' : '검사할 지정 로고를 올리지 않았습니다.' },
      // Same contract as backend review.py: available=true only when a separate playback MP4 was made (AVI).
      // MP4/MOV/WEBM originals are streamed as-is and report available=false with this reason.
      playback: { available: false, reason: '원본이 브라우저에서 바로 재생됩니다.' },
      double_check: { status: 'skipped', reason: '이중 검수(6-1)는 기본 꺼짐입니다.' }, warnings: [VIDEO_NOTICE],
      counts: { frames: analysis.frames.length, ocr_frames: analysis.frames.length, lines: analysis.lines.length, kept, excluded: analysis.lines.length - kept,
        tracks: analysis.tracks.length, segments: analysis.segments.length, results: { pass: count('pass'), fail: count('fail'), review: count('review') }, mismatch: review.mismatch.length },
    };
  }
  function videoView(entry, now) {
    const { analysis } = entry, total = analysis.frames.length, elapsed = elapsedOf(entry, now), { stage, start } = stageAt(elapsed);
    let progress = VIDEO_PROGRESS[stage], detail = null, changed = start;
    // stage_detail carries over when the backend's update() does not replace it (extracting, classifying, double_check).
    if (stage === 'queued' || stage === 'extracting') detail = `대기 ${entry.waiting}건`;
    else if (stage === 'scene_detection') detail = `프레임 ${total}장`;
    else if (stage === 'ocr') {
      const done = Math.min(total, Math.floor(total * (elapsed - start) / VIDEO_OCR_MS));
      progress = VIDEO_PROGRESS.ocr + Math.floor(73 * done / total); detail = `프레임 ${done} / ${total}`; changed = start + Math.ceil(done * VIDEO_OCR_MS / total);
    } else if (stage === 'classifying') detail = `프레임 ${total} / ${total}`;
    else if (stage === 'segmenting') detail = `줄 ${analysis.lines.length}개`;
    else if (stage === 'review' || stage === 'double_check') detail = `구간 ${analysis.segments.length}개`;
    const view = { id: entry.id, status: stage === 'completed' ? 'completed' : stage === 'queued' ? 'queued' : 'running', stage, progress, stage_detail: detail,
      project_name: entry.project_name, criteria_version: entry.criteria_version, criteria: entry.criteria, disclosure_input: entry.disclosure_input,
      media: entry.media, logo_query: entry.logo_query, runtime: ['queued', 'extracting', 'scene_detection', 'ocr'].includes(stage) ? null : runtime(total),
      warnings: [], error: null, created_at: entry.created_at, updated_at: iso(entry.createdMs + (failed(entry, now) ? entry.failAt : changed)) };
    if (failed(entry, now)) return { ...view, status: 'failed', stage: 'failed', warnings: [VIDEO_FAIL_WARNING], error: VIDEO_OCR_FAILURE, failed_stage: 'ocr' };
    if (stage === 'completed') return { ...view, ...(entry.result ||= completedFields(entry)) };
    return view;
  }
  function logoView(entry, now) { // logos.routes run_candidates: queued -> running(frames, candidates) -> candidates_ready
    const age = Math.max(0, now - entry.createdMs), view = { id: entry.id, title: entry.title, status: 'queued', stage: 'queued', progress: 0,
      logos: entry.logos, review_id: entry.review_id, created_at: entry.created_at, updated_at: entry.created_at };
    if (age < 1000) return view;
    const stopped = entry.failAt !== null && age - 1000 >= entry.failAt, run = stopped ? entry.failAt : age - 1000, count = entry.logos.length;
    let state;
    if (run >= 6000) state = { at: 6000, status: 'candidates_ready', stage: 'candidates_ready', progress: 100, stage_detail: null, seconds: VIDEO_SCRIPT.duration_ms / 1000 };
    else if (run < 500) state = { at: 0, status: 'running', stage: 'frames', progress: 1 };
    else if (run < 4000) {
      const second = Math.min(12, 1 + Math.floor((run - 500) * 12 / 3500));
      state = { at: 500 + Math.ceil((second - 1) * 3500 / 12), status: 'running', stage: 'candidates', progress: Math.round(5 + 60 * second / 12), stage_detail: `후보 탐지 ${second}/12초` };
    } else {
      const done = Math.min(count, Math.floor((run - 4000) * count / 2000));
      state = { at: 4000 + Math.ceil(done * 2000 / count), status: 'running', stage: 'candidates', progress: Math.round(70 + 25 * done / count), stage_detail: done ? `후보 OCR ${done}/${count}` : '후보 OCR' };
    }
    const { at, ...rest } = state;
    Object.assign(view, rest, { updated_at: iso(entry.createdMs + 1000 + (stopped ? entry.failAt : at)) });
    if (stopped) Object.assign(view, { status: 'failed', error: '후보 찾기에 실패했습니다: 시각 QA 합성 실패 사례입니다.' });
    return view;
  }

  async function submitReview(request) { // review.submit + accept, with the upload hashed and discarded
    const form = await streamMultipart(request, name => (name === 'video'
      ? { bytes: VIDEO_LIMITS.upload_bytes, message: `동영상은 ${VIDEO_LIMITS.upload_bytes / 1048576}MB 이하로 업로드해 주세요.` }
      : { bytes: 8 * 1048576, message: '로고 이미지는 8MB 이하로 올려 주세요.' }));
    const video = form.file('video'), logo = form.file('logo'), version = form.field('criteria_version') ?? '1', projectName = form.field('project_name') ?? '동영상 검수';
    if (!video) throw fail('INVALID_REQUEST', 422, '동영상 파일이 필요합니다.');
    if (!/^\s*\d+\s*$/.test(version) || Number(version) < 1 || Number(version) > 1_000_000) throw fail('INVALID_REQUEST', 422, '기준 버전을 확인해 주세요.');
    if (!within(projectName, 1, 120)) throw fail('INVALID_REQUEST', 422, '프로젝트 이름은 1~120자로 입력해 주세요.');
    const criteria = videoCriteria(form.field('criteria') ?? '[]'), disclosure = videoDisclosureInput(form.field('disclosure') ?? '{}');
    if (activeCount(Date.now()) >= VIDEO_LIMITS.active_jobs) throw fail('TOO_MANY_VIDEO_JOBS', 429, `동영상 검수는 실행·대기 합쳐 ${VIDEO_LIMITS.active_jobs}건까지 받습니다. 잠시 후 다시 시도해 주세요.`);
    if (!video.size) throw fail('EMPTY_VIDEO', 400, '빈 동영상 파일입니다.');
    const container = sniffContainer(video.head);
    if (!container) throw fail('UNSUPPORTED_VIDEO', 415, 'MP4·MOV·WEBM·AVI 동영상만 지원합니다.');
    const logoQuery = Boolean(logo?.filename && logo.size);
    if (logoQuery && !isImage(logo.head)) throw fail('UNSUPPORTED_LOGO', 415, '로고는 이미지 파일(PNG·JPG 등)이어야 합니다.');
    const entry = addJob({ createdMs: Date.now(), projectName, criteriaVersion: Number(version), criteria, disclosure, media: videoMedia(container, video.size, video.sha256), logoQuery });
    return { id: entry.id, status: 'queued' };
  }
  async function submitLogo(request) { // logos.routes.submit
    const form = await streamMultipart(request, name => (name === 'video' ? { bytes: 1024 ** 3, message: '영상이 너무 큽니다.' } : { bytes: 8 * 1048576, message: '원본 로고 이미지가 너무 큽니다.' }));
    const reviewId = form.field('review_id') ?? null, files = form.files('logos'), video = form.file('video'), title = form.field('title') ?? '영상 로고 검수';
    if (!video || !within(title, 1, 120)) throw fail('INVALID_REQUEST', 422, '영상과 제목(1~120자)을 확인해 주세요.');
    if (reviewId !== null && !isUuid(reviewId)) throw fail('INVALID_REQUEST', 422, 'review_id가 올바르지 않습니다.');
    if (files.length < 1 || files.length > 12) throw fail('INVALID_REQUEST', 422, '원본 로고는 1~12장입니다.');
    let texts;
    try { texts = JSON.parse(form.field('logo_texts') ?? '[]'); } catch { throw fail('INVALID_REQUEST', 422, 'logo_texts는 JSON 배열이어야 합니다.'); }
    if (!Array.isArray(texts) || ![0, files.length].includes(texts.length) || !texts.every(item => typeof item === 'string' && Array.from(item).length <= 300)) {
      throw fail('INVALID_REQUEST', 422, 'logo_texts는 원본 로고 수와 같은 길이의 문자열 배열이어야 합니다.');
    }
    if (!LOGO_VIDEO_SUFFIXES.has(extname(video.filename).toLowerCase())) throw fail('INVALID_REQUEST', 422, '지원하는 영상 형식이 아닙니다.');
    if (files.some(file => !isImage(file.head))) throw fail('INVALID_REQUEST', 422, '원본 로고가 이미지가 아닙니다.');
    const createdMs = Date.now(), entry = { id: randomUUID(), title, review_id: reviewId, createdMs, created_at: iso(createdMs), failAt: title.includes('실패') ? 2500 : null,
      logos: files.map((file, index) => ({ name: basename(file.filename || `logo_${index}`, extname(file.filename || `logo_${index}`)), file: `${index}.png`, text: texts[index] ?? '' })) };
    logoJobs.set(entry.id, entry);
    return { id: entry.id, status: 'queued' };
  }
  function sendClip(request, response) { // GET/HEAD with single-range support so the player can seek
    if (!clip) throw fail('RESOURCE_NOT_FOUND', 404, VIDEO_NO_CLIP);
    const size = clip.data.length, headers = { 'Content-Type': clip.type, 'Accept-Ranges': 'bytes' }, range = request.headers.range;
    const body = (start, end) => (request.method === 'HEAD' ? undefined : clip.data.subarray(start, end + 1));
    if (range === undefined) { response.writeHead(200, { ...headers, 'Content-Length': size }); response.end(body(0, size - 1)); return; }
    const match = /^bytes=(\d*)-(\d*)$/.exec(String(range).trim());
    let start = NaN, end = NaN;
    if (match && match[1]) { start = Number(match[1]); end = match[2] ? Math.min(Number(match[2]), size - 1) : size - 1; }
    else if (match && match[2] && Number(match[2]) > 0) { start = Math.max(0, size - Number(match[2])); end = size - 1; }
    if (!(start <= end) || start >= size) { response.writeHead(416, { ...headers, 'Content-Range': `bytes */${size}` }); response.end(); return; }
    response.writeHead(206, { ...headers, 'Content-Range': `bytes ${start}-${end}/${size}`, 'Content-Length': end - start + 1 });
    response.end(body(start, end));
  }

  const started = Date.now(), seedDisclosure = { kind: 'endorsement', celebrity_exposure: false, title: '#광고 합성 예시 영상', caption: '', platform: '' };
  const seedCriteria = [['"예시 자막 첫 번째 줄" 문구 포함', 'information'], ['광고 표시 문구가 시작 3초 안에 보일 것', 'information'], ['화면 글자가 잘리지 않을 것', 'visual']]
    .map(([text, type], index) => ({ id: `criterion-${index + 1}`, text, type, source: text, document_condition: null }));
  const seedMedia = clip ? videoMedia(clip.container, clip.data.length, clip.sha256) : videoMedia('matroska', 0, createHash('sha256').digest('hex'));
  addJob({ id: VIDEO_SEED_IDS.completed, createdMs: started - 30 * 60000, projectName: '시각 QA 예시 숏폼', criteria: seedCriteria, disclosure: seedDisclosure, media: seedMedia });
  addJob({ id: VIDEO_SEED_IDS.failed, createdMs: started - 60 * 60000, projectName: '시각 QA 실패 예시', criteria: seedCriteria, disclosure: seedDisclosure, media: seedMedia });

  return {
    script: () => ({ ...VIDEO_SCRIPT, cues: VIDEO_CUES.map(({ text, start_ms, end_ms, bbox_xyxy, role }) => ({ text, start_ms, end_ms, bbox_xyxy, role })), notice: VIDEO_NOTICE }),
    async handle(request, response, { path, method, url, json }) {
      const now = Date.now();
      if (path === '/api/video-reviews/status' && method === 'GET') {
        return json({ ready: true, ffmpeg: true, ocr: true, detail: VIDEO_NOTICE, logo: { ready: true, detail: null }, readonly: false,
          pipeline: 'visual-http-fixture', limits: VIDEO_LIMITS, sampling: VIDEO_SAMPLING });
      }
      if (path === '/api/video-reviews' && method === 'GET') {
        const newest = [...jobs.values()].reverse().sort((a, b) => b.createdMs - a.createdMs).slice(0, 20);
        return json({ items: newest.map(entry => pick(videoView(entry, now), ['id', 'status', 'stage', 'progress', 'created_at', 'media', 'counts', 'project_name'])) });
      }
      if (path === '/api/video-reviews' && method === 'POST') return json(await submitReview(request), 202);
      const review = path.match(/^\/api\/video-reviews\/([^/]+)(?:\/(video|frames\/([^/]+)))?$/);
      if (review && (method === 'GET' || (method === 'HEAD' && review[2] === 'video'))) {
        const entry = jobs.get(review[1]);
        if (!entry) throw fail('RESOURCE_NOT_FOUND', 404, '동영상 검수 기록을 찾을 수 없습니다.');
        if (!review[2]) return json(videoView(entry, now));
        if (review[2] === 'video') return sendClip(request, response);
        const frame = /^t\d{6}$/.test(review[3]) ? entry.analysis.meta.get(review[3]) : null;
        if (!frame || ['queued', 'extracting'].includes(stageAt(elapsedOf(entry, now)).stage)) throw fail('RESOURCE_NOT_FOUND', 404, '프레임을 찾을 수 없습니다.');
        response.writeHead(200, { 'Content-Type': 'image/svg+xml; charset=utf-8' });
        response.end(frameSvg(frame.time_ms)); return;
      }
      if (path === '/api/video-logos/status' && method === 'GET') return json({ ready: true, detail: null });
      if (path === '/api/video-logos' && method === 'GET') {
        const reviewId = url.searchParams.get('review_id');
        const items = [...logoJobs.values()].reverse().filter(entry => !reviewId || entry.review_id === reviewId).sort((a, b) => b.createdMs - a.createdMs).slice(0, 20);
        return json({ items: items.map(entry => pick(logoView(entry, now), LOGO_LIST_KEYS)) });
      }
      if (path === '/api/video-logos' && method === 'POST') return json(await submitLogo(request), 202);
      const logo = path.match(/^\/api\/video-logos\/([^/]+)$/);
      if (logo && method === 'GET') {
        const entry = logoJobs.get(logo[1]);
        if (!entry) throw fail('RESOURCE_NOT_FOUND', 404, '영상 로고 작업을 찾을 수 없습니다.');
        return json(logoView(entry, now));
      }
      throw fail('FIXTURE_ROUTE_NOT_FOUND', 404);
    },
  };
}

export async function createFixtureHandler({ profile = 'v1', video = null } = {}) {
  if (!['legacy', 'v1'].includes(profile)) throw new Error('Profile must be legacy or v1');
  if (video && profile !== 'legacy') throw new Error('--video is only available with the legacy profile');
  const f = fixtures();
  const png = await readFile(new URL('../v1/poster.png', import.meta.url));
  const videoFixture = profile === 'legacy' ? createVideoFixture(video ? await loadClip(video) : null) : null;
  const cookieName = `cc_visual_${profile}`;
  const sessions = new Map(), replay = new Map();
  const projects = new Map([[f.project.id, f.project]]), images = new Map([[f.image.id, f.image]]);
  const sets = new Map([[f.set.id, f.set]]), drafts = new Map([[f.draft.id, f.draft]]);
  const versions = new Map([[f.criteria.id, f.criteria]]), runs = new Map([[f.run.id, { dto: f.run, started: null }]]);
  const evidence = new Map([[ids.result, [f.evidence]]]);
  const tokens = new Map([[ids.run, [f.token]]]), legacyRuns = new Map();
  let sequence = 1000;
  const nextId = () => uid(sequence++);
  const scoped = (map, id, pid) => { const value = get(map, id); if (value.project_id !== pid) throw fail('RESOURCE_NOT_FOUND', 404); return value; };
  const projectWrite = pid => { const value = get(projects, pid); if (value.business_state === 'archived') throw fail('PROJECT_ARCHIVED', 409); return value; };
  function currentVersion(value) {
    const draft = drafts.get(value.source_draft_id);
    return { ...clone(value), draft_is_current: !!draft && draft.revision === value.source_draft_revision };
  }
  function runView(entry) {
    const age = entry.started === null ? Infinity : Date.now() - entry.started;
    if (age >= 2200) return clone(entry.dto);
    return { ...clone(entry.dto), status: 'running', stage: age < 1000 ? 'ocr' : 'review',
      progress: age < 1000 ? 25 : 65, results: [], results_complete: false, overall_verdict: null, ocr: null };
  }
  function makeRun(pid, imageId, versionId, retryOf = null) {
    const p = projectWrite(pid), image = scoped(images, imageId, pid), version = scoped(versions, versionId, pid);
    if (!currentVersion(version).draft_is_current) throw fail('CRITERIA_STALE', 409);
    const runId = nextId(), ocrTokens = [];
    const results = version.criteria.map((criterion, index) => {
      const resultId = nextId(), tokenId = nextId(), evidenceId = nextId();
      const isVisual = criterion.type === 'visual', status = isVisual ? 'review' : 'pass';
      const y = 24 + (index % 8) * 30;
      const token = { ...clone(f.token), id: tokenId, external_token_id: String(index), order: index,
        text: '합성 관측: ' + criterion.text, frame_id: image.frame_id, bbox_xyxy: [20, y, 180, y + 22] };
      ocrTokens.push(token);
      evidence.set(resultId, [{ ...clone(f.evidence), id: evidenceId, frame_id: image.frame_id,
        token_ids: [tokenId], text: token.text, bbox_xyxy: token.bbox_xyxy, limitations: [NOTICE] }]);
      return { ...clone(f.run.results[0]), result_id: resultId, criteria_item_id: criterion.criteria_item_id,
        criterion_id: criterion.id, criterion: criterion.text, type: criterion.type, verdict: status, status,
        observed_text: token.text, observed_value: null, reason: isVisual ? '시각 조건은 담당자 확인 대상으로 표시하는 합성 사례입니다.' : NOTICE,
        limitations: [NOTICE], check_method: 'visual-http-fixture', evidence: [], evidence_count: 1, evidence_complete: false };
    });
    const dto = { ...clone(f.run), id: runId, project_id: pid, image_id: imageId, criteria_version_id: versionId,
      retry_of: retryOf, project_name: p.name, criteria_version: version.version_no, criteria: clone(version.criteria), image: clone(image),
      results, overall_verdict: results.some(result => result.verdict === 'review') ? 'review' : 'pass',
      created_at: timestamp(), runtime: { pipeline: 'visual-http-fixture', notice: NOTICE },
      ocr: { ...clone(f.run.ocr), id: nextId(), frame_id: image.frame_id, record_count: ocrTokens.length } };
    runs.set(runId, { dto, started: Date.now() }); tokens.set(runId, ocrTokens);
    return { id: runId, project_id: pid, image_id: imageId, criteria_version_id: versionId, retry_of: retryOf, status: 'queued' };
  }

  return async function handler(request, response) {
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.setHeader('X-Creative-Checker-Fixture', profile);
    const json = (body, status = 200) => {
      response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
      response.end(JSON.stringify(profile === 'v1' ? wrap(body) : body));
    };
    const imageResponse = () => { response.writeHead(200, { 'Content-Type': 'image/png' }); response.end(png); };
    try {
      const url = new URL(request.url, `http://${request.headers.host || '127.0.0.1'}`);
      const peer = request.socket?.remoteAddress;
      if ((peer && !['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(peer)) || !isLoopback(url.hostname)) throw fail('LOCAL_ONLY', 403);
      if (request.headers.origin && !isLoopback(new URL(request.headers.origin).hostname)) throw fail('LOCAL_ONLY', 403);
      const path = url.pathname, method = request.method;
      if (path === '/__fixture' && method === 'GET') return json({ profile, notice: NOTICE, persistent: false, model_calls: false });
      if (videoFixture && path === '/__fixture/video-script' && method === 'GET') return json(videoFixture.script());
      const cookie = String(request.headers.cookie || '').split(';').map(part => part.trim()).find(part => part.startsWith(cookieName + '='))?.slice(cookieName.length + 1);
      const user = sessions.get(cookie) || null;
      const auth = '/api/v1/auth';
      if (path === auth + '/me' && method === 'GET') return json({ authenticated: !!user, user });
      if (path === auth + '/csrf' && method === 'GET') return json({ csrf_token: CSRF, developer_login_available: true });
      if (path === auth + '/login' && method === 'POST') {
        if (request.headers['x-csrftoken'] !== CSRF) throw fail('CSRF_FAILED', 403);
        const body = bodyJSON(await readBody(request));
        const developer = body.username === 'admin' && body.password === 'admin';
        const regular = profile === 'v1' ? body.email === 'reviewer@example.test' && body.password?.trim() === 'fixture-password'
          : body.username?.trim() === 'tester' && body.password === 'test-password';
        if (!developer && !regular) throw fail(profile === 'v1' ? 'INVALID_CREDENTIALS' : 'invalid_credentials', 401);
        const currentUser = profile === 'v1' ? { ...clone(f.user), ...(developer ? { account_kind: 'developer', email: null } : {}) }
          : { id: 'visual-test-user', username: developer ? 'admin' : 'tester', display_name: developer ? '관리자 (개발용)' : '검증 사용자' };
        if (cookie) sessions.delete(cookie);
        const sessionId = randomUUID(); sessions.set(sessionId, currentUser);
        response.setHeader('Set-Cookie', `${cookieName}=${sessionId}; Path=/; HttpOnly; SameSite=Strict`);
        return json({ user: currentUser });
      }
      if (path === auth + '/logout' && method === 'POST') {
        if (request.headers['x-csrftoken'] !== CSRF) throw fail('CSRF_FAILED', 403);
        sessions.delete(cookie); response.setHeader('Set-Cookie', `${cookieName}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict`);
        response.writeHead(204); response.end(); return;
      }
      if (method === 'GET' && path === (profile === 'v1' ? '/api/v1/health' : '/api/health')) {
        return json(profile === 'v1'
          ? { status: 'degraded', checks: { db: { state: 'ready', simulated: true, backend: 'in_memory_fixture' }, model: { state: 'not_connected' } }, limits: { sample_available: false }, notice: NOTICE }
          : { runtime: { ready: true, engine: 'fixture-only', execution_target: 'test', device: 'fixture' }, limits: {}, notice: NOTICE });
      }
      if (!path.startsWith('/api/')) throw fail('FIXTURE_ROUTE_NOT_FOUND', 404);
      const knownPath = profile === 'legacy'
        ? /^\/api\/(?:sample(?:\/image)?|requirements\/extract\/stream|reviews(?:\/[^/]+(?:\/image)?)?|video-reviews(?:\/[^/]+(?:\/(?:video|frames\/[^/]+))?)?|video-logos(?:\/[^/]+)?)$/.test(path)
        : /^\/api\/v1\/(?:projects(?:\/[^/]+(?:\/(?:images|criteria-sets|criteria-drafts|criteria-versions|reviews)(?:\/[^/]+)?)?)?|images\/[^/]+(?:\/content)?|reviews(?:\/[^/]+(?:\/(?:retry|export|ocr-records|results\/[^/]+\/evidence))?)?)$/.test(path);
      if (!knownPath) throw fail('FIXTURE_ROUTE_NOT_FOUND', 404);
      if (!user) throw fail('AUTHENTICATION_REQUIRED', 401);

      if (profile === 'legacy') {
        if (path.startsWith('/api/video-')) return await videoFixture.handle(request, response, { path, method, url, json });
        if (path === '/api/sample' && method === 'GET') return json({ project_name: '시각 QA 샘플 프로젝트', requirements: '행사명: 가을 문화 축제\n기간: 2026.10.30 ~ 11.01\n로고와 배치를 확인해 주세요.' });
        if (path === '/api/sample/image' && method === 'GET') return imageResponse();
        if (path === '/api/requirements/extract/stream' && method === 'POST') {
          const form = await formData(await readBody(request), request.headers['content-type']);
          if (!form.get('document')) throw fail('INVALID_MULTIPART', 422);
          const conditions = [{ text: '행사명: 가을 문화 축제', type: 'information', page: 1, source: '행사명은 가을 문화 축제로 표기한다. (시각 QA 예시)',
            label: '행사명', requirement: '가을 문화 축제', level: '필수', inspection: '원문의 행사명과 대조한다.', approval_status: 'pending', source_kind: 'fixture' }];
          response.writeHead(200, { 'Content-Type': 'application/x-ndjson; charset=utf-8' });
          const events = [
            { type: 'progress', stage: 'reading', percent: 20, message: '예시 문서 읽기 단계' },
            { type: 'progress', stage: 'extracting', percent: 70, message: '예시 조건과 출처 정리 중' },
            { type: 'result', result: { conditions, requirements: conditions[0].text, page_count: 1, scanned_pages: 0, model: 'fixture-only', warnings: [NOTICE] } },
          ];
          let index = 0;
          const emit = () => {
            if (response.destroyed || response.writableEnded) return;
            response.write(JSON.stringify(events[index++]) + '\n');
            if (index === events.length) response.end();
            else { const timer = setTimeout(emit, 450); response.once('close', () => clearTimeout(timer)); }
          };
          emit(); return;
        }
        if (path === '/api/reviews' && method === 'POST') {
          const form = await formData(await readBody(request), request.headers['content-type']);
          let criteria;
          try { criteria = JSON.parse(String(form.get('criteria'))); } catch { throw fail('INVALID_REQUEST', 422); }
          if (!form.get('image') || !Array.isArray(criteria) || !criteria.length || criteria.length > 100) throw fail('INVALID_REQUEST', 422);
          const runId = randomUUID();
          const dto = { id: runId, status: 'completed', stage: 'completed', progress: 100,
            project_name: String(form.get('project_name') || '시각 QA'), criteria_version: Number(form.get('criteria_version')) || 1,
            image: { url: `/api/reviews/${runId}/image`, width: f.image.width, height: f.image.height }, criteria,
            results: criteria.map((item, index) => ({ criterion_id: item.id, criterion: item.text, type: item.type,
              status: item.type === 'visual' ? 'review' : index === 1 ? 'fail' : 'pass',
              reason: NOTICE, observed_text: '합성 관측: ' + item.text,
              evidence: [{ text: '합성 관측: ' + item.text, bbox_xyxy: [20, 24 + index % 8 * 30, 180, 46 + index % 8 * 30], confidence: null }] })),
            model_runtime: { status: 'not_configured' }, runtime: { pipeline: 'visual-http-fixture', notice: NOTICE } };
          legacyRuns.set(runId, { dto, started: Date.now() }); return json({ id: runId, status: 'queued' }, 202);
        }
        const match = path.match(/^\/api\/reviews\/([^/]+)(\/image)?$/);
        if (match && method === 'GET') {
          const entry = get(legacyRuns, match[1]); if (match[2]) return imageResponse();
          if (Date.now() - entry.started < 1800) return json({ id: entry.dto.id, status: 'running', stage: 'ocr', progress: 40 });
          return json(entry.dto);
        }
        throw fail('FIXTURE_ROUTE_NOT_FOUND', 404);
      }

      if (!path.startsWith('/api/v1/')) throw fail('FIXTURE_PROFILE_MISMATCH', 404);
      const mutating = !['GET', 'HEAD'].includes(method);
      if (mutating && request.headers['x-csrftoken'] !== CSRF) throw fail('CSRF_FAILED', 403);
      const buffer = mutating ? await readBody(request) : Buffer.alloc(0);
      const body = buffer.length && !request.headers['content-type']?.startsWith('multipart/') ? bodyJSON(buffer) : null;
      const key = request.headers['idempotency-key'];
      const replayKey = key ? `${cookie}:${method}:${path}:${key}` : null;
      const bodySignature = buffer.toString('base64');
      if (replayKey && replay.has(replayKey)) {
        const previous = replay.get(replayKey); if (previous.signature !== bodySignature) throw fail('IDEMPOTENCY_KEY_REUSED', 409);
        return json(previous.body, previous.status);
      }
      const reply = (value, status = 200) => {
        if (replayKey && status < 300) replay.set(replayKey, { signature: bodySignature, body: clone(value), status });
        return json(value, status);
      };
      if (path === '/api/v1/projects') {
        if (method === 'GET') {
          const archived = url.searchParams.get('archived') || 'exclude';
          return reply(pageDto([...projects.values()].filter(p => archived === 'include' || (p.business_state === 'archived') === (archived === 'only'))));
        }
        if (method === 'POST') {
          if (!nonempty(body?.name)) throw fail('INVALID_REQUEST', 422);
          const value = { ...clone(f.project), id: nextId(), name: body.name.trim() }; projects.set(value.id, value);
          const set = { id: nextId(), project_id: value.id, name: '기본 검수 기준' }; sets.set(set.id, set);
          return reply(value, 201);
        }
      }
      const imageMatch = path.match(/^\/api\/v1\/images\/([^/]+)(\/content)?$/);
      if (imageMatch && method === 'GET') { const image = get(images, imageMatch[1]); return imageMatch[2] ? imageResponse() : reply(image); }
      const projectMatch = path.match(/^\/api\/v1\/projects\/([^/]+)(?:\/([^/]+)(?:\/([^/]+))?)?$/);
      if (projectMatch) {
        const [, pid, resource, identifier] = projectMatch; const project = get(projects, pid);
        if (!resource) {
          if (method === 'GET') return reply(project);
          if (method === 'PATCH') {
            if (body.name !== undefined) { if (!nonempty(body.name)) throw fail('INVALID_REQUEST', 422); project.name = body.name.trim(); }
            if (typeof body.archived === 'boolean') project.business_state = body.archived ? 'archived' : 'active';
            return reply(project);
          }
        }
        if (mutating) projectWrite(pid);
        if (resource === 'images' && !identifier) {
          if (method === 'GET') return reply(pageDto([...images.values()].filter(value => value.project_id === pid)));
          if (method === 'POST') {
            const form = await formData(buffer, request.headers['content-type']);
            if (!form.get('image')) throw fail('INVALID_MULTIPART', 422);
            const id = nextId(); const value = { ...clone(f.image), id, asset_id: nextId(), frame_id: nextId(), project_id: pid, url: `/api/v1/images/${id}/content` };
            images.set(id, value); return reply(value, 201);
          }
        }
        if (resource === 'criteria-sets' && !identifier) {
          if (method === 'GET') return reply(pageDto([...sets.values()].filter(value => value.project_id === pid)));
          if (method === 'POST') { if (!nonempty(body.name)) throw fail('INVALID_REQUEST', 422); const value = { id: nextId(), project_id: pid, name: body.name }; sets.set(value.id, value); return reply(value, 201); }
        }
        if (resource === 'criteria-drafts') {
          if (!identifier && method === 'GET') return reply(pageDto([...drafts.values()].filter(value => value.project_id === pid)));
          if (!identifier && method === 'POST') {
            scoped(images, body.image_id, pid); scoped(sets, body.criteria_set_id, pid);
            const value = { ...selectedItems(body), id: nextId(), project_id: pid, criteria_set_id: body.criteria_set_id, image_id: body.image_id, revision: '1', state: 'editing' };
            drafts.set(value.id, value); return reply(value, 201);
          }
          if (identifier) {
            const value = scoped(drafts, identifier, pid);
            if (method === 'GET') return reply(value);
            if (method === 'PUT') {
              if (body.expected_revision !== value.revision) throw fail('DRAFT_REVISION_CONFLICT', 409);
              Object.assign(value, selectedItems(body), { revision: String(BigInt(value.revision) + 1n) }); return reply(value);
            }
          }
        }
        if (resource === 'criteria-versions') {
          if (identifier && method === 'GET') return reply(currentVersion(scoped(versions, identifier, pid)));
          if (!identifier && method === 'GET') return reply(pageDto([...versions.values()].filter(value => value.project_id === pid).map(currentVersion)));
          if (!identifier && method === 'POST') {
            const draft = scoped(drafts, body.draft_id, pid);
            if (body.expected_revision !== draft.revision) throw fail('DRAFT_REVISION_CONFLICT', 409);
            const selected = draft.items.filter(item => item.selected);
            if (!selected.length || selected.some(item => !nonempty(item.text))) throw fail('INVALID_REQUEST', 422);
            const previous = [...versions.values()].filter(value => value.criteria_set_id === draft.criteria_set_id);
            const value = { ...clone(f.criteria), id: nextId(), project_id: pid, criteria_set_id: draft.criteria_set_id, image_id: draft.image_id,
              version_no: Math.max(0, ...previous.map(value => value.version_no)) + 1, source_draft_id: draft.id, source_draft_revision: draft.revision,
              criteria: selected.map((item, index) => ({ criteria_item_id: nextId(), id: item.id, position: index + 1, text: item.text, type: item.type, source: item.source, is_required: null })) };
            versions.set(value.id, value); return reply(value, 201);
          }
        }
        if (resource === 'reviews' && !identifier && method === 'GET') {
          let list = [...runs.values()].map(runView).filter(value => value.project_id === pid);
          if (url.searchParams.get('status')) list = list.filter(value => value.status === url.searchParams.get('status'));
          if (url.searchParams.get('image_id')) list = list.filter(value => value.image_id === url.searchParams.get('image_id'));
          return reply(pageDto(list.reverse()));
        }
      }
      if (path === '/api/v1/reviews' && method === 'POST') return reply(makeRun(body.project_id, body.image_id, body.criteria_version_id), 202);
      const runMatch = path.match(/^\/api\/v1\/reviews\/([^/]+)(?:\/(retry|export|ocr-records|results)(?:\/([^/]+)\/evidence)?)?$/);
      if (runMatch) {
        const [, runId, action, resultId] = runMatch; const entry = get(runs, runId), run = runView(entry);
        if (!action && method === 'GET') return reply(run);
        if (action === 'retry' && method === 'POST') return reply(makeRun(run.project_id, run.image_id, run.criteria_version_id, runId), 202);
        if (action === 'ocr-records' && method === 'GET') {
          const rows = run.status === 'completed' ? tokens.get(runId) || [] : [];
          return reply(pageDto(rows, { availability: run.status === 'completed' ? 'available' : 'pending', record_count: rows.length }));
        }
        if (action === 'results' && resultId && method === 'GET') {
          if (!run.results.some(result => result.result_id === resultId)) throw fail('RESOURCE_NOT_FOUND', 404);
          const rows = evidence.get(resultId) || []; return reply(pageDto(rows, { availability: 'available', evidence_count: rows.length }));
        }
        if (action === 'export' && method === 'GET') {
          return reply({ ...run, contract_version: 'cc-review-export-v1', exported_at: timestamp(), fixture_notice: NOTICE,
            results: run.results.map(result => ({ ...result, evidence: evidence.get(result.result_id) || [], evidence_complete: true })), ocr_records: tokens.get(runId) || [] });
        }
      }
      throw fail('FIXTURE_ROUTE_NOT_FOUND', 404);
    } catch (error) {
      if (response.headersSent || response.destroyed) { response.end(); return; }
      json({ error: { code: error.code || 'FIXTURE_INTERNAL_ERROR', message: error.detail || (error.code === 'invalid_credentials' ? '아이디 또는 비밀번호를 확인해 주세요.' : NOTICE), fields: {} } }, error.status || 500);
    }
  };
}

export function parseOptions(argv) {
  let profile = 'v1', port = 8991, video = null;
  for (let index = 0; index < argv.length; index++) {
    if (argv[index] === '--profile') profile = argv[++index];
    else if (argv[index] === '--port') port = Number(argv[++index]);
    else if (argv[index] === '--video') video = String(argv[++index]);
    else throw new Error('Usage: node fixture-server.mjs [--profile v1|legacy] [--port 8991] [--video clip.webm|clip.mp4]');
  }
  if (!['legacy', 'v1'].includes(profile) || !Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid profile or local port');
  if (video !== null && (profile !== 'legacy' || !/\.(?:webm|mp4)$/i.test(video))) throw new Error('--video needs --profile legacy and a .webm or .mp4 file');
  return { profile, port, video };
}

async function main() {
  const options = parseOptions(process.argv.slice(2));
  const handler = await createFixtureHandler(options);
  const server = createServer(handler);
  server.requestTimeout = 15000; server.headersTimeout = 10000;
  server.on('error', error => { console.error(`Fixture server could not start (${error.code || 'error'}).`); process.exitCode = 1; });
  server.listen(options.port, '127.0.0.1', () => console.log(`VISUAL FIXTURE ONLY: ${options.profile} API at http://127.0.0.1:${options.port} — no database, model, or persistence.`
    + (options.video ? ' Video playback clip loaded in memory.' : '')));
  for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { server.close(); server.closeAllConnections(); });
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(() => { console.error('Invalid fixture options or unavailable bundled fixture. See README.txt.'); process.exitCode = 1; });
}
