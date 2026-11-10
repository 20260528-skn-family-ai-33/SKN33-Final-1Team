import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchJson } from '../../api/client.js';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { useWorkspaceReset } from '../../workspace/WorkspaceSession.jsx';
import { parseRequirements } from '../review/workflow.js';
import { LogoInputs, submitLogoJob, useLogoStatus } from './LogoFlowParts.jsx';
import { criteriaPayload, formatTime } from './videoFormat.js';
import VideoDocumentInput from './VideoDocumentInput.jsx';

const ACCEPT = 'video/mp4,video/quicktime,video/webm,video/x-matroska,video/x-msvideo,video/avi,.avi';
const VIDEO_NAME = /\.(mp4|m4v|mov|webm|mkv|avi)$/i;
const MiB = 1048576;

// 선택한 영상을 브라우저에서 미리 재생하고 길이·해상도를 읽는다. 읽지 못하는 형식도 검수 접수는 막지 않는다.
// 브라우저가 녹화한 WebM은 길이 정보가 없을(Infinity) 수 있어, 유한한 길이만 쓴다.
function useLocalPreview(file) {
  const [url, setUrl] = useState(null);
  const [meta, setMeta] = useState(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setMeta(null); setFailed(false);
    if (!file) { setUrl(null); return undefined; }
    const value = URL.createObjectURL(file); setUrl(value);
    return () => URL.revokeObjectURL(value);
  }, [file]);
  function read(element) {
    const duration = Number.isFinite(element.duration) && element.duration > 0 ? element.duration : null;
    setMeta({ duration, width: element.videoWidth || null, height: element.videoHeight || null });
  }
  return { url, meta, failed, read, setFailed };
}

