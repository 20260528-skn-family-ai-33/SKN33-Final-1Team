import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { fetchJson } from '../../api/client.js';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { autoCompareBody, bundleOutcome, groupDifferences, OUTCOME } from './logoFlow.js';
import { formatTime } from './videoFormat.js';

// 동영상 검수 화면에 붙는 영상 로고 검수(/api/video-logos) 조각: 원본 로고 입력, 접수, 작업 상태.
export const MAX_LOGOS = 12;
const RUNNING = new Set(['queued', 'running']);
export const logoStatusLabels = { queued: '접수됨', running: '후보 찾는 중', candidates_ready: '후보 찾기 완료', failed: '실패' };
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
    <ErrorMessage message={error || (status && !status.ready ? status.detail || '영상 로고 검수 서버가 준비되지 않았습니다.' : '')} />
  </section>;
}

// 동영상 검수 접수 직후, 같은 영상과 원본 로고로 영상 로고 작업을 만든다. 로고만 검수하면 reviewId 없이 만든다.
// detector: 위치 후보를 찾는 곳. 'openai'(일반 자료) 또는 'runpod'(보안 자료·기본). 정밀 대조는 둘 다 RunPod다.
// 동영상 검수와 함께 접수하면(reviewId) 영상을 다시 올리지 않고, 서버가 그 검수에 저장한 영상을 쓴다.
// 연결된 영상을 쓸 수 없는 서버(예전 배포·연결 설정 없음)가 거절하면 그때만 영상을 함께 올린다.
export async function submitLogoJob({ video, logos, title, reviewId, detector = 'runpod', signal }) {
  const send = withVideo => {
    const form = new FormData();
    if (withVideo) form.append('video', video);
    logos.forEach(item => form.append('logos', item.file));
    form.append('logo_texts', JSON.stringify(logos.map(item => item.text.trim())));
    form.append('title', title);
    if (reviewId) form.append('review_id', reviewId);
    form.append('detector', detector === 'openai' ? 'openai' : 'runpod');
    return fetchJson('/api/video-logos', { method: 'POST', body: form, signal });
  };
  if (!reviewId) return send(true);
  try {
    return await send(false);
  } catch (failure) {
    // 새 서버는 LINKED_VIDEO_UNAVAILABLE로 거절한다. 영상 칸이 필수인 예전 서버는 입력 검증 오류로 거절하는데,
    // FastAPI 그대로면 'Field required', 인증 앱(backend/app/main.py)으로 감싼 AWS 영상 서비스는 INVALID_AUTH_REQUEST다.
    const needsVideo = failure?.status === 422 && (['LINKED_VIDEO_UNAVAILABLE', 'INVALID_AUTH_REQUEST'].includes(failure.code)
      || /Field required/i.test(failure.message || ''));
    if (!needsVideo || signal?.aborted) throw failure;
    return send(true);
  }
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

// 진행 화면의 처리 단계(01~08) 뒤에 잇는 로고 단계: 09 후보 찾기, 10 정밀 대조.
// 로고 작업은 동영상 검수와 함께 돌지만 같은 목록에서 상태를 보이게 한다. 진행률은 서버 값 그대로 쓴다.
const percent = value => `${Math.min(100, Math.max(0, Math.round(value || 0)))}%`;

function mergedStep(states, details) {
  if (!states.length) return { status: 'pending' };
  if (states.every(state => state === 'completed')) return { status: 'completed' };
  const active = states.findIndex(state => state === 'active');
  if (active >= 0) return { status: 'active', detail: details[active] };
  return { status: states.includes('failed') ? 'failed' : 'pending' };
}

export function logoProgressSteps(jobs) {
  if (!jobs?.length) return [];
  const candidates = mergedStep(
    jobs.map(job => (job.status === 'candidates_ready' ? 'completed' : job.status === 'failed' ? 'failed' : RUNNING.has(job.status) ? 'active' : 'pending')),
    jobs.map(job => (job.status === 'queued' ? '대기' : percent(job.progress))));
  const compared = jobs.filter(job => job.compare_status);
  const compare = mergedStep(
    compared.map(job => (job.compare_status === 'completed' ? 'completed' : job.compare_status === 'failed' ? 'failed' : 'active')),
    compared.map(job => (job.compare_status === 'queued' ? '대기' : percent(job.compare_progress))));
  // 후보 찾기가 끝나지 않은 작업이 남아 있으면 정밀 대조 전체를 완료로 보이지 않는다.
  if (compare.status === 'completed' && compared.length < jobs.length) compare.status = 'pending';
  return [
    { key: 'logo_candidates', label: '로고 후보 찾기', ...candidates,
      title: jobs.map(job => job.stage_detail).find(Boolean) || '영상에서 1초마다 로고 후보를 찾습니다.' },
    { key: 'logo_compare', label: '로고 정밀 대조', ...compare,
      title: '순위가 높은 후보로 원본 로고와 자동 대조합니다. 최종 판정은 담당자가 합니다.' },
  ];
}

// 처리 단계 목록 아래에 두는 로고 작업 오류·경고. 단계 목록에 넣기 어려운 문장만 남긴다.
export function LogoProgressNotes({ jobs, error }) {
  return <>
    <ErrorMessage message={error} />
    {(jobs || []).map(job => <React.Fragment key={job.id}>
      <ErrorMessage message={job.error} />
      {job.warnings?.map(warning => <p key={warning} className="rv-video-inline-warning"><Icon name="warning" />{warning}</p>)}
    </React.Fragment>)}
  </>;
}

// 후보 찾기가 끝난 작업의 로고 검수 화면 바로가기. 진행 화면의 버튼 줄에 그대로 넣는다.
export function LogoResultLinks({ jobs }) {
  return (jobs || []).filter(job => job.status === 'candidates_ready').map(job => <Link key={job.id}
    className={`rv-button ${job.compare_status === 'completed' ? 'primary' : 'secondary'}`} to={`/video/logo?job=${job.id}`}>
    {job.compare_status === 'completed' ? '로고 검수 결과 확인' : '로고 검수 보기'}</Link>);
}

// 결과 화면에 바로 보이는 영상 로고 정밀 대조 결과. 후보를 고르는 화면을 거치지 않고, 서버가 후보 찾기 직후
// 자동으로 고른 후보(auto_selection)로 대조한 결과를 보여 준다. 검수하려던 로고가 아니면 '로고 후보 다시 선택'으로
// 위치 확인 화면(/video/logo?view=confirm)을 열어 후보를 고친 뒤 다시 대조한다. 결과가 바뀌면 이 목록도 다시 읽는다.
// 구간 타임라인 패널 아래에 붙는다(VideoResults의 Timeline children). onSeek(ms)를 주면 대표 시각 단추로 그 시점에 간다.
export function LogoCompareResults({ jobs, error, notice, onSeek }) {
  if (!jobs?.length && !error && !notice) return null;
  return <section className="rv-video-logo-result" aria-labelledby="video-logo-result-title">
    <header className="rv-video-logo-result-head">
      <h3 id="video-logo-result-title">로고 정밀 대조</h3>
      <p>원본 로고마다 자동으로 고른 후보를 대조했어요. 차이 근거가 있으면 보류, 없으면 통과입니다. 최종 판정은 담당자가 합니다.</p>
    </header>
    {notice && <p className="rv-video-inline-warning" role="alert"><Icon name="warning" />{notice}</p>}
    <ErrorMessage message={error} />
    {(jobs || []).map(job => <LogoCompareJob key={job.id} job={job} onSeek={onSeek} />)}
  </section>;
}

const secondText = sec => formatTime(sec * 1000);
// 묶음 결과를 화면의 판정 알약(충족·위반·확인 필요와 같은 색)으로 보인다. 글자는 로고 대조의 이름(통과·보류·비교 정보 부족)이다.
const OUTCOME_STATUS = { match: 'pass', diff: 'fail', unknown: 'review' };

function LogoProgress({ label, value }) {
  return <div className="rv-video-logo-progress" role="status">
    <div><span>{label}</span><strong>{value}</strong></div>
    <div className="rv-video-logo-track" aria-hidden="true"><i style={{ width: /%$/.test(value) ? value : '8%' }} /></div>
  </div>;
}

// 정밀 대조 기록이 없는 작업(후보 찾기 뒤 자동 대조를 하지 않는 서버가 만든 작업)을 서버 auto_selection과 같은
// 규칙으로 고른 후보로 대조한다. 이미 대조 중이면(409) 그대로 둔다. video가 없으면 후보 자료(data.json)를 읽는다.
export async function startAutoCompare(base, { video, signal } = {}) {
  const data = video || await fetchJson(base + '/video/data.json', { signal });
  try {
    await fetchJson(base + '/compare', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(autoCompareBody(data)), signal });
  } catch (failure) {
    if (failure?.status !== 409) throw failure;
  }
}

