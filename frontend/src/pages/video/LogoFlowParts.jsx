import React, { useEffect, useState } from 'react';
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

// 포스터는 화면 대조용 이미지를 미리 보고, 영상은 로고별 기준 문구도 입력한다.
export function LogoInputs({ logos, onChange, status, disabled, referenceOnly = false }) {
  const [previews, setPreviews] = useState([]);
  const [referenceError, setReferenceError] = useState('');
  useEffect(() => {
    const urls = logos.map(item => URL.createObjectURL(item.file)); setPreviews(urls);
    return () => urls.forEach(url => URL.revokeObjectURL(url));
  }, [logos]);
  const off = disabled || (status && !status.ready);
  function add(files) {
    const supported = [...files].filter(file => referenceOnly
      ? /\.(jpe?g|png|webp|svg)$/i.test(file.name) && (!file.type || file.type.startsWith('image/'))
      : file.type.startsWith('image/'));
    if (referenceOnly) {
      setReferenceError(supported.length !== files.length ? 'JPG, PNG, WEBP, SVG 파일을 선택해 주세요.'
        : logos.length + supported.length > MAX_LOGOS ? `로고는 최대 ${MAX_LOGOS}장까지 첨부할 수 있어요.` : '');
    }
    onChange([...logos, ...supported.map(file => ({ file, text: '' }))].slice(0, MAX_LOGOS));
  }
  return <fieldset className={`rv-video-subsection rv-video-logos${referenceOnly ? ' rv-poster-logos' : ''}`}
    onDragOver={referenceOnly ? event => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); } : undefined}
    onDrop={referenceOnly ? event => {
      if (!event.dataTransfer.files.length) return;
      event.preventDefault();
      if (!off) add(event.dataTransfer.files);
    } : undefined}>
    <legend>{referenceOnly ? '원본 로고 첨부' : '5. 원본 로고'} <span>선택</span></legend>
    <p className="rv-video-subsection-hint">{referenceOnly ? '포스터와 대조할 로고를 끌어다 놓거나 선택하세요.'
      : '올리면 같은 영상에서 로고 위치 후보를 찾고, 위치를 확정한 뒤 정밀 대조합니다. 판정은 사람이 합니다.'}</p>
    {status && !status.ready && <p className="rv-video-inline-warning"><Icon name="warning" />{status.detail || '영상 로고 검수 서버가 준비되지 않았습니다.'}</p>}
    {logos.length > 0 && <ul className="rv-video-logo-list">{logos.map((item, i) => <li key={`${item.file.name}-${i}`}>
      {previews[i] ? <img src={previews[i]} alt={`원본 로고 ${item.file.name}`} /> : <span className="rv-video-logo-thumb" />}
      {referenceOnly ? <span className="rv-logo-filename" title={item.file.name}>{item.file.name}</span> : <label><span>{item.file.name}</span>
        <input value={item.text} maxLength={300} placeholder="기준 문구 (선택, 줄은 | 로 구분: 울산광역시남구|ULSAN NAMGU)" disabled={off}
          onChange={event => onChange(logos.map((x, k) => (k === i ? { ...x, text: event.target.value } : x)))} /></label>}
      <button type="button" className="rv-video-text-button" disabled={disabled} aria-label={referenceOnly ? `${item.file.name} 삭제` : undefined}
        onClick={() => { setReferenceError(''); onChange(logos.filter((_, k) => k !== i)); }}>빼기</button>
    </li>)}</ul>}
    {logos.length < MAX_LOGOS && <label className={`rv-button secondary rv-video-logo-add${off ? ' is-disabled' : ''}`}><Icon name="upload" />원본 로고 이미지 추가
      <input className="visually-hidden" type="file" accept={referenceOnly ? '.jpg,.jpeg,.png,.webp,.svg' : 'image/*'} multiple disabled={off}
        onChange={event => { add(event.target.files); event.target.value = ''; }} /></label>}
    {referenceOnly ? <p className="rv-video-subsection-foot" role="status">{referenceError || `${logos.length} / ${MAX_LOGOS}장 · 이 화면의 미리보기용입니다.`}</p>
      : <p className="rv-video-subsection-foot">{logos.length ? `원본 로고 ${logos.length}장 · 동영상 검수와 함께 같은 영상으로 로고 후보를 찾습니다.` : '올리지 않으면 로고 검수 없이 자막 검수만 합니다.'}</p>}
  </fieldset>;
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
