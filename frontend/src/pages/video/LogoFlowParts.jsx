import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { fetchJson } from '../../api/client.js';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { Icon } from '../../components/Icon.jsx';

// 동영상 검수 화면에 붙는 영상 로고 검수(/api/video-logos) 조각: 원본 로고 입력, 접수, 작업 상태.
export const MAX_LOGOS = 12;
const RUNNING = new Set(['queued', 'running']);
export const logoStatusLabels = { queued: '접수됨', running: '후보 찾는 중', candidates_ready: '위치 확정 대기', failed: '실패' };
export const compareStatusLabels = { queued: '정밀 대조 대기', running: '정밀 대조 중', completed: '정밀 대조 완료', failed: '정밀 대조 실패' };

export function useLogoStatus() {
  const [status, setStatus] = useState(null);
  useEffect(() => {
    const controller = new AbortController();
    fetchJson('/api/video-logos/status', { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setStatus(value); })
      .catch(error => { if (!controller.signal.aborted) setStatus({ ready: false, detail: error.message }); });
    return () => controller.abort();
  }, []);
  return status;
}

// 영상·포스터 모두 로고별 기준 문구(선택)를 입력한다. 포스터(referenceOnly)는 JPG·PNG·WEBP·SVG만 받고 SVG는 접수할 때 PNG로 바꾼다.
export function LogoInputs({ logos, onChange, status, disabled, referenceOnly = false }) {
  const [previews, setPreviews] = useState([]);
  const [error, setError] = useState('');
  const [picked, setPicked] = useState(() => new Set());
  const [dragging, setDragging] = useState(false);
  const input = useRef(null);
  useEffect(() => {
    const urls = logos.map(item => URL.createObjectURL(item.file)); setPreviews(urls);
    return () => urls.forEach(url => URL.revokeObjectURL(url));
  }, [logos]);
  const off = disabled || Boolean(status && !status.ready);
  const selectedCount = logos.filter(item => picked.has(item.file)).length;
  function add(files) {
    if (off || !files.length) return;
    const supported = [...files].filter(file => referenceOnly
      ? /\.(jpe?g|png|webp|svg)$/i.test(file.name) && (!file.type || file.type.startsWith('image/'))
      : file.type.startsWith('image/'));
    setError(supported.length !== files.length ? (referenceOnly ? 'JPG, PNG, WEBP, SVG 파일을 선택해 주세요.' : '이미지 파일을 선택해 주세요.')
      : logos.length + supported.length > MAX_LOGOS ? `로고는 최대 ${MAX_LOGOS}장까지 첨부할 수 있어요.` : '');
    if (supported.length && logos.length < MAX_LOGOS) onChange([...logos, ...supported.map(file => ({ file, text: '' }))].slice(0, MAX_LOGOS));
  }
  function pick(file, checked) {
    setPicked(previous => {
      const next = new Set(previous);
      if (checked) next.add(file); else next.delete(file);
      return next;
    });
  }
  function removePicked() {
    onChange(logos.filter(item => !picked.has(item.file)));
    setPicked(new Set());
    setError('');
  }
  return <section className="rv-form-block rv-reference-logos" aria-label="원본 로고 첨부">
    <h2>원본 로고 첨부</h2>
    <div className={`rv-form-upload rv-reference-logo-drop${dragging ? ' is-dragging' : ''}`}
      onDragOver={event => { if (event.dataTransfer.types.includes('Files')) { event.preventDefault(); if (!off) setDragging(true); } }}
      onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget)) setDragging(false); }}
      onDrop={event => { event.preventDefault(); setDragging(false); add(event.dataTransfer.files); }}>
      <div className="rv-form-row rv-reference-logo-row"><span>여러 로고를 끌어다 놓을 수 있어요.</span>
        <input ref={input} type="file" hidden accept={referenceOnly ? '.jpg,.jpeg,.png,.webp,.svg' : 'image/*'} multiple disabled={off}
          onChange={event => { add(event.target.files); event.target.value = ''; }} />
        <button type="button" className="rv-button secondary rv-compact" disabled={off || logos.length >= MAX_LOGOS} onClick={() => input.current?.click()}>로고 선택</button>
      </div>
      {logos.length > 0 && <>
        <ul className="rv-reference-logo-list">{logos.map((item, index) => <li key={`${item.file.name}-${index}`}>
          <label>
            <input type="checkbox" checked={picked.has(item.file)} disabled={disabled} onChange={event => pick(item.file, event.target.checked)} />
            {previews[index] && <img src={previews[index]} alt="" />}
            <span title={item.file.name}>{item.file.name}</span>
          </label>
          <input type="text" className="rv-reference-logo-text" value={item.text} maxLength={300} disabled={off}
            aria-label={`${item.file.name} 기준 문구`} placeholder="기준 문구 (선택, 줄은 | 로 구분)"
            onChange={event => onChange(logos.map((logo, position) => position === index ? { ...logo, text: event.target.value } : logo))} />
        </li>)}</ul>
        <div className="rv-form-row rv-reference-logo-row"><span role="status">{selectedCount}개 선택</span>
          <button type="button" className="rv-button secondary rv-compact" disabled={disabled || !selectedCount} onClick={removePicked}>선택한 로고 삭제</button></div>
      </>}
    </div>
    <p className="rv-form-hint">{referenceOnly ? '포스터를 등록하면 원본 로고의 위치 후보를 찾습니다. 위치를 확정하면 원본과 정밀 대조합니다.'
      : '같은 영상에서 로고 위치 후보를 찾습니다. 위치를 확정하면 원본과 정밀 대조합니다.'}</p>
    <ErrorMessage message={error || (status && !status.ready ? status.detail || '영상 로고 검수 서버가 준비되지 않았습니다.' : '')} />
  </section>;
}

