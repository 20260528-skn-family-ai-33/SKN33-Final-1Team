import React, { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { fetchJson } from '../../api/client.js';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { POSTER_LOGO_API } from './posterLogoFlow.js';

// 포스터 검수 화면에 붙는 포스터 로고 검수 조각: 작업 조회와 상태 카드. 로고 작업은 판정이 아니라 처리 상태다.
const RUNNING = new Set(['queued', 'running']);
export const posterLogoStatusLabels = { queued: '접수됨', running: '후보 찾는 중', candidates_ready: '위치 확정 대기', failed: '실패' };
export const posterCompareLabels = { queued: '정밀 대조 대기', running: '정밀 대조 중', completed: '정밀 대조 완료', failed: '정밀 대조 실패' };

// 포스터 검수 실행에 연결한 로고 작업을 읽는다. 후보 찾기·정밀 대조가 도는 동안 다시 읽는다.
export function usePosterLogoJobs(runId, reload = 0) {
  const [state, setState] = useState({ runId: null, jobs: null, error: '' });
  useEffect(() => {
    if (!runId) return undefined;
    const controller = new AbortController(); let timer; let seen = false;
    async function poll() {
      try {
        const list = await fetchJson(`${POSTER_LOGO_API}?run_id=${encodeURIComponent(runId)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        const items = list.items || [];
        seen ||= items.length > 0;
        setState({ runId, jobs: items, error: '' });
        if (items.some(job => RUNNING.has(job.status) || RUNNING.has(job.compare_status))) timer = setTimeout(poll, 2000);
      } catch (failure) {
        if (controller.signal.aborted) return;
        // 로고 작업을 본 적이 없으면(로고 API 미연결·로고 미첨부) 카드를 숨기고 다시 묻지 않는다. 포스터 검수는 그대로 쓴다.
        if (!seen) { setState({ runId, jobs: [], error: '' }); return; }
        setState(previous => ({ runId, jobs: previous.runId === runId ? previous.jobs : null, error: failure.message }));
        timer = setTimeout(poll, 5000);
      }
    }
    poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [runId, reload]);
  return state.runId === runId ? state : { runId, jobs: null, error: '' };
}

const jobState = job => (job.status === 'failed' ? 'is-error' : job.status === 'candidates_ready' ? 'is-todo' : 'is-running');
const compareState = job => (job.compare_status === 'failed' ? 'is-error' : job.compare_status === 'completed' ? 'is-done' : 'is-running');

// 포스터 검수 진행·결과 화면 아래에 두는 카드. 위치 확정 → 정밀 대조 순서로 사람이 진행한다.
export function PosterLogoCard({ jobs, error, notice }) {
  if (!jobs?.length && !error && !notice) return null;
  return <section className="rv-panel rv-video-logo-card rv-poster-logo-card" aria-labelledby="poster-logo-title">
    <div className="rv-panel-heading"><div><h2 id="poster-logo-title">포스터 로고 검수</h2>
      <p>후보 찾기 → 위치 확정 → 정밀 대조 순서로 진행합니다. 기준 문구 검수와 별도이며, 로고 일치 여부는 사람이 판단합니다.</p></div></div>
    <div className="rv-video-logo-body">
      {notice && <p className="rv-video-inline-warning" role="alert"><Icon name="warning" />{notice}</p>}
      <ErrorMessage message={error} />
      <ul className="rv-video-logo-jobs">{(jobs || []).map(job => <li key={job.id}>
        <div className="rv-video-logo-head"><strong>{job.title}</strong>
          <span className={`rv-video-logo-state ${jobState(job)}`}><span aria-hidden="true" />{posterLogoStatusLabels[job.status] || job.status}</span>
          {job.compare_status && <span className={`rv-video-logo-state ${compareState(job)}`} title="정밀 대조는 판정 없이 근거만 모읍니다.">
            <span aria-hidden="true" />{posterCompareLabels[job.compare_status]}{RUNNING.has(job.compare_status) ? ` ${job.compare_progress || 0}%` : ''}</span>}
        </div>
        {RUNNING.has(job.status) && <>
          <div className="rv-video-logo-track" role="progressbar" aria-label="로고 후보 찾기 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={job.progress || 0}>
            <div style={{ width: `${Math.min(100, job.progress || 0)}%` }} /></div>
          <p className="rv-video-logo-detail">{job.stage_detail || '포스터에서 원본 로고 후보를 찾고 있어요.'} · {job.progress || 0}%</p></>}
        {job.status === 'candidates_ready' && <p className="rv-video-logo-detail">원본 로고 {job.logo_count}개 · 후보 {job.candidate_count ?? 0}개를 찾았어요.</p>}
        {job.error && <ErrorMessage message={job.error} />}
        {job.compare_error && job.compare_status === 'failed' && <ErrorMessage message={job.compare_error} />}
        {job.status === 'candidates_ready' && <div className="rv-video-logo-actions">
          <Link className="rv-button primary" to={`/review/logo?job=${job.id}`}>로고 위치 확정하기</Link>
          {job.compare_status === 'completed' && <Link className="rv-button secondary" to={`/review/logo?job=${job.id}&view=compare`}>정밀 대조 근거 보기</Link>}
        </div>}
      </li>)}</ul>
    </div>
  </section>;
}
