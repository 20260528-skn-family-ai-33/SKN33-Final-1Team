import React, { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { fetchJson } from '../../api/client.js';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import './logoConfirm.css';

// 동영상 검수 화면에 붙는 영상 로고 검수(/api/video-logos) 조각: 원본 로고 입력, 접수, 작업 상태 카드.
export const MAX_LOGOS = 12;
const RUNNING = new Set(['queued', 'running']);
export const logoStatusLabels = { queued: '접수됨', running: '후보 찾는 중', candidates_ready: '위치 확정 대기', failed: '실패' };
export const compareStatusLabels = { queued: '정밀 대조 대기', running: '정밀 대조 중', completed: '정밀 대조 완료', failed: '정밀 대조 실패' };

export function useLogoStatus() {
  const [status, setStatus] = useState(null);
  useEffect(() => {
    fetchJson('/api/video-logos/status').then(setStatus).catch(error => setStatus({ ready: false, detail: error.message }));
  }, []);
  return status;
}

// 원본 로고(최대 12장)와 로고마다 기준 문구. 기준 문구는 줄을 |로 나누고, 비우면 글자 비교를 하지 않는다.
export function LogoInputs({ logos, onChange, status, disabled }) {
  const [previews, setPreviews] = useState([]);
  useEffect(() => {
    const urls = logos.map(item => URL.createObjectURL(item.file)); setPreviews(urls);
    return () => urls.forEach(url => URL.revokeObjectURL(url));
  }, [logos]);
  function add(event) {
    const files = [...event.target.files].filter(file => file.type.startsWith('image/')); event.target.value = '';
    onChange([...logos, ...files.map(file => ({ file, text: '' }))].slice(0, MAX_LOGOS));
  }
  const off = disabled || (status && !status.ready);
  return <fieldset className="wide lf-inputs"><legend>원본 로고 (선택 · 로고 위치 확정과 정밀 대조)</legend>
    {status && !status.ready && <p className="help">{status.detail || '영상 로고 검수 서버가 준비되지 않았습니다.'}</p>}
    {logos.map((item, i) => <div key={i} className="lf-input-row">
      {previews[i] && <img src={previews[i]} alt={`원본 로고 ${item.file.name}`} />}
      <label>{item.file.name}
        <input value={item.text} maxLength={300} placeholder="기준 문구 (선택, 줄은 | 로 구분: 울산광역시남구|ULSAN NAMGU)" disabled={off}
          onChange={event => onChange(logos.map((x, k) => (k === i ? { ...x, text: event.target.value } : x)))} /></label>
      <button type="button" className="btn small secondary" disabled={disabled} onClick={() => onChange(logos.filter((_, k) => k !== i))}>빼기</button>
    </div>)}
    {logos.length < MAX_LOGOS && <label className="btn small secondary lf-add">원본 로고 이미지 추가
      <input className="visually-hidden" type="file" accept="image/*" multiple disabled={off} onChange={add} /></label>}
    <p className="help">{logos.length ? `원본 로고 ${logos.length}장 · 동영상 검수와 함께 같은 영상으로 로고 후보를 찾습니다.` : '올리지 않으면 로고 검수 없이 자막 검수만 합니다.'}</p>
  </fieldset>;
}

// 동영상 검수 접수 직후, 같은 영상과 원본 로고로 영상 로고 작업을 만든다.
export async function submitLogoJob({ video, logos, title, reviewId }) {
  const form = new FormData();
  form.append('video', video);
  logos.forEach(item => form.append('logos', item.file));
  form.append('logo_texts', JSON.stringify(logos.map(item => item.text.trim())));
  form.append('title', title);
  form.append('review_id', reviewId);
  return fetchJson('/api/video-logos', { method: 'POST', body: form });
}

// 동영상 검수와 함께 올린 로고 작업의 상태. 후보 찾기·정밀 대조가 도는 동안 다시 읽는다.
export function LogoFlowCard({ reviewId }) {
  const [jobs, setJobs] = useState(null); const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController(); let timer;
    async function poll() {
      try {
        const list = await fetchJson(`/api/video-logos?review_id=${encodeURIComponent(reviewId)}`, { signal: controller.signal });
        setJobs(list.items || []); setError('');
        if ((list.items || []).some(j => RUNNING.has(j.status) || RUNNING.has(j.compare_status))) timer = setTimeout(poll, 2000);
      } catch (failure) {
        if (failure.name === 'AbortError') return;
        setError(failure.message); timer = setTimeout(poll, 4000);
      }
    }
    poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [reviewId]);
  if (!jobs?.length && !error) return null;
  return <div className="form-card lf-flow-card">
    <div className="card-heading"><h2>영상 로고 검수</h2><span className="pill">위치 확정 → 정밀 대조 · 판정은 사람이</span></div>
    <ErrorMessage message={error} />
    <ul className="lf-flow-jobs">{(jobs || []).map(job => <li key={job.id}>
      <div className="lf-flow-head"><strong>{job.title}</strong>
        <span className={`vbadge ${job.status === 'failed' ? 'fail' : job.status === 'candidates_ready' ? 'pass' : 'review'}`}>{logoStatusLabels[job.status] || job.status}</span>
        {job.compare_status && <span className={`vbadge ${job.compare_status === 'failed' ? 'fail' : job.compare_status === 'completed' ? 'pass' : 'review'}`}>
          {compareStatusLabels[job.compare_status]}{RUNNING.has(job.compare_status) ? ` ${job.compare_progress || 0}%` : ''}</span>}</div>
      {RUNNING.has(job.status) && <>
        <div className="progress-track" role="progressbar" aria-label="로고 후보 찾기 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={job.progress || 0}>
          <div style={{ width: `${Math.min(100, job.progress || 0)}%` }} /></div>
        <p className="help">{job.stage_detail || '영상에서 1초마다 로고 후보를 찾고 있어요.'} · {job.progress || 0}%</p></>}
      {job.error && <ErrorMessage message={job.error} />}
      {job.warnings?.map(warning => <p key={warning} className="lf-missing">⚠ {warning}</p>)}
      {job.status === 'candidates_ready' && <div className="lf-flow-actions">
        <Link className="btn primary small" to={`/video/logo?job=${job.id}`}>로고 위치 확정하기</Link>
        {job.compare_status === 'completed' && <Link className="btn small secondary" to={`/video/logo?job=${job.id}&view=compare`}>정밀 대조 근거 보기</Link>}
      </div>}
    </li>)}</ul>
  </div>;
}
