import React, { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import * as api from './api.js';
import * as C from './contracts.js';
import { useAction, useDirty, usePending, useResource } from './hooks.jsx';
import { DirtyDialog, PendingPanel, Resource, Title } from './components.jsx';
import { ErrorMessage } from '../components/ErrorMessage.jsx';
import ImageStage from '../components/ImageStage.jsx';

export function DraftPage() {
  const { projectId, draftId } = useParams();
  const state = useResource(`draft:${projectId}:${draftId}`, async signal => {
    const [project, draft] = await Promise.all([api.project(projectId, signal), api.draft(projectId, draftId, signal)]);
    return { project, draft, image: await api.image(draft.image_id, projectId, signal) };
  });
  return <Resource state={state}>{value => <DraftEditor key={value.draft.id} {...value} />}</Resource>;
}
function DraftEditor({ project, draft, image }) {
  const pid = project.id; const navigate = useNavigate();
  const [base, setBase] = useState(draft); const [content, setContent] = useState(() => C.content(draft)); const [latest, setLatest] = useState(null); const [notice, setNotice] = useState('');
  const action = useAction(`draft:${draft.id}`);
  const pending = usePending(pid, (value, entry) => navigate(`/projects/${pid}/${entry.operation === 'confirm_criteria' ? 'criteria' : 'reviews'}/${value.id}`));
  const dirty = JSON.stringify(content) !== JSON.stringify(C.content(base)); const blocker = useDirty(dirty);
  const writable = C.editable(project) && base.state === 'editing'; const busy = action.busy || pending.busy;
  function change(index, patch) { setContent(current => ({ ...current, items: current.items.map((row, i) => i === index ? { ...row, ...patch } : row) })); setNotice(''); }
  async function save(event) {
    event.preventDefault(); const error = C.contentError(content); if (error) return action.setError(error);
    setNotice(''); setLatest(null);
    const outcome = await action.run(async signal => {
      try { return { saved: await api.putDraft(pid, draft.id, { ...content, expected_revision: base.revision }, signal) }; }
      catch (failure) {
        if (failure.code !== 'DRAFT_REVISION_CONFLICT') throw failure;
        // The old editing buffer remains intact; GET does not authorize an overwrite.
        return { conflict: await api.draft(pid, draft.id, signal) };
      }
    });
    if (outcome?.saved) { setBase(outcome.saved); setContent(C.content(outcome.saved)); setNotice('서버에 저장했습니다.'); }
    if (outcome?.conflict) setLatest(outcome.conflict);
  }
  return <><Title title="검수 기준 초안을 검토하세요.">저장한 항목을 선택하고 기준을 확정하세요. 확정본은 별도로 보존됩니다.</Title>
    <div className="v1-toolbar"><span className="pill">서버 revision {base.revision}</span><span role="status">{dirty ? '저장하지 않은 편집' : notice || '서버 저장본과 동일'}</span><Link className="text-btn" to={`/projects/${pid}/drafts`}>초안·확정본 목록</Link></div>
    {!writable && <p className="v1-notice">현재 역할 또는 보관 상태에서는 편집할 수 없습니다.</p>}
    <div className="v2-preparation-grid"><section className="v2-image-column"><ImageStage src={image.url} alt="초안에 연결된 이미지" title="기준을 검토할 이미지" subtitle={`${project.name} · ${image.width}×${image.height}`} width={image.width} height={image.height} /><aside className="v2-confirm-panel"><h2>기준 확정</h2><p>선택 항목 {content.items.filter(row => row.selected).length}개</p><p className="help">미저장 편집은 확정에 포함되지 않습니다. 먼저 변경 내용을 저장해 주세요.</p>
      <button className="btn primary" disabled={!writable || busy || dirty || !!C.contentError(content, true) || !!latest} onClick={() => pending.submit('confirm_criteria', { draft_id: base.id, expected_revision: base.revision })}>저장된 기준 확정</button>
      <Link className="text-btn" to={`/projects/${pid}/drafts?view=criteria`}>과거 확정본 보기</Link>
    </aside></section><form className="form-card v2-editor-panel" onSubmit={save}><h2>요구사항과 항목</h2><label htmlFor="draft-raw">요구사항 원문</label><textarea id="draft-raw" value={content.raw_requirements} disabled={!writable || busy} onChange={event => setContent(current => ({ ...current, raw_requirements: event.target.value }))} />
      <p className="help">원문을 바꿔도 아래 검수 항목은 자동으로 덮어쓰지 않습니다. 항목도 직접 검토해 주세요.</p>
      {content.items.map((row, index) => <fieldset className="v1-item-editor" key={row.id} disabled={!writable || busy}><legend>항목 {index + 1}</legend>
        <label className="v1-check"><input type="checkbox" checked={row.selected} onChange={event => change(index, { selected: event.target.checked })} />확정할 항목에 포함</label>
        <label htmlFor={`item-text-${index}`}>검수 문구</label><textarea id={`item-text-${index}`} value={row.text} onChange={event => change(index, { text: event.target.value })} />
        <label htmlFor={`item-type-${index}`}>항목 유형</label><select id={`item-type-${index}`} value={row.type} onChange={event => change(index, { type: event.target.value })}><option value="information">정보 확인</option><option value="visual">시각 확인</option></select>
        <label htmlFor={`item-source-${index}`}>출처 문구</label><input id={`item-source-${index}`} value={row.source} onChange={event => change(index, { source: event.target.value })} />
        <button type="button" className="text-btn" onClick={() => setContent(current => ({ ...current, items: current.items.filter((_, i) => i !== index) }))}>항목 삭제</button>
      </fieldset>)}
      <button type="button" className="btn secondary" disabled={!writable || busy || content.items.length >= 100} onClick={() => setContent(current => ({ ...current, items: [...current.items, { id: crypto.randomUUID(), text: '', type: 'information', source: '사용자 입력', selected: true }] }))}>항목 추가</button>
      <p className="help">출처 파일 메타데이터: {content.source_documents === null ? '미등록' : `${content.source_documents.length}개`}. 저장된 원본 정보를 유지합니다.</p><ErrorMessage message={action.error} />
      <div className="v1-actions"><button className="btn primary" disabled={!writable || busy || !dirty}>변경 내용 저장</button><button type="button" className="btn secondary" disabled={busy || !dirty} onClick={() => { setContent(C.content(base)); setLatest(null); setNotice('서버 저장본으로 되돌렸습니다.'); }}>편집 취소</button></div>
      {latest && <section className="v1-notice" aria-label="초안 충돌"><h3>서버 초안이 바뀌었습니다.</h3><p>내 편집은 위에 그대로 남아 있습니다. revision {latest.revision}의 최신 내용을 확인해 주세요.</p><pre>{latest.raw_requirements}</pre><ul>{latest.items.map(row => <li key={row.id}>{row.selected ? '선택' : '제외'} · {row.text} · {row.type} · 출처: {row.source}</li>)}</ul><button type="button" className="btn secondary" onClick={() => { setBase(latest); setContent(C.content(latest)); setLatest(null); setNotice('최신 서버 초안을 불러왔습니다.'); }}>내 편집을 버리고 최신본 불러오기</button></section>}
    </form></div><PendingPanel mutation={pending} /><DirtyDialog blocker={blocker} /></>;
}
export function CriteriaPage() {
  const { projectId, versionId } = useParams(); const navigate = useNavigate();
  const state = useResource(`criteria:${projectId}:${versionId}`, async signal => { const [project, criteria] = await Promise.all([api.project(projectId, signal), api.criteria(projectId, versionId, signal)]); return { project, criteria, image: await api.image(criteria.image_id, projectId, signal) }; });
  const pending = usePending(projectId, (value, entry) => navigate(`/projects/${projectId}/${entry.operation === 'confirm_criteria' ? 'criteria' : 'reviews'}/${value.id}`));
  return <><Title title="확정한 검수 기준입니다.">이 버전은 읽기 전용입니다. 수정하려면 연결된 초안에서 작업하세요.</Title><Resource state={state}>{({ project, criteria, image }) => <>
    <div className="v1-toolbar"><span className="pill">서버 기준 v{criteria.version_no}</span><span>{criteria.confirmation_state === 'confirmed' ? '확정됨' : '확정되지 않음'} · {criteria.draft_is_current ? '현재 초안과 동일' : '현재 초안과 다르거나 출처 확인 필요'}</span></div>
    <div className="v2-preparation-grid"><section className="v2-image-column"><ImageStage src={image.url} alt="확정 기준에 연결된 이미지" title="검수 대상 이미지" subtitle={`${project.name} · ${image.width}×${image.height}`} width={image.width} height={image.height} /><div className="v2-image-note"><span>01 / 검수 이미지</span><p>확정 기준과 이 이미지를 함께 보며 마지막으로 확인해 주세요.</p></div></section><section className="form-card v2-editor-panel"><h2>확정 항목 {criteria.criteria.length}개</h2><ol className="v1-criteria">{criteria.criteria.map(row => <li key={row.criteria_item_id}><strong>{row.text}</strong><p className="help">{row.type === 'visual' ? '시각 확인' : '정보 확인'} · {row.source ?? '출처 미등록'}</p></li>)}</ol><p className="help">출처 초안 revision: {criteria.source_draft_revision ?? '미등록'}</p>
      {criteria.source_draft_id && <Link className="btn secondary" to={`/projects/${projectId}/drafts/${criteria.source_draft_id}`}>현재 초안 열기</Link>}<div className="v2-confirm-panel"><p className="help">이 이미지와 기준 버전으로 검수를 접수합니다.</p><button className="btn primary" disabled={!C.editable(project) || pending.busy || criteria.confirmation_state !== 'confirmed' || criteria.origin_kind !== 'service_confirmation' || !criteria.draft_is_current} onClick={() => pending.submit('create_review', { project_id: projectId, image_id: criteria.image_id, criteria_version_id: criteria.id })}>이 기준으로 검수 시작</button>
      {!criteria.draft_is_current && <p className="v1-notice">현재 초안을 다시 확인하고 확정해 주세요. 과거 기준은 이력으로 보존됩니다.</p>}<Link className="text-btn" to={`/projects/${projectId}/reviews`}>검수 이력 확인</Link></div></section></div>
    </>}</Resource><PendingPanel mutation={pending} /></>;
}