// 영상·확정 기준·광고 입력을 한 화면에서 받아 접수한다(기준과 광고 입력은 이 시점에 고정).
export default function VideoInput({ status, draft, setDraft, file, setFile, logos, setLogos, onSubmitted, busy, setBusy }) {
  const [documentBusy, setDocumentBusy] = useState(false);
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState(false);
  const input = useRef(null);
  const chooseButton = useRef(null);
  const replaceButton = useRef(null);
  const lastFile = useRef(file);
  const active = useRef(null);
  useWorkspaceReset(() => active.current?.abort());
  useEffect(() => () => active.current?.abort(), []);
  const logoStatus = useLogoStatus();
  const preview = useLocalPreview(file);
  const limits = status?.limits;
  const criteria = useMemo(() => parseRequirements(draft.requirements, draft.documentSources), [draft.requirements, draft.documentSources]);
  const information = criteria.filter(c => c.type === 'information').length;
  const { ad } = draft;
  const uploadMiB = limits?.upload_bytes ? Math.floor(limits.upload_bytes / MiB) : null;
  const limitText = [(limits?.formats?.length ? limits.formats.map(f => f.toUpperCase()).join(' · ') : 'MP4 · MOV · WEBM · AVI'),
    uploadMiB && `최대 ${uploadMiB}MB`, limits?.duration_seconds && `${limits.duration_seconds}초 이하`,
    limits?.short_side_min && `짧은 변 ${limits.short_side_min}px 이상`].filter(Boolean).join(' · ');
  // 자체 광고는 서버가 시작·끝 0.2초 표본을 뽑지 않으므로 1초 표본만 안내한다.
  const sampling = status?.sampling && (ad.kind === 'self' ? '1초 간격 표본 검사' : status.sampling);

  // 파일을 고르거나 지우면 초점을 다음에 누를 버튼으로 옮겨 키보드 사용자가 위치를 잃지 않게 한다.
  useEffect(() => {
    if (lastFile.current === file) return;
    lastFile.current = file;
    (file ? replaceButton : chooseButton).current?.focus();
  }, [file]);

  // 서버 한도를 넘는 길이·해상도는 미리 알려 주되, 판정은 서버가 하므로 접수 버튼은 막지 않는다.
  const warnings = [];
  const duration = preview.meta?.duration;
  if (duration && limits?.duration_seconds && duration > limits.duration_seconds + .05) warnings.push(`영상 길이 ${formatTime(duration * 1000)}가 서버 한도 ${limits.duration_seconds}초를 넘어 접수되지 않을 수 있어요.`);
  if (preview.meta?.width && preview.meta?.height && limits?.short_side_min && Math.min(preview.meta.width, preview.meta.height) < limits.short_side_min) warnings.push(`짧은 변이 ${Math.min(preview.meta.width, preview.meta.height)}px라 서버 기준 ${limits.short_side_min}px보다 작습니다.`);

  function choose(selected) {
    setError('');
    if (!selected) return;
    if (limits?.upload_bytes && selected.size > limits.upload_bytes) {
      setFile(null); if (input.current) input.current.value = '';
      setError(`동영상은 ${Math.floor(limits.upload_bytes / MiB)}MB 이하여야 합니다.`);
      return;
    }
    setFile(selected);
  }
  function drop(event) {
    event.preventDefault(); setDragging(false);
    if (busy) return;
    const selected = [...event.dataTransfer.files].find(item => item.type.startsWith('video/') || VIDEO_NAME.test(item.name));
    if (selected) choose(selected); else setError('MP4 · MOV · WEBM · AVI 동영상 파일을 놓아 주세요.');
  }
  function removeFile() { setFile(null); setError(''); if (input.current) input.current.value = ''; }
  const update = (key, value) => { setError(''); setDraft(previous => ({ ...previous, [key]: value })); };
  const field = (key, value) => { setError(''); setDraft(previous => ({ ...previous, ad: { ...previous.ad, [key]: value } })); };

  function applyDocument(rows) {
    const existing = new Set(criteria.map(item => item.text));
    const added = rows.filter(row => !existing.has(row.text));
    const requirements = [draft.requirements.trim(), ...added.map(row => row.text)].filter(Boolean).join('\n');
    if (requirements.length > 6000 || criteria.length + added.length > 100) {
      setError('검수 요구사항을 6,000자 및 100개 이내로 정리해 주세요.');
      return false;
    }
    setError('');
    setDraft(previous => ({ ...previous, requirements, documentSources: [...added, ...(previous.documentSources || [])] }));
    return true;
  }

  async function submit() {
    if (!file || busy || documentBusy) return;
    const controller = new AbortController(); active.current = controller;
    const { signal } = controller;
    const title = draft.projectName.trim() || '숏폼 검수';
    setBusy(true); setError('');
    try {
      const form = new FormData();
      form.append('video', file);
      form.append('criteria', JSON.stringify(criteriaPayload(criteria)));
      form.append('criteria_version', '1');
      form.append('project_name', title);
      form.append('disclosure', JSON.stringify(ad));
      const created = await fetchJson('/api/video-reviews', { method: 'POST', body: form, signal });
      if (signal.aborted) return;
      // 원본 로고를 올렸으면 같은 영상으로 영상 로고 작업도 만든다. 실패해도 동영상 검수는 그대로 진행한다.
      let logoError = '';
      if (logos.length) {
        try { await submitLogoJob({ video: file, logos, title, reviewId: created.id, signal }); setLogos([]); }
        catch (failure) { logoError = `동영상 검수는 접수했지만 로고 검수를 접수하지 못했습니다: ${failure.message}`; }
      }
      if (signal.aborted) return;
      removeFile();
      onSubmitted(created.id, logoError);
    } catch (failure) { if (!signal.aborted) setError(failure.message); }
    finally { if (!signal.aborted) { active.current = null; setBusy(false); } }
  }

  const chooseFile = () => input.current?.click();
  const fileMeta = file ? [`${(file.size / MiB).toFixed(1)}MB`, duration && formatTime(duration * 1000),
    preview.meta?.width && preview.meta?.height && `${preview.meta.width}×${preview.meta.height}`].filter(Boolean).join(' · ') : limitText;

  return <section className="rv-video-input" aria-labelledby="page-title">
    <h1 id="page-title" className="sr-only" tabIndex={-1}>숏폼 동영상 검수</h1>
    {/* 입력란에서 Enter를 눌러도 접수되지 않도록 폼 제출을 막고, 접수는 버튼으로만 한다. */}
    <form className="rv-input-form" onSubmit={event => event.preventDefault()}>
      <div className="rv-editor-grid">
        <div className="rv-video-input-column">
          <input id="video-file" ref={input} className="visually-hidden" type="file" accept={ACCEPT} aria-label="검수 동영상 선택" tabIndex={-1}
            onChange={event => choose(event.target.files[0] || null)} disabled={busy} />
          <div className={`rv-input-drop-target${dragging ? ' is-dragging' : ''}`} onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true); }}
            onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget)) setDragging(false); }} onDrop={drop}>
            <section className="image-stage rv-video-stage" aria-label="동영상 작업 영역">
              <div className="image-stage-heading"><div className="image-stage-file"><span className="image-stage-icon"><Icon name="film" /></span>
                <div><strong>검수할 동영상</strong><span>{file ? file.name : '동영상 파일 1개'}</span></div></div><span className="image-stage-original">원본</span></div>
              <div className="image-stage-viewport rv-video-stage-viewport">
                {!file ? <div className="rv-input-empty">
                  <span className="rv-input-empty-icon" aria-hidden="true"><Icon name="film" /></span>
                  <h2>{dragging ? '놓으면 바로 올라가요.' : '여기에 동영상을 놓아 주세요.'}</h2>
                  <p>영상을 끌어다 놓거나<br />파일을 선택해 시작하세요.</p>
                  <button type="button" ref={chooseButton} className="rv-button primary" onClick={chooseFile} disabled={busy}><Icon name="upload" />파일 선택</button>
                  <span>{status ? limitText : '서버 한도를 확인하고 있어요.'}</span>
                </div> : preview.failed ? <div className="image-stage-empty"><Icon name="film" /><strong className="rv-video-empty-title">미리 볼 수 없는 형식이에요.</strong>
                  <p>브라우저가 이 파일을 재생하지 못해 미리보기만 생략합니다.<br />검수 접수는 그대로 진행할 수 있어요.</p></div>
                  : <video className="rv-video-preview" src={preview.url || undefined} controls muted playsInline preload="metadata"
                    onLoadedMetadata={event => preview.read(event.currentTarget)} onDurationChange={event => preview.read(event.currentTarget)}
                    onError={() => preview.setFailed(true)} />}
              </div>
            </section>
          </div>
          <div className="rv-input-filebar">
            <div className="rv-input-filemeta"><Icon name={file ? 'film' : 'info'} /><div><strong>{file ? file.name : '원본 동영상으로 준비해 주세요.'}</strong><span>{fileMeta}</span></div></div>
            {file && <div className="rv-input-fileactions"><button type="button" ref={replaceButton} onClick={chooseFile} disabled={busy}>다른 동영상 선택</button><button type="button" onClick={removeFile} disabled={busy}>삭제</button></div>}
          </div>
          <div role="status">{warnings.map(text => <p key={text} className="rv-video-inline-warning"><Icon name="warning" />{text}</p>)}</div>

        </div>

        <aside className="rv-panel rv-input-inspector rv-video-brief" aria-label="프로젝트와 검수 기준">
          <div className="rv-panel-heading"><h2>검수 요구사항</h2><span className="rv-input-count">기준 {criteria.length}개</span></div>
          <div className="rv-input-fields">
            <div className="rv-input-field rv-input-requirements">
              <div className="rv-input-field-heading"><label htmlFor="video-requirements">검수 요구사항</label><span>한 줄에 한 항목</span></div>
              <p className="rv-input-writing-hint" id="video-requirements-hint">화면 글자로 확인할 내용을 적어 주세요. 비워 두면 기준 판정 없이 자막 트랙과 광고 표시만 봅니다.</p>
              <textarea id="video-requirements" rows={6} value={draft.requirements} disabled={busy} aria-describedby="video-requirements-hint"
                placeholder={'행사명: 김천김밥축제\n기간: 2026.10.23 ~ 10.25\n"사전 예약 필수"'} onChange={event => update('requirements', event.target.value)} />
              <div className="rv-input-counter"><span>{criteria.length ? `정보 ${information} · 시각 ${criteria.length - information}` : '입력한 기준 없음'}</span><span>{criteria.length}개 항목</span></div>
            </div>
            <VideoDocumentInput projectName={draft.projectName} disabled={busy} busy={documentBusy} setBusy={setDocumentBusy} onApply={applyDocument} />
            <LogoInputs logos={logos} onChange={value => { setError(''); setLogos(value); }} status={logoStatus} disabled={busy} />
            <fieldset className="rv-video-subsection" disabled={busy}><legend>광고·협찬 표시</legend>
              <p className="rv-video-subsection-hint">기준과 따로, 게시물 제목·캡션과 화면 문구로 광고 표시 규정을 판정합니다.</p>
              <div className="rv-video-subsection-grid">
                <div className="rv-input-field"><label htmlFor="video-ad-title">게시물 제목</label><input id="video-ad-title" value={ad.title} maxLength={500} autoComplete="off" onChange={event => field('title', event.target.value)} /></div>
                <div className="rv-input-field"><label htmlFor="video-ad-caption">캡션·태그</label><input id="video-ad-caption" value={ad.caption} maxLength={5000} autoComplete="off" onChange={event => field('caption', event.target.value)} /></div>
              </div>
              <label className="rv-video-check"><input type="checkbox" checked={ad.kind === 'self'} onChange={event => field('kind', event.target.checked ? 'self' : 'endorsement')} />
                <span><strong>자사·자체 광고</strong><small>표시 의무가 없어 광고 표시 검수에서 제외합니다.</small></span></label>
              <label className="rv-video-check"><input type="checkbox" checked={ad.celebrity_exposure} onChange={event => field('celebrity_exposure', event.target.checked)} />
                <span><strong>PPL/협찬</strong><small>화면 표시를 시작·끝 3초 기준으로 판단합니다.</small></span></label>
            </fieldset>
          </div>
        </aside>
      </div>
      <div className="rv-input-error"><ErrorMessage message={error} />
        {status && !status.ready && <ErrorMessage message={status.detail || '동영상 검수 서버가 준비되지 않았습니다.'} />}</div>
      <div className="rv-actionbar rv-input-actionbar">
        <div className="rv-input-next-note"><Icon name="lock" /><div><strong>기준·광고 입력은 접수 때 고정됩니다.</strong>
          <p>{[criteria.length ? `기준 ${criteria.length}개` : '기준 없음', ad.kind === 'self' ? '광고 표시 제외' : '광고 표시 판정', logos.length ? `로고 ${logos.length}장` : null,
            sampling ? `${sampling} · CPU에서는 프레임 1장에 수 초~20초` : null].filter(Boolean).join(' · ')}</p></div></div>
        <button type="button" className="rv-button primary" onClick={submit} disabled={!file || busy || documentBusy || !status?.ready}>{busy ? '올리는 중…' : '동영상 검수 시작'}{!busy && <Icon name="arrow" />}</button>
      </div>
    </form>
  </section>;
}
