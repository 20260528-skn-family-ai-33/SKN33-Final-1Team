import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { fetchJson } from '../../api/client.js';
import { useAuth } from '../../auth/AuthProvider.jsx';
import { Brand } from '../../components/Brand.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { evidenceBoxStyle, parseRequirements } from '../../workflow.js';
import {
  criteriaPayload, diffChars, formatTime, isTerminal, mismatchLabels, reviewerLabels, roleLabels, ruleLabels,
  severityLabels, stageLabels, statusLabels,
} from './videoFormat.js';

// 서버 준비 상태와 최근 동영상 검수 목록을 함께 불러온다.
function useVideoStatus() {
  const [status, setStatus] = useState(null);
  const [jobs, setJobs] = useState([]);
  const refresh = useCallback(async () => {
    try {
      const [ready, list] = await Promise.all([fetchJson('/api/video-reviews/status'), fetchJson('/api/video-reviews')]);
      setStatus(ready); setJobs(list.items || []);
    } catch (error) { setStatus({ ready: false, detail: error.message }); }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  return { status, jobs, refresh };
}

// 끝날 때까지 작업 상태를 주기적으로 다시 읽는다.
function useVideoJob(id, onFinish) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!id) { setJob(null); return undefined; }
    const controller = new AbortController();
    let timer;
    async function poll() {
      try {
        const value = await fetchJson(`/api/video-reviews/${encodeURIComponent(id)}`, { signal: controller.signal });
        setJob(value); setError('');
        if (isTerminal(value.status)) onFinish(); else timer = setTimeout(poll, 1500);
      } catch (failure) {
        if (failure.name === 'AbortError') return;
        setError(failure.message);
        // 일시적인 연결 오류는 다시 확인하고, 없는 기록(404)만 멈춘다.
        if (failure.status !== 404) timer = setTimeout(poll, 3000);
      }
    }
    setJob(null); poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [id, onFinish]);
  return { job, error };
}

function Badge({ status, children }) { return <span className={`vbadge ${status}`}>{children || statusLabels[status] || status}</span>; }