// 동영상 검수 접수 직후, 같은 영상과 원본 로고로 영상 로고 작업을 만든다.
export async function submitLogoJob({ video, logos, title, reviewId, signal }) {
  const form = new FormData();
  form.append('video', video);
  logos.forEach(item => form.append('logos', item.file));
  form.append('logo_texts', JSON.stringify(logos.map(item => item.text.trim())));
  form.append('title', title);
  form.append('review_id', reviewId);
  return fetchJson('/api/video-logos', { method: 'POST', body: form, signal });
}

// 동영상 검수와 함께 올린 로고 작업을 읽는다. 후보 찾기·정밀 대조가 도는 동안 다시 읽는다.
// 화면 단계가 바뀌어도 조회가 다시 시작되지 않도록 페이지에서 한 번만 쓴다.
export function useLogoJobs(reviewId) {
  const [state, setState] = useState({ reviewId: null, jobs: null, error: '' });
  useEffect(() => {
    if (!reviewId) return undefined;
    const controller = new AbortController(); let timer;
    async function poll() {
      try {
        const list = await fetchJson(`/api/video-logos?review_id=${encodeURIComponent(reviewId)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        const items = list.items || [];
        setState({ reviewId, jobs: items, error: '' });
        if (items.some(j => RUNNING.has(j.status) || RUNNING.has(j.compare_status))) timer = setTimeout(poll, 2000);
      } catch (failure) {
        if (controller.signal.aborted) return;
        setState(previous => ({ reviewId, jobs: previous.reviewId === reviewId ? previous.jobs : null, error: failure.message }));
        timer = setTimeout(poll, 4000);
      }
    }
    poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [reviewId]);
  return state.reviewId === reviewId ? state : { reviewId, jobs: null, error: '' };
}

// 로고 작업은 판정이 아니라 처리 상태라서 충족·위반 색을 쓰지 않는다: 처리 중 · 사람 차례 · 끝남 · 오류.
const logoState = job => (job.status === 'failed' ? 'is-error' : job.status === 'candidates_ready' ? 'is-todo' : 'is-running');
const compareState = job => (job.compare_status === 'failed' ? 'is-error' : job.compare_status === 'completed' ? 'is-done' : 'is-running');

function LogoChips({ job }) {
  return <>
    <span className={`rv-video-logo-state ${logoState(job)}`}><span aria-hidden="true" />{logoStatusLabels[job.status] || job.status}</span>
    {job.compare_status && <span className={`rv-video-logo-state ${compareState(job)}`} title={job.compare_status === 'completed' ? '정밀 대조는 판정 없이 근거만 모읍니다.' : undefined}>
      <span aria-hidden="true" />{compareStatusLabels[job.compare_status]}{RUNNING.has(job.compare_status) ? ` ${job.compare_progress || 0}%` : ''}</span>}
  </>;
}

function LogoActions({ job }) {
  if (job.status !== 'candidates_ready') return null;
  return <div className="rv-video-logo-actions">
    <Link className="rv-button primary" to={`/video/logo?job=${job.id}`}>로고 위치 확정하기</Link>
    {job.compare_status === 'completed' && <Link className="rv-button secondary" to={`/video/logo?job=${job.id}&view=compare`}>정밀 대조 근거 보기</Link>}
  </div>;
}

// 진행 화면 아래에 두는 로고 작업 카드. 후보 찾기 진행률은 서버 값 그대로 보여 준다.
export function LogoFlowCard({ jobs, error }) {
  if (!jobs?.length && !error) return null;
  return <section className="rv-panel rv-video-logo-card" aria-labelledby="video-logo-title">
    <div className="rv-panel-heading"><div><h2 id="video-logo-title">영상 로고 검수</h2><p>위치 확정 → 정밀 대조 순서로 진행합니다. 판정은 사람이 합니다.</p></div></div>
    <div className="rv-video-logo-body">
      <ErrorMessage message={error} />
      <ul className="rv-video-logo-jobs">{(jobs || []).map(job => <li key={job.id}>
        <div className="rv-video-logo-head"><strong>{job.title}</strong><LogoChips job={job} /></div>
        {RUNNING.has(job.status) && <>
          <div className="rv-video-logo-track" role="progressbar" aria-label="로고 후보 찾기 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={job.progress || 0}>
            <div style={{ width: `${Math.min(100, job.progress || 0)}%` }} /></div>
          <p className="rv-video-logo-detail">{job.stage_detail || '영상에서 1초마다 로고 후보를 찾고 있어요.'} · {job.progress || 0}%</p></>}
        {job.error && <ErrorMessage message={job.error} />}
        {job.warnings?.map(warning => <p key={warning} className="rv-video-inline-warning"><Icon name="warning" />{warning}</p>)}
        <LogoActions job={job} />
      </li>)}</ul>
    </div>
  </section>;
}

// 결과 화면 머리글에 두는 한 줄 요약. 다음에 할 일(위치 확정·근거 보기)은 버튼으로, 실패 사유·빠진 구간 경고는 그 아래에 남긴다.
export function LogoFlowInline({ jobs, error, notice }) {
  if (!jobs?.length && !error && !notice) return null;
  return <div className="rv-video-logo-inline" role="group" aria-label="영상 로고 검수">
    {notice && <p className="rv-video-inline-warning" role="alert"><Icon name="warning" />{notice}</p>}
    {error && <p className="rv-video-inline-warning"><Icon name="warning" />{error}</p>}
    {(jobs || []).map(job => <React.Fragment key={job.id}>
      <div className="rv-video-logo-inline-item" title={[job.title, job.stage_detail].filter(Boolean).join(' · ') || undefined}>
        <span className="rv-video-logo-inline-title"><Icon name="image" />영상 로고 검수</span>
        <LogoChips job={job} />
        {RUNNING.has(job.status) && <span className="rv-video-logo-inline-progress">{job.progress || 0}%</span>}
        <LogoActions job={job} />
      </div>
      {job.error && <p className="rv-video-inline-warning" role="alert"><Icon name="warning" />{job.error}</p>}
      {job.warnings?.map(warning => <p key={warning} className="rv-video-inline-warning"><Icon name="warning" />{warning}</p>)}
    </React.Fragment>)}
  </div>;
}
