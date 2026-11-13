import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { fetchJson } from '../../api/client.js';
import { SiteHeader } from '../../components/SiteHeader.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { PixelField } from '../../components/PixelField.jsx';
import { useWorkspaceReset } from '../../workspace/WorkspaceSession.jsx';
import { frameworkJson } from './frameworkClient.js';
import '../../styles/my-tasks.css';

const labels = { queued: '처리 대기', extracting: '문서 분석 중', reviewing: '검수 중',
  waiting_criteria: '기준 확인 필요', waiting_asset: '포스터 등록 필요', waiting_review: '담당자 승인 대기',
  waiting_revision: '수정본 대기', completed: '완료', failed: '실패', outcome_unknown: '결과 확인 필요', cancelled: '취소됨' };
const kindLabels = { poster: '포스터 검수', video: '동영상 검수' };
const taskTime = new Intl.DateTimeFormat('ko-KR', { dateStyle: 'short', timeStyle: 'short', timeZone: 'Asia/Seoul' });

export default function MyTasksPage() {
  const resetWorkspace = useWorkspaceReset();
  const [visibility, setVisibility] = useState('visible');
  const [cursors, setCursors] = useState([null]);
  const [page, setPage] = useState(null);
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [notice, setNotice] = useState('');
  const reading = useRef(null), action = useRef(null);
  const title = useRef(null);
  const visibilitySelect = useRef(null);
  const pagination = useRef(null);
  const returnFocus = useRef(null);
  const before = cursors.at(-1);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' });
    title.current?.focus({ preventScroll: true });
  }, []);

  useEffect(() => () => action.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController(); reading.current = controller;
    setLoading(true); setPage(null); setLoadError('');
    const query = new URLSearchParams({ visibility, limit: '30', ...(before ? { before } : {}) });
    const options = { signal: controller.signal, cache: 'no-store' };
    // 영상 목록 API는 최근 20건을 반환한다. 첫 페이지에서만 합쳐 문서 이력 커서에 영향을 주지 않는다.
    const includeVideos = !before && visibility !== 'hidden';
    Promise.allSettled([frameworkJson('/my-tasks?' + query, options),
      includeVideos ? fetchJson('/api/video-reviews', { ...options, notifyAuthFailure: false }) : null,
    ]).then(([runs, videos]) => {
      if (controller.signal.aborted) return;
      const warnings = [];
      if (runs.status === 'rejected') warnings.push(runs.reason?.status === 404
        ? '문서·포스터 이력 기능을 사용할 수 없습니다. 서버 적용 상태를 확인해 주세요.'
        : runs.reason?.message || '문서·포스터 이력을 불러오지 못했습니다.');
      if (videos.status === 'rejected') warnings.push('동영상 이력을 불러오지 못했습니다.');
      if (runs.status === 'fulfilled' || videos.value) {
        const recorded = runs.value || { items: [], has_more: false };
        const recent = (videos.value?.items || []).filter(item => typeof item?.id === 'string' && item.id).map(item => ({
          run_id: item.id, project_name: item.project_name || '동영상 검수', kind: 'video', created_at: item.created_at,
          state: item.status === 'running' ? 'reviewing' : item.status,
        }));
        setPage({ ...recorded, items: [...recorded.items, ...recent].sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0)) });
      }
      setLoadError(warnings.join(' '));
      setLoading(false);
    });
    return () => controller.abort();
  }, [visibility, before, reload]);

  useEffect(() => {
    if (loading || !returnFocus.current) return;
    const pageButton = returnFocus.current === 'page' && pagination.current?.querySelector('button:not(:disabled)');
    (pageButton || visibilitySelect.current)?.focus({ preventScroll: true });
    returnFocus.current = null;
  }, [loading]);

  function refresh() {
    returnFocus.current = 'visibility';
    setError('');
    setNotice('');
    setReload(n => n + 1);
  }

  function changePage(nextCursors) {
    returnFocus.current = 'page';
    setNotice('');
    setError('');
    setCursors(nextCursors);
  }

  async function perform(item, operation) {
    if (action.current) return;
    const controller = new AbortController(); action.current = controller;
    returnFocus.current = 'visibility';
    reading.current?.abort(); setBusy(true); setError(''); setNotice('');
    try {
      const cancel = operation === 'cancel';
      await frameworkJson(`/runs/${encodeURIComponent(item.run_id)}/${cancel ? 'cancel' : 'history-visibility'}`, {
        method: 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cancel ? {} : { hidden: operation === 'hide' }),
      });
      if (!controller.signal.aborted) setNotice(cancel ? '대기 작업을 취소했습니다. 원본과 기록은 보존됩니다.'
        : operation === 'hide' ? '이력을 숨겼습니다. 숨긴 이력에서 다시 표시할 수 있습니다.' : '이력을 다시 표시했습니다.');
    } catch (err) { if (!controller.signal.aborted) setError(err.message); }
    finally {
      if (!controller.signal.aborted) { action.current = null; setBusy(false); setReload(n => n + 1); }
    }
  }

  return <div className="rv-workspace rv-workspace--tasks">
    <a href="#review-content" className="rv-skip">작업 영역으로 이동</a>
    <SiteHeader current="/review/tasks" locked={busy} lockReason="작업 변경을 저장하고 있습니다." />
    {busy && <span id="mt-lock-reason" className="sr-only">작업 변경을 저장하고 있습니다.</span>}
    <main className="rv-main rv-my-tasks" id="review-content" aria-labelledby="tasks-title" tabIndex={-1}>
      <header className="rv-task-heading"><PixelField monochrome /><div><span className="rv-eyebrow">MY TASKS</span><h1 id="tasks-title" ref={title} tabIndex={-1}>내 작업</h1>
        <p>문서 분석과 포스터 검수 작업을 다시 열 수 있어요. 최근 동영상 검수는 기본 목록과 전체 이력의 첫 페이지에 표시돼요.</p></div>
        {busy ? <button type="button" className="rv-button primary" disabled>새 검수</button>
          : <Link className="rv-button primary" to="/review/new" onClick={event => {
            if (!event.defaultPrevented && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) resetWorkspace();
          }}>새 검수</Link>}
      </header>
      <section className="rv-panel rv-task-panel" aria-label="내 작업 목록">
        <div className="rv-task-toolbar">
          <div className="rv-task-view"><label>이력 표시 <select ref={visibilitySelect} value={visibility} disabled={busy}
            onChange={event => {
              setVisibility(event.target.value); setCursors([null]); setError(''); setNotice('');
            }}>
            <option value="visible">기본 목록</option><option value="hidden">숨긴 이력</option><option value="all">전체 이력</option>
          </select></label>{page && <span className="rv-task-count">{page.items.length}건</span>}</div>
          <button type="button" className="rv-button secondary" disabled={loading || busy} onClick={refresh}>새로고침</button>
        </div>
        {page && <ErrorMessage message={loadError} />}
        <ErrorMessage message={error} />
        {notice && <p className="rv-task-notice" role="status">{notice}</p>}
        {loading && <div className="rv-task-state" role="status"><span className="rv-task-spinner" aria-hidden="true" /><strong>내 작업을 불러오고 있어요.</strong></div>}
        {!loading && !page && <div className="rv-task-state">
          <div role="alert"><strong>내 작업을 불러오지 못했어요.</strong><p>{loadError}</p></div>
          <button type="button" className="rv-button primary" onClick={refresh}>다시 시도</button>
        </div>}
        {page && <>{page.items.length ? <div className="rv-task-table-wrap"><table className="rv-task-table"><caption className="sr-only">내 작업 이력</caption>
          <thead><tr><th scope="col">작업</th><th scope="col">접수 시간</th><th scope="col">상태</th><th scope="col">관리</th></tr></thead>
          <tbody>{page.items.map(item => <tr key={`${item.kind}:${item.run_id}`}>
            <td><strong>{item.project_name}</strong><span>{kindLabels[item.kind] || '문서 분석'} · {item.run_id.slice(0, 8)}</span></td>
            <td className="rv-task-time">{Number.isFinite(Date.parse(item.created_at)) ? <time dateTime={item.created_at}>{taskTime.format(new Date(item.created_at))}</time> : '시각 정보 없음'}</td>
            <td><div className="rv-task-badges"><span className={`rv-task-status is-${item.state}`}>{labels[item.state] || item.state}</span>
              {item.hidden && <span className="rv-task-hidden">숨김</span>}</div></td>
            <td><div className="rv-task-actions">
              {busy ? <button type="button" className="rv-button secondary" disabled>열기</button>
                : <Link className="rv-button secondary" aria-label={`${item.project_name} 열기`}
                   to={(item.kind === 'video' ? '/video?id=' : '/review/new?framework=') + encodeURIComponent(item.run_id)}
                   state={{ projectName: item.project_name }}>열기</Link>}
              {item.can_cancel && <button type="button" className="rv-button secondary" disabled={busy}
                aria-label={`${item.project_name} 대기 취소`} onClick={() => perform(item, 'cancel')}>대기 취소</button>}
              {item.hidden ? <button type="button" className="rv-button secondary" disabled={busy} aria-label={`${item.project_name} 다시 표시`}
                onClick={() => perform(item, 'restore')}>다시 표시</button>
                : item.can_hide && <button type="button" className="rv-button secondary" disabled={busy} aria-label={`${item.project_name} 이력 숨기기`}
                  onClick={() => perform(item, 'hide')}>이력 숨기기</button>}
            </div></td>
          </tr>)}</tbody></table></div>
          : <div className="rv-task-state" role="status"><span className="rv-task-state-icon"><Icon name="file" /></span>
            <strong>{visibility === 'hidden' ? '숨긴 이력이 없어요.' : '표시할 작업이 없어요.'}</strong>
            <p>{visibility === 'hidden' ? '완료된 작업을 숨기면 여기에서 다시 찾을 수 있어요.' : '검수를 시작하면 여기에서 작업을 확인할 수 있어요.'}</p>
          </div>}
        </>}
        {!loading && (cursors.length > 1 || page?.has_more) && <nav className="rv-task-pagination" ref={pagination} aria-label="목록 페이지">
          <button type="button" className="rv-button secondary" disabled={busy || cursors.length === 1}
            onClick={() => changePage(cursors.slice(0, -1))}>이전</button><span>{cursors.length}페이지</span>
          <button type="button" className="rv-button secondary" disabled={busy || !page?.has_more}
            onClick={() => changePage([...cursors, page.next_cursor])}>다음</button>
        </nav>}
      </section>
    </main>
  </div>;
}