// 영상·확정 기준·광고 입력을 한 번에 받아 접수한다(기준과 광고 입력은 이 시점에 고정).
function UploadCard({ status, onSubmitted }) {
  const [file, setFile] = useState(null);
  const [projectName, setProjectName] = useState('숏폼 검수');
  const [requirements, setRequirements] = useState('');
  const [ad, setAd] = useState({ kind: 'endorsement', celebrity_exposure: false, title: '', caption: '', platform: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const input = useRef(null);
  const limits = status?.limits;
  const criteria = useMemo(() => parseRequirements(requirements), [requirements]);

  function choose(event) {
    const selected = event.target.files[0] || null;
    setError('');
    if (selected && limits && selected.size > limits.upload_bytes) {
      setFile(null); event.target.value = '';
      setError(`동영상은 ${Math.floor(limits.upload_bytes / 1048576)}MB 이하여야 합니다.`);
      return;
    }
    setFile(selected);
  }

  async function submit() {
    if (!file || busy) return;
    setBusy(true); setError('');
    try {
      const form = new FormData();
      form.append('video', file);
      form.append('criteria', JSON.stringify(criteriaPayload(criteria)));
      form.append('criteria_version', '1');
      form.append('project_name', projectName.trim() || '숏폼 검수');
      form.append('disclosure', JSON.stringify(ad));
      const created = await fetchJson('/api/video-reviews', { method: 'POST', body: form });
      setFile(null); if (input.current) input.current.value = '';
      onSubmitted(created.id);
    } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  }

  const field = (key, value) => { setError(''); setAd(previous => ({ ...previous, [key]: value })); };
  return <div className="form-card video-upload">
    <div className="card-heading"><h2>동영상과 기준 입력</h2><span className="pill">기준·광고 입력은 접수 때 고정</span></div>
    <div className="video-upload-body">
      <label className="upload-zone" htmlFor="video-file">
        <Icon name="upload" />
        <strong>{file ? file.name : 'MP4 · MOV · WEBM · AVI 파일 선택'}</strong>
        <span className="help">{file ? `${(file.size / 1048576).toFixed(1)}MB` : limits ? `최대 ${Math.floor(limits.upload_bytes / 1048576)}MB · ${limits.duration_seconds}초 이하 · 짧은 변 360px 이상` : '서버 한도를 확인하고 있어요.'}</span>
      </label>
      <input id="video-file" ref={input} className="visually-hidden" type="file" accept="video/mp4,video/quicktime,video/webm,video/x-matroska,video/x-msvideo,video/avi,.avi" onChange={choose} disabled={busy} />
      <div className="video-fields">
        <label>프로젝트 이름<input value={projectName} maxLength={120} onChange={event => { setError(''); setProjectName(event.target.value); }} /></label>
        <label className="wide">검수 기준 (한 줄에 하나)
          <textarea rows={4} value={requirements} placeholder={'행사명: 김천김밥축제\n기간: 2026.10.23 ~ 10.25\n"사전 예약 필수"'} onChange={event => { setError(''); setRequirements(event.target.value); }} />
          <span className="help">{criteria.length ? `기준 ${criteria.length}개 · 정보 ${criteria.filter(c => c.type === 'information').length} / 시각 ${criteria.filter(c => c.type === 'visual').length}` : '비워 두면 기준 판정 없이 자막 트랙과 광고 표시만 봅니다.'}</span>
        </label>
        <fieldset className="wide"><legend>광고·협찬 표시 입력</legend>
          <label>게시물 제목<input value={ad.title} maxLength={500} onChange={event => field('title', event.target.value)} /></label>
          <label>태그<input value={ad.caption} maxLength={5000} onChange={event => field('caption', event.target.value)} /></label>
          <label className="check"><input type="checkbox" checked={ad.kind === 'self'} onChange={event => field('kind', event.target.checked ? 'self' : 'endorsement')} />자사·자체 광고 (표시 의무 없어 검수 제외)</label>
          <label className="check"><input type="checkbox" checked={ad.celebrity_exposure} onChange={event => field('celebrity_exposure', event.target.checked)} />PPL/협찬</label>
        </fieldset>
      </div>
      {status?.sampling && <p className="help">{status.sampling}. CPU에서는 프레임 1장에 수 초~20초 걸립니다.</p>}
      <ErrorMessage message={error} />
      {status && !status.ready && <ErrorMessage message={status.detail || '동영상 검수 서버가 준비되지 않았습니다.'} />}
      <button className="btn primary" onClick={submit} disabled={!file || busy || !status?.ready}>{busy ? '올리는 중…' : '동영상 검수 시작'}</button>
    </div>
  </div>;
}

// 진행 단계와 단계별 세부 정보를 표시한다.
function Progress({ job }) {
  return <div className="form-card run-card">
    <div className={`run-symbol ${job.status === 'failed' ? 'stopped' : ''}`}><Icon name={job.status === 'failed' ? 'warning' : 'search'} /></div>
    <h2>{stageLabels[job.stage] || '처리하고 있어요.'}</h2>
    <p aria-live="polite">{job.stage_detail || `영상 ${formatTime(job.media?.duration_ms)} · ${job.media ? `${job.media.width}×${job.media.height}` : ''}`}</p>
    <div className="progress-track" role="progressbar" aria-label="동영상 검수 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={job.progress || 0}><div style={{ width: `${Math.min(100, Math.max(0, job.progress || 0))}%` }} /></div>
    <div className="progress-description"><span>실제 처리 상태</span><span>{job.progress || 0}%</span></div>
    <ErrorMessage message={job.error} />
  </div>;
}

// 구간 타임라인: 검사 시점은 점, 미검사 구간은 빗금, 근거가 있는 구간은 강조한다.
function Timeline({ job, selected, onSelect, highlighted }) {
  const duration = job.coverage.duration_ms || 1;
  const pct = ms => `${Math.min(100, ms / duration * 100)}%`;
  return <div className="timeline" aria-label="구간 타임라인">
    <div className="timeline-track">
      {job.segments.map(segment => <button key={segment.id} type="button" title={`${formatTime(segment.start_ms)}–${formatTime(segment.end_ms)}`}
        className={`timeline-segment ${segment.track_ids.length ? 'has-text' : ''} ${highlighted.has(segment.id) ? 'evidence' : ''} ${selected === segment.id ? 'current' : ''}`}
        style={{ left: pct(segment.start_ms), width: pct(segment.end_ms - segment.start_ms) }} onClick={() => onSelect(segment)}
        aria-label={`${formatTime(segment.start_ms)}부터 ${formatTime(segment.end_ms)}까지 구간`} />)}
    </div>
    <div className="timeline-points" aria-hidden="true">{job.coverage.inspected_ms.map(t => <span key={t} style={{ left: pct(t) }} />)}</div>
    <div className="timeline-scale"><span>0:00</span><span>점 = 검사한 시점 · 빗금 = 검사하지 않은 구간</span><span>{formatTime(duration)}</span></div>
  </div>;
}

// 선택한 프레임 위에 남긴 줄·제외한 줄·선택 근거의 위치를 표시한다.
function FrameView({ job, frameId, focus }) {
  const frame = job.frames.find(item => item.frame_id === frameId);
  if (!frame) return <p className="help video-empty">구간을 고르면 대표 프레임과 글자 위치를 보여 줍니다.</p>;
  return <div className="video-frame-body">
    <div className="result-canvas video-canvas">
      <img src={`/api/video-reviews/${job.id}/frames/${frame.frame_id}`} alt={`${formatTime(frame.time_ms)} 프레임과 읽은 글자 영역`} />
      {frame.lines.map(line => { const style = evidenceBoxStyle(line.bbox_xyxy, frame.width, frame.height); return style ? <div key={line.line_id} className={`evidence-box ${line.kept ? '' : 'excluded'}`} style={style} title={line.text} /> : null; })}
      {focus && (() => { const style = evidenceBoxStyle(focus, frame.width, frame.height); return style ? <div className="evidence-box active" style={style} /> : null; })()}
    </div>
    <p className="help">{formatTime(frame.time_ms)} · {frame.sampling === 'base' ? '1초 표본' : '시작·끝 0.2초 표본'}{frame.ocr_status === 'failed' ? ' · OCR 실패' : ''}</p>
  </div>;
}

// 기준 문구와 화면 문구를 나란히 두고 다른 글자만 강조한다.
function MismatchRow({ item }) {
  return <div className="mismatch-row"><Badge status="mismatch">불일치</Badge><span>{mismatchLabels[item.kind] || item.kind}</span>
    {item.expected ? <div className="mismatch-pair"><span>기준 <b>{item.expected}</b></span><span>화면 <b>{diffChars(item.expected, item.observed).map(([ch, different], i) => different ? <mark key={i}>{ch}</mark> : <React.Fragment key={i}>{ch}</React.Fragment>)}</b></span></div>
      : <div className="mismatch-pair"><span>대표 <b>{item.observed}</b></span><span>다르게 읽힘 <b>{item.variants.join(' / ')}</b></span></div>}
  </div>;
}

function CriterionCard({ result, onEvidence }) {
  return <li className="criterion-result">
    <div className="criterion-head"><Badge status={result.status} />{result.mismatch?.length > 0 && <Badge status="mismatch">불일치</Badge>}
      <span className="axis">{severityLabels[result.severity]} · {reviewerLabels[result.reviewer]}</span></div>
    <strong>{result.criterion}</strong>
    <p>{result.reason}</p>
    {result.mismatch?.map((item, i) => <MismatchRow key={i} item={item} />)}
    {result.mismatch?.length > 0 && <p className="help">OCR이 잘못 읽었을 수도 있으니 원본을 확인해 주세요.</p>}
    {result.evidence_segments?.length > 0 && <div className="evidence-links">{result.evidence_segments.map((e, i) =>
      <button key={i} className="text-btn" onClick={() => onEvidence(e)}>{formatTime(e.start_ms)}~{formatTime(e.end_ms)} 위치 보기</button>)}</div>}
  </li>;
}

function DisclosureCard({ disclosure }) {
  if (!disclosure) return null;
  return <div className="form-card">
    <div className="card-heading"><h2>광고·협찬 표시</h2><Badge status={disclosure.status} /></div>
    <div className="disclosure-body">
      <p>{disclosure.reason}</p>
      {disclosure.applicable && <p className="help">표시 문구·시간(표본 기준): <b>{statusLabels[disclosure.text_time_status]}</b> — 표본 문구 확인과 표시 규정 충족은 다른 상태입니다.</p>}
      {Object.keys(disclosure.rules || {}).length > 0 && <ul className="rule-list">{Object.entries(disclosure.rules).map(([key, rule]) => <li key={key}>
        <span className="rule-key">{key}</span><span>{ruleLabels[key]}</span><Badge status={rule.status} /><span className="help">{rule.note}</span></li>)}</ul>}
      {disclosure.notes?.map(note => <p className="help" key={note}>{note}</p>)}
    </div>
  </div>;
}

// 문서 7단계 결과 화면: 플레이어 + 구간 타임라인 + 근거 프레임 bbox + 기준별 결과 + 광고 표시 카드.
function Results({ job }) {
  const player = useRef(null);
  const firstText = job.segments.find(segment => segment.track_ids.length) || job.segments[0];
  const [segmentId, setSegmentId] = useState(firstText?.id);
  const [frameId, setFrameId] = useState(firstText?.rep_frame_id);
  const [focus, setFocus] = useState(null);
  const results = job.review?.results || [];
  const tracks = Object.fromEntries((job.tracks || []).map(track => [track.id, track]));
  const highlighted = new Set(results.flatMap(r => (r.evidence_segments || []).map(e => e.segment_id)));
  const counts = job.counts || {};

  function seek(ms) { const video = player.current; if (video) { video.currentTime = ms / 1000; video.pause(); } }
  function selectSegment(segment) { setSegmentId(segment.id); setFrameId(segment.rep_frame_id); setFocus(null); seek(segment.start_ms); }
  function showEvidence(evidence) { setSegmentId(evidence.segment_id); setFrameId(evidence.frame_id); setFocus(evidence.bbox_xyxy); seek(evidence.time_ms); }

  return <>
    <div className="result-alert"><Icon name="info" /><div>
      <strong>{job.coverage.note}. '충족'은 검사한 시점에서 충족이라는 뜻입니다.</strong>
      <p>{job.classifier === 'logreg' ? '학습한 자막 줄 분류 모델' : '자막 줄 분류 모델이 아직 없어 OCR 신뢰도 0.5 하한 규칙'}으로 자막 후보를 골랐습니다. 제외한 줄도 사유와 함께 남겨 두었습니다.</p>
      {job.warnings?.map(warning => <p key={warning}>⚠ {warning}</p>)}
    </div></div>
    <div className="result-metrics video-metrics">
      {[['충족', counts.results?.pass, 'pass'], ['위반', counts.results?.fail, 'fail'], ['확인 필요', counts.results?.review, 'review'], ['불일치', counts.mismatch, 'review']].map(([label, value, tone]) =>
        <div className={`metric ${tone}`} key={label}><span>{label}</span><strong>{value ?? 0}</strong></div>)}
    </div>
    <div className="video-result-grid">
      <div className="form-card">
        <div className="card-heading"><h2>영상과 구간</h2><span className="pill">트랙 {counts.tracks} · 구간 {counts.segments} · 프레임 {counts.frames}</span></div>
        <div className="video-frame-body">
          <video ref={player} className="video-player" controls preload="metadata" src={`/api/video-reviews/${job.id}/video`} />
          <Timeline job={job} selected={segmentId} onSelect={selectSegment} highlighted={highlighted} />
        </div>
        <FrameView job={job} frameId={frameId} focus={focus} />
        <ol className="segment-list" aria-label="구간 목록">{job.segments.map(item => <li key={item.id}>
          <button className={item.id === segmentId ? 'current' : ''} onClick={() => selectSegment(item)}>
            <span className="track-time">{formatTime(item.start_ms)}–{formatTime(item.end_ms)}</span>
            <strong>{item.track_ids.length ? item.track_ids.map(id => tracks[id]?.text).join(' · ') : '자막 없음'}</strong>
            {highlighted.has(item.id) && <span className="track-meta">판정 근거 구간</span>}
          </button></li>)}</ol>
      </div>
      <div className="video-side">
        <div className="form-card">
          <div className="card-heading"><h2>기준별 결과</h2><span className="pill">영상당 1회 판정 · 기준 v{job.criteria_version}</span></div>
          {results.length ? <ul className="criterion-list">{results.filter(r => !r.whole_video).map(r => <CriterionCard key={r.criterion_id} result={r} onEvidence={showEvidence} />)}</ul>
            : <p className="help video-empty">입력한 기준이 없어 기준 판정을 하지 않았습니다.</p>}
          {results.some(r => r.whole_video) && <><h3 className="whole-title">영상 전체에서 확인할 항목</h3>
            <ul className="criterion-list">{results.filter(r => r.whole_video).map(r => <CriterionCard key={r.criterion_id} result={r} onEvidence={showEvidence} />)}</ul></>}
        </div>
        <DisclosureCard disclosure={job.disclosure} />
        <div className="form-card">
          <div className="card-heading"><h2>자막 트랙</h2><span className="pill">{job.runtime?.seconds_per_frame ? `장당 ${job.runtime.seconds_per_frame}초` : 'Paddle OCR'}</span></div>
          <ol className="track-list">{(job.tracks || []).map(track => <li key={track.id}><button onClick={() => showEvidence({ segment_id: job.segments.find(s => s.track_ids.includes(track.id))?.id, frame_id: track.rep_reading_frame, bbox_xyxy: track.bbox_xyxy, time_ms: track.start_ms })}>
            <span className="track-time">{formatTime(track.start_ms)}–{formatTime(track.end_ms)}</span>
            <strong>{track.text}</strong>
            <span className="track-meta">{roleLabels[track.role] || track.role} · 투표 {track.vote.count}/{track.vote.total}{track.requires_review ? ' · 과반 없음' : ''}{track.variants.length ? ` · 다르게 읽힘 ${track.variants.length}` : ''}</span>
          </button></li>)}</ol>
        </div>
      </div>
    </div>
  </>;
}

// 동영상을 올려 계획 문서의 0~7단계로 검수하는 시연 화면.
export default function VideoReviewPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const id = params.get('id');
  const { status, jobs, refresh } = useVideoStatus();
  const { job, error } = useVideoJob(id, refresh);
  const legacy = job && job.status === 'completed' && !job.coverage;

  return <div className="video-page">
    <header className="app-topbar video-topbar">
      <Brand onClick={() => navigate('/')} />
      <div className="topbar-right">
        <Link className="text-btn" to="/review/new">포스터 검수로</Link>
        <span className="user-label">{user?.display_name || user?.username}</span>
      </div>
    </header>
    <main className="app-main">
      <div className="page-heading"><div className="eyebrow">SHORT-FORM · 시연</div><h1 tabIndex="-1" id="page-title">숏폼 동영상 검수</h1>
        <p>표본 시각마다 프레임을 읽어 자막 트랙으로 묶고, 확정한 기준을 영상당 한 번 판정합니다. 광고·협찬 표시는 따로 판정합니다. 결과는 이 서버의 실행 폴더에만 저장됩니다.</p></div>
      <div className="video-top-grid">
        <UploadCard status={status} onSubmitted={value => { setParams({ id: value }); refresh(); }} />
        <div className="form-card">
          <div className="card-heading"><h2>최근 동영상 검수</h2><button className="text-btn" onClick={refresh}>새로 고침</button></div>
          {jobs.length ? <ul className="video-jobs">{jobs.map(item => <li key={item.id}><button className={item.id === id ? 'current' : ''} onClick={() => setParams({ id: item.id })}>
            <span>{new Date(item.created_at).toLocaleString('ko-KR')}</span>
            <strong>{item.project_name || '이전 형식 결과'} · {item.status === 'completed' ? '완료' : stageLabels[item.stage] || item.status}</strong>
            <span>{item.media ? `${formatTime(item.media.duration_ms)} · ${item.media.width}×${item.media.height}` : ''}</span>
          </button></li>)}</ul> : <p className="help video-empty">아직 실행한 동영상 검수가 없습니다.</p>}
        </div>
      </div>
      <ErrorMessage message={error} />
      {legacy && <ErrorMessage message="계획 문서 반영 전에 만든 결과라 새 화면으로 보여 줄 수 없습니다. 같은 영상을 다시 검수해 주세요." />}
      {job && !legacy && (job.status === 'completed' ? <Results key={job.id} job={job} /> : <Progress job={job} />)}
    </main>
  </div>;
}
