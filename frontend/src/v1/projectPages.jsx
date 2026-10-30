import React, { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { useAuth } from './auth.jsx';
import { useAction, useDirty, useResource } from './hooks.jsx';
import * as api from './api.js';
import * as C from './contracts.js';
import { Title, Resource, Pager, DirtyDialog } from './components.jsx';
import { ErrorMessage } from '../components/ErrorMessage.jsx';
import { parseRequirements } from '../workflow.js';
import ImageStage from '../components/ImageStage.jsx';
const role = value => ({ owner: '소유자', editor: '편집자', viewer: '조회자' })[value];
export function ProjectsPage() {
  const auth = useAuth(); const [params, setParams] = useSearchParams(); const navigate = useNavigate(); const archived = params.get('archived') || 'exclude'; const cursor = params.get('cursor');
  const state = useResource(`${auth.user.id}:${archived}:${cursor}`, signal => api.projects({ archived, cursor }, signal)); const action = useAction(`create:${auth.user.id}`); const [name, setName] = useState('');
  async function create(event) { event.preventDefault(); if (!name.trim() || C.codepoints(name) > 120) return action.setError('프로젝트 이름은 1~120자로 입력해 주세요.'); const value = await action.run(signal => api.createProject(name, signal)); if (value) navigate(`/projects/${value.id}/review/new`); }
  return <>
    <div className="v2-dashboard-intro"><div><Title title="내 검수 프로젝트">기준을 정리하고, 결과물을 확인하고, 다음 작업을 이어가세요.</Title><div className="v2-intro-caption"><span className="v2-small-mark" aria-hidden="true">✳</span><span>하나의 기준에서 시작하는 꼼꼼한 검토.</span></div></div>
      <form className="v2-create-panel" onSubmit={create}><span className="v2-kicker">START SOMETHING NEW</span><h2>새 작업을 시작할까요?</h2><label htmlFor="project-name">새 프로젝트 이름</label><div className="v2-create-input"><input id="project-name" value={name} placeholder="예: 가을 캠페인 포스터" onChange={event => setName(event.target.value)} disabled={action.busy} /><button className="btn primary" disabled={action.busy}>프로젝트 만들기</button></div><ErrorMessage message={action.error} /></form>
    </div>
    <section className="v2-projects-section"><div className="v2-section-heading"><div><span className="v2-kicker">PROJECT LIBRARY</span><h2>이어서 작업하기</h2></div><label className="v2-inline-label">보관 상태<select aria-label="프로젝트 보관 상태" value={archived} onChange={event => setParams({ archived: event.target.value })}><option value="exclude">진행 중</option><option value="include">모두</option><option value="only">보관됨</option></select></label></div>
    <Resource state={state}>{page => <><div className="v2-project-table"><div className="v2-project-table-head" aria-hidden="true"><span>프로젝트</span><span>내 역할</span><span>상태</span><span>다음 작업</span></div>{page.items.map((project, index) => <article className="v2-project-row" key={project.id}><div className="v2-project-name"><span className="v2-project-icon" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><path d="M7 8h7l3 3h8v14H7z" stroke="currentColor" strokeWidth="1.5"/><path d="M7 15h18" stroke="currentColor" strokeWidth="1.5"/></svg></span><div><span className="v2-row-number">PROJECT {String(index + 1).padStart(2, '0')}</span><h3><Link to={`/projects/${project.id}/review/new`}>{project.name}</Link></h3></div></div><span className="v2-role">{role(project.my_role)}</span><span className={`v2-project-state ${project.business_state === 'archived' ? 'is-archived' : ''}`}><i aria-hidden="true" />{project.business_state === 'archived' ? '보관됨' : '진행 중'}</span><div className="v2-row-actions"><Link className="text-btn" to={`/projects/${project.id}/reviews`}>검수 이력</Link><Link className="btn secondary" to={`/projects/${project.id}/review/new`}>프로젝트 열기 <span aria-hidden="true">↗</span></Link></div></article>)}</div>{!page.items.length && <div className="v2-empty"><span aria-hidden="true">＋</span><h3>표시할 프로젝트가 없습니다.</h3><p>위에서 새 프로젝트를 만들거나 보관 상태를 변경해 주세요.</p></div>}<Pager page={page} path="/projects" filters={{ archived }} /></>}</Resource></section>
  </>;
}
function ProjectControls({ project, reload, dirty }) {
  const action = useAction(`project:${project.id}`); const [name, setName] = useState(project.name);
  return <section className="v1-project-header v2-project-header"><div><h2>{project.name}</h2><p className="help">내 역할: {role(project.my_role)} · {project.business_state === 'archived' ? '보관된 프로젝트입니다. 기존 자료를 조회할 수 있습니다.' : '진행 중'}</p></div>{project.my_role === 'owner' && <details><summary>프로젝트 관리</summary><form className="v1-inline" onSubmit={async event => { event.preventDefault(); if (dirty) return; if (!name.trim() || C.codepoints(name) > 120) return action.setError('이름은 1~120자로 입력해 주세요.'); const value = await action.run(signal => api.patchProject(project.id, { name }, signal)); if (value) reload(); }}><label htmlFor="rename-project">프로젝트 이름</label><input id="rename-project" value={name} onChange={event => setName(event.target.value)} /><button className="btn secondary" disabled={action.busy || dirty}>이름 저장</button></form><button className="btn secondary" disabled={action.busy || dirty} onClick={async () => { const value = await action.run(signal => api.patchProject(project.id, { archived: project.business_state !== 'archived' }, signal)); if (value) reload(); }}>{project.business_state === 'archived' ? '프로젝트 복구' : '프로젝트 보관'}</button><ErrorMessage message={action.error} /></details>}</section>;
}
export function NewDraftPage() {
  const { projectId } = useParams(); const state = useResource(`new:${projectId}`, async signal => { const [project, sets, images] = await Promise.all([api.project(projectId, signal), api.sets(projectId, {}, signal), api.images(projectId, {}, signal)]); return { project, sets, images }; });
  return <Resource state={state}>{value => <NewDraftForm key={projectId} {...value} reload={state.reload} />}</Resource>;
}
function NewDraftForm({ project, sets: initialSets, images: initialImages, reload }) {
  const navigate = useNavigate(); const [params, setParams] = useSearchParams(); const pid = project.id; const action = useAction(`new:${pid}`);
  const [sets, setSets] = useState(initialSets); const [images, setImages] = useState(initialImages); const [setId, setSetId] = useState(params.get('criteria_set_id') || '');
  const [setName, setSetName] = useState(''); const [requirements, setRequirements] = useState(''); const [file, setFile] = useState(null); const [preview, setPreview] = useState('');
  const fileInput = useRef(null);
  const imageId = params.get('image_id'); const selected = useResource(`selected:${pid}:${imageId}`, signal => imageId ? api.image(imageId, pid, signal) : Promise.resolve(null));
  const [saved, setSaved] = useState(false); const blocker = useDirty(!saved && (!!requirements || !!file)); const canWrite = C.editable(project);
  const selectedImageReady = !!imageId && !selected.loading && !selected.error && selected.data?.id === imageId;
  useEffect(() => { if (!file) { setPreview(''); return; } const url = URL.createObjectURL(file); setPreview(url); return () => URL.revokeObjectURL(url); }, [file]);
  function clearPendingFile() {
    // Changing file also runs the preview effect cleanup, revoking its object URL.
    setFile(null); setPreview('');
    if (fileInput.current) fileInput.current.value = '';
  }
  function selectStoredImage(id) {
    if (action.busy) return;
    clearPendingFile(); action.setError('');
    setParams(current => { const next = new URLSearchParams(current); if (id) next.set('image_id', id); else next.delete('image_id'); return next; }, { replace: true });
  }
  function selectFile(event) {
    if (!canWrite || action.busy) return;
    const nextFile = event.target.files[0];
    if (!nextFile) { clearPendingFile(); return; }
    if (nextFile.size > 20 * 1024 * 1024) { clearPendingFile(); action.setError('파일은 20 MiB 이하여야 합니다.'); return; }
    setPreview(''); setFile(nextFile); action.setError('');
    // A local preview is not the previously registered image or a saved asset.
    setParams(current => { const next = new URLSearchParams(current); next.delete('image_id'); return next; }, { replace: true });
  }
  async function upload() {
    if (!file || !canWrite || action.busy) return;
    const value = await action.run(signal => api.upload(pid, file, signal)); if (!value) return;
    clearPendingFile(); setParams(current => { const next = new URLSearchParams(current); next.set('image_id', value.id); return next; }, { replace: true });
    setImages(current => ({ ...current, items: [value, ...current.items.filter(item => item.id !== value.id)] }));
  }
  async function save(event) {
    event.preventDefault();
    if (!canWrite || action.busy) return;
    if (file) return action.setError('선택한 새 이미지를 먼저 등록해 주세요.');
    const items = parseRequirements(requirements); const error = C.contentError({ raw_requirements: requirements, items });
    if (error || !setId || !selectedImageReady) return action.setError(error || '등록된 이미지와 기준 묶음을 선택해 주세요.');
    const value = await action.run(signal => api.createDraft(pid, { criteria_set_id: setId, image_id: imageId, raw_requirements: requirements, items, source_documents: null }, signal));
    if (value) { blocker.allow(); setSaved(true); setRequirements(''); navigate(`/projects/${pid}/drafts/${value.id}`, { state: { saved: true } }); }
  }
  return <><Title title="이미지와 요구사항을 준비하세요.">이미지를 먼저 등록하고 서버에 초안을 저장합니다.</Title><ProjectControls project={project} reload={reload} dirty={!!requirements || !!file} />
    {!canWrite && <p className="v1-notice">현재 역할 또는 보관 상태에서는 새 자료를 저장할 수 없습니다. <Link to={`/projects/${pid}/drafts`}>기존 초안 조회</Link></p>}
    <div className="v2-preparation-grid"><section className="v2-image-column"><div className="v2-upload-controls"><h2>검수 이미지</h2><p className="help">정적 PNG·JPEG·WEBP, 20 MiB 이하. 실제 형식과 치수는 서버에서 확인합니다.</p>
      <label htmlFor="image-file">새 이미지 선택</label><input id="image-file" ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" disabled={!canWrite || action.busy} onChange={selectFile} />
      <button className="btn secondary" disabled={!file || !canWrite || action.busy} onClick={upload}>이미지 등록</button>
      <label htmlFor="stored-image">등록된 이미지</label><select id="stored-image" value={imageId || ''} disabled={action.busy} onChange={event => selectStoredImage(event.target.value)}><option value="">선택해 주세요</option>{selected.data && !images.items.some(row => row.id === selected.data.id) && <option value={selected.data.id}>등록 이미지 · {selected.data.width}×{selected.data.height}</option>}{images.items.map(image => <option key={image.id} value={image.id}>등록 이미지 · {image.width}×{image.height} · {image.id.slice(0, 8)}</option>)}</select>
      {images.has_more && <button className="text-btn" disabled={action.busy} onClick={async () => { const page = await action.run(signal => api.images(pid, { cursor: images.next_cursor }, signal)); if (page) setImages(current => ({ ...page, items: [...current.items, ...page.items.filter(item => !current.items.some(old => old.id === item.id))] })); }}>이미지 더 불러오기</button>}
      </div>{file ? <ImageStage src={preview || undefined} alt="아직 업로드하지 않은 이미지 미리보기" title="업로드 전 미리보기" subtitle="이미지를 등록하면 서버에 저장됩니다." empty="선택한 이미지의 미리보기를 준비하고 있어요." /> : imageId ? <Resource state={selected}>{image => image && <ImageStage src={image.url} alt="서버에 등록된 검수 이미지" title="검수 이미지" subtitle={`서버에 등록됨 · ${image.width}×${image.height}`} width={image.width} height={image.height} />}</Resource> : <ImageStage title="검수 이미지" empty="이미지를 등록하거나 기존 이미지를 선택해 주세요." />}
      <p className="help">업로드 전 새로고침하면 파일을 다시 선택해야 합니다. 등록된 이미지는 URL로 다시 불러옵니다.</p>
    </section><form className="form-card v2-editor-panel" onSubmit={save}><h2>검수 요구사항</h2><label htmlFor="criteria-set">기준 묶음</label><select id="criteria-set" value={setId} disabled={!canWrite || action.busy} onChange={event => { setSetId(event.target.value); setParams(current => { const next = new URLSearchParams(current); next.set('criteria_set_id', event.target.value); return next; }, { replace: true }); }}><option value="">선택해 주세요</option>{sets.items.map(set => <option key={set.id} value={set.id}>{set.name}</option>)}</select>
      {sets.has_more && <button type="button" className="text-btn" disabled={action.busy} onClick={async () => { const page = await action.run(signal => api.sets(pid, { cursor: sets.next_cursor }, signal)); if (page) setSets(current => ({ ...page, items: [...current.items, ...page.items.filter(item => !current.items.some(old => old.id === item.id))] })); }}>묶음 더 불러오기</button>}
      <label htmlFor="set-name">새 묶음 이름</label><div className="v1-inline"><input id="set-name" value={setName} disabled={!canWrite || action.busy} onChange={event => setSetName(event.target.value)} /><button type="button" className="btn secondary" disabled={!setName.trim() || C.codepoints(setName) > 200 || !canWrite || action.busy} onClick={async () => { const value = await action.run(signal => api.createSet(pid, setName, signal)); if (value) { setSets(current => ({ ...current, items: [value, ...current.items] })); setSetId(value.id); setParams(current => { const next = new URLSearchParams(current); next.set('criteria_set_id', value.id); return next; }, { replace: true }); setSetName(''); } }}>묶음 만들기</button></div>
      <label htmlFor="requirements">요구사항 원문</label><textarea id="requirements" value={requirements} onChange={event => setRequirements(event.target.value)} disabled={!canWrite || action.busy} placeholder="요구사항을 한 줄에 하나씩 입력해 주세요." /><p className="help">{C.codepoints(requirements).toLocaleString()} / 20,000자 · 줄마다 초안 항목을 만듭니다.</p>
      {file && <p className="v1-notice" role="status">선택한 새 이미지를 먼저 등록해 주세요. 등록된 이미지를 선택하면 새 파일 미리보기는 취소됩니다.</p>}<ErrorMessage message={action.error} /><p className="help">통신 오류가 발생하면 초안 목록에서 저장 여부를 확인해 주세요. 자동으로 다시 저장하지 않습니다.</p><button className="btn primary" disabled={!canWrite || action.busy || !!file || !setId || !selectedImageReady}>{action.busy ? '저장 중…' : '초안 저장하고 검토'}</button>
    </form></div><DirtyDialog blocker={blocker} /></>;
}
export function DraftListPage() {
  const { projectId } = useParams(); const [params, setParams] = useSearchParams(); const mode = params.get('view') === 'criteria' ? 'criteria' : 'drafts'; const cursor = params.get('cursor');
  const state = useResource(`draft-list:${projectId}:${mode}:${cursor}`, async signal => { const [project, page] = await Promise.all([api.project(projectId, signal), mode === 'criteria' ? api.versions(projectId, { cursor }, signal) : api.drafts(projectId, { cursor }, signal)]); return { project, page }; });
  return <><Title title="저장된 초안과 기준">편집 중인 초안과 과거 확정본을 구분해 확인하세요.</Title><div className="v1-actions"><button className={`btn ${mode === 'drafts' ? 'primary' : 'secondary'}`} onClick={() => setParams({ view: 'drafts' })}>편집 초안</button><button className={`btn ${mode === 'criteria' ? 'primary' : 'secondary'}`} onClick={() => setParams({ view: 'criteria' })}>확정 기준</button></div><Resource state={state}>{({ project, page }) => <><p>{project.name} · 내 역할 {role(project.my_role)}</p><div className="v2-record-list">{page.items.map(value => <article className="form-card v2-record-row" key={value.id}><span className="pill">{mode === 'criteria' ? `기준 v${value.version_no}` : `초안 revision ${value.revision}`}</span><h2>{mode === 'criteria' ? '확정한 불변 기준' : value.raw_requirements.slice(0, 80)}</h2><Link className="btn secondary" to={`/projects/${projectId}/${mode === 'criteria' ? 'criteria' : 'drafts'}/${value.id}`}>{mode === 'criteria' ? '확정본 확인' : '초안 열기'}</Link></article>)}</div>{!page.items.length && <p className="form-card">저장된 자료가 없습니다.</p>}<Pager page={page} path={`/projects/${projectId}/drafts`} filters={{ view: mode }} /></>}</Resource></>;
}