function LogoCompareJob({ job, onSeek }) {
  const base = `/api/video-logos/${encodeURIComponent(job.id)}`;
  const ready = job.status === 'candidates_ready';
  // 후보 찾기가 끝나면 정밀 대조 상태·결과를 직접 읽는다(목록 조회에는 결과·실패 사유가 없다). 대조 중이면 2초마다 다시 읽고,
  // 대조 기록이 아예 없으면 한 번만 자동 대조를 요청한다.
  const [result, setResult] = useState(null);
  const [loadError, setLoadError] = useState('');
  const requested = useRef(false);
  useEffect(() => {
    setResult(null); setLoadError('');
    if (!ready) return undefined;
    const controller = new AbortController(); let timer;
    async function poll() {
      try {
        const value = await fetchJson(base + '/compare', { signal: controller.signal });
        if (controller.signal.aborted) return;
        setResult(value); setLoadError('');
        if (!value.compare_status && !requested.current) {
          requested.current = true;
          await startAutoCompare(base, { signal: controller.signal });
          if (!controller.signal.aborted) timer = setTimeout(poll, 500);
        } else if (RUNNING.has(value.compare_status)) timer = setTimeout(poll, 2000);
      } catch (failure) {
        if (!controller.signal.aborted) setLoadError(failure.message);
      }
    }
    poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [base, ready, job.compare_status]);

  const status = result ? result.compare_status : job.compare_status;
  const bundles = result?.bundles || EMPTY_BUNDLES;
  const canReselect = ready && !RUNNING.has(status) && !!result;
  let state = null;
  if (job.status === 'failed') state = <ErrorMessage message={job.error || '로고 후보 찾기에 실패했습니다.'} />;
  else if (RUNNING.has(job.status)) state = <LogoProgress label="로고 후보 찾는 중" value={job.status === 'queued' ? '대기' : percent(job.progress)} />;
  else if (loadError) state = <ErrorMessage message={loadError} />;
  else if (!result) state = <p className="rv-video-logo-result-note" role="status">정밀 대조 결과를 불러오고 있어요.</p>;
  else if (!status) state = <LogoProgress label="자동으로 고른 후보로 정밀 대조 시작" value="대기" />;
  else if (RUNNING.has(status)) state = <LogoProgress label="정밀 대조 중" value={status === 'queued' ? '대기' : percent(result.compare_progress)} />;
  else if (status === 'failed') state = <ErrorMessage message={result.compare_error || '정밀 대조가 완료되지 않았습니다.'} />;
  else if (!bundles.length) state = <p className="rv-video-logo-result-note">영상에서 원본 로고 후보를 찾지 못해 대조한 구간이 없어요. 후보를 직접 골라 다시 대조할 수 있어요.</p>;
  const warnings = [...(job.warnings || []), ...(result?.warnings || [])];
  const tally = bundles.reduce((counts, bundle) => {
    const tone = OUTCOME[bundleOutcome(bundle.evidence?.outcomes)].tone;
    return { ...counts, [tone]: (counts[tone] || 0) + 1 };
  }, {});

  return <div className="rv-video-logo-job">
    <div className="rv-video-logo-job-head">
      <div className="rv-video-logo-job-title">
        {bundles.length > 0 && <span className="rv-video-logo-tally">{['match', 'diff', 'unknown'].filter(tone => tally[tone])
          .map(tone => <span key={tone} className={`is-${tone}`}>{OUTCOME[tone].label} {tally[tone]}</span>)}</span>}</div>
      <div className="rv-video-logo-result-actions">
        {bundles.length > 0 && <Link className="rv-button secondary rv-compact" to={`/video/logo?job=${encodeURIComponent(job.id)}`}>정밀 대조 근거 상세 보기</Link>}
        {canReselect && <Link className="rv-button secondary rv-compact" to={`/video/logo?job=${encodeURIComponent(job.id)}&view=confirm`}>로고 후보 다시 선택</Link>}
      </div></div>
    {state}
    {bundles.length > 0 && <ul className="rv-video-logo-rows">{bundles.map(bundle => {
      const evidence = bundle.evidence || {};
      const outcome = OUTCOME[bundleOutcome(evidence.outcomes)];
      const differences = groupDifferences(bundle.per_sec);
      const name = bundle.reference || `원본 로고 ${bundle.logo + 1}`;
      const several = bundles.filter(item => item.logo === bundle.logo).length > 1;
      const rep = evidence.representative;
      const facts = [`비교 ${evidence.n_secs || 0}초`, bundle.skipped?.length ? `건너뜀 ${bundle.skipped.length}초` : null,
        differences.length ? `차이 ${differences.map(group => `${group.kind} ${group.secs}초`).join(', ')}`
          : outcome.tone === 'match' ? '차이 없음' : null].filter(Boolean).join(' · ');
      return <li key={`${bundle.logo}-${bundle.crop}`} className={`rv-video-logo-row is-${outcome.tone}`}>
        <div className="rv-video-logo-pair">
          <span className="rv-video-logo-thumb" title="원본 로고"><img src={`${base}/refs/${bundle.logo}.png`} alt={`${name} 원본`} /></span>
          <span className="rv-video-logo-vs" aria-hidden="true">→</span>
          {rep ? <span className="rv-video-logo-thumb" title={`영상 ${secondText(rep.sec)}`}><img src={`${base}/compare/rep/${bundle.logo}/${bundle.crop}.jpg`}
            alt={`${secondText(rep.sec)} 영상 속 ${name}`} /></span>
            : <span className="rv-video-logo-thumb is-empty">프레임 없음</span>}
        </div>
        <div className="rv-video-logo-row-text"><strong title={name}>{name}{several ? ` · 구간 ${bundle.crop + 1}` : ''}</strong>
          <span>{facts}</span></div>
        {rep && (onSeek
          ? <button type="button" className="rv-video-logo-seek" onClick={() => onSeek(rep.sec * 1000)}
            aria-label={`${secondText(rep.sec)} 시점으로 이동`}><Icon name="arrow" />{secondText(rep.sec)}</button>
          : <span className="rv-video-logo-seek">{secondText(rep.sec)}</span>)}
        <span className={`rv-result-status ${OUTCOME_STATUS[outcome.tone]}`}><span aria-hidden="true" />{outcome.label}</span>
      </li>;
    })}</ul>}
    {warnings.map(warning => <p key={warning} className="rv-video-inline-warning"><Icon name="warning" />{warning}</p>)}
  </div>;
}
const EMPTY_BUNDLES = [];
