import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { post, projectUrl } from './api';
import { useAction, useApi, inputError } from './useApi';
import { useWorkspace } from './Projects';
import type { Asset, CriteriaGroup, CriteriaVersion, Criterion, CriterionKind, DocumentVersion } from './types';
import { dateTime, Empty, ErrorNotice, Field, FilePreview, IssueList, Loading, PanelHeading, ReadOnly, Status, submit, Success } from './ui';

type DraftCriterion = Criterion & { expectedInput: string; expectedFormat: 'text' | 'json' };
type DraftGroup = CriteriaGroup & { conditionInput: string; conditionFormat: 'text' | 'json'; exceptionInput: string; exceptionFormat: 'text' | 'json' };
const kindLabels: Record<CriterionKind, string> = { exact_text: '정확한 문구', date: '날짜', amount: '금액', quantity: '수량', logo: '로고', layout: '배치', color: '색상', unsupported: '검사 미지원' };
const toDraft = (item: Criterion): DraftCriterion => ({ ...item, asset_ids: item.asset_ids ?? [], notes: item.notes ?? '', source: item.source ?? { kind: 'user' }, expectedInput: typeof item.expected === 'string' ? item.expected : JSON.stringify(item.expected ?? null, null, 2), expectedFormat: typeof item.expected === 'string' ? 'text' : 'json' });
const toDraftGroup = (group: CriteriaGroup): DraftGroup => ({ ...group, criterion_ids: [...group.criterion_ids],
  conditionInput: typeof group.condition === 'string' ? group.condition : JSON.stringify(group.condition ?? null, null, 2), conditionFormat: typeof group.condition === 'string' ? 'text' : 'json',
  exceptionInput: typeof group.exception === 'string' ? group.exception : JSON.stringify(group.exception ?? null, null, 2), exceptionFormat: typeof group.exception === 'string' ? 'text' : 'json',
});

export function DocumentsCriteria() {
  const { project, canEdit, canApprove, refreshProject } = useWorkspace();
  const documents = useApi<DocumentVersion[]>(projectUrl(project.id, 'documents'), true);
  const versions = useApi<CriteriaVersion[]>(projectUrl(project.id, 'criteria'), true);
  const assets = useApi<Asset[]>(projectUrl(project.id, 'assets'), true);
  const upload = useAction(); const save = useAction(); const approval = useAction();
  const [file, setFile] = useState<File | null>(null); const [fileKey, setFileKey] = useState(0);
  const [documentId, setDocumentId] = useState(''); const [criteriaId, setCriteriaId] = useState('');
  const [items, setItems] = useState<DraftCriterion[]>([]); const [groups, setGroups] = useState<DraftGroup[]>([]);
  const [dirty, setDirty] = useState(false); const [sourceBlock, setSourceBlock] = useState<string | null>(null);
  const [density, setDensity] = useState('comfortable'); const [confirmed, setConfirmed] = useState(false);
  const orderedDocuments = [...(documents.data ?? [])].sort((a, b) => b.version - a.version);
  const document = orderedDocuments.find(item => item.id === documentId) ?? orderedDocuments.find(item => item.id === project.current_document_id) ?? orderedDocuments[0];
  const applicable = [...(versions.data ?? [])].filter(version => version.document_id === document?.id).sort((a, b) => b.version - a.version);
  const version = applicable.find(item => item.id === criteriaId) ?? applicable[0];
  useEffect(() => {
    if (dirty || documents.loading || versions.loading) return;
    setItems((version?.items ?? document?.criteria ?? []).map(toDraft));
    setGroups((version?.groups ?? document?.groups ?? []).map(toDraftGroup));
    setConfirmed(false);
  }, [document, version, dirty, documents.loading, versions.loading]);
  function patchItem(id: string, patch: Partial<DraftCriterion>) { setItems(current => current.map(item => item.id === id ? { ...item, ...patch } : item)); setDirty(true); setConfirmed(false); }
  function patchGroup(id: string, patch: Partial<DraftGroup>) { setGroups(current => current.map(group => group.id === id ? { ...group, ...patch } : group)); setDirty(true); setConfirmed(false); }
  function changeSelection(next: () => void) { if (dirty && !window.confirm('저장하지 않은 조건 편집을 버리고 선택을 변경할까요?')) return; setDirty(false); setSourceBlock(null); save.clear(); approval.clear(); next(); }
  function addItem() {
    setItems(current => [...current, toDraft({ id: crypto.randomUUID(), text: '', category: 'creative', required: true, kind: 'exact_text', expected: '', target: 'all', asset_ids: [], source: { kind: 'user', quote: '' }, supported: true, notes: '' })]);
    setDirty(true); setConfirmed(false);
  }
  async function saveVersion() {
    if (!document) return inputError('먼저 기준 문서를 등록해 주세요.');
    if (!items.length) return inputError('승인할 조건을 한 개 이상 입력해 주세요.');
    const payloadItems: Criterion[] = items.map(({ expectedInput, expectedFormat, ...item }, index) => {
      if (!item.text.trim()) return inputError(`${index + 1}번째 조건 내용을 입력해 주세요.`);
      let expected: Criterion['expected'] = expectedInput;
      if (expectedFormat === 'json') { try { expected = JSON.parse(expectedInput); } catch { return inputError(`${index + 1}번째 조건의 기대값 JSON을 확인해 주세요.`); } }
      return { ...item, text: item.text.trim(), expected };
    });
    for (const group of groups) {
      if (!group.criterion_ids.length) return inputError('관계 그룹마다 조건을 한 개 이상 선택해 주세요.');
      if (group.criterion_ids.some(id => !items.some(item => item.id === id))) return inputError('관계 그룹에 삭제된 조건이 포함되어 있습니다.');
    }
    const payloadGroups: CriteriaGroup[] = groups.map(({ conditionInput, conditionFormat, exceptionInput, exceptionFormat, ...group }, index) => {
      try { return { ...group, condition: conditionFormat === 'json' ? JSON.parse(conditionInput) : conditionInput, exception: exceptionFormat === 'json' ? JSON.parse(exceptionInput) : exceptionInput }; }
      catch { return inputError(`관계 그룹 ${index + 1}의 적용 조건·예외 JSON을 확인해 주세요.`); }
    });
    const created = await post<CriteriaVersion>(projectUrl(project.id, 'criteria'), { document_id: document.id, items: payloadItems, groups: payloadGroups });
    setDirty(false); setCriteriaId(created.id); versions.reload(); refreshProject();
  }
  return <><div className="page-heading"><div><h1>문서·조건</h1><p>원문 위치를 대조하고 조건과 관계를 편집한 뒤 기준 버전을 승인합니다.</p></div>
    <Link className="button" to={`../assets`}>산출물로 이동</Link></div>
    {!canEdit && <ReadOnly archived={project.archived} />}
    <section className="sheet"><PanelHeading title="기준 문서 등록" />
      {canEdit ? <form className="inline-form padded" onSubmit={submit(() => upload.run(async () => {
        if (!file) return inputError('문서를 선택해 주세요.');
        if (file.size > 20 * 1024 * 1024) return inputError('문서는 20MiB 이하여야 합니다.');
        const form = new FormData(); form.append('file', file);
        const created = await post<DocumentVersion>(projectUrl(project.id, 'documents'), form);
        changeSelection(() => { setDocumentId(created.id); setCriteriaId(''); }); setFile(null); setFileKey(value => value + 1); documents.reload(); versions.reload(); refreshProject();
      }, '문서를 등록했습니다. 추출 상태를 조회하고 조건을 확인해 주세요.'))}>
        <Field label="새 문서 버전" hint="PDF·DOCX·HWPX, 20MiB 이하. PDF 50페이지, 나머지 추출 10만 자까지."><input key={fileKey} type="file" required accept=".pdf,.docx,.hwpx" onChange={event => setFile(event.target.files?.[0] ?? null)} /></Field>
        <button className="button primary" disabled={upload.pending || !file}>{upload.pending ? '등록 중…' : '문서 등록'}</button>
      </form> : <p className="padded muted">원문과 승인된 조건을 아래에서 조회할 수 있습니다.</p>}
      <ErrorNotice error={upload.error} /><Success>{upload.success}</Success>
    </section>
    <div className="comparison-grid"><section className="sheet source-pane"><PanelHeading title="원문 대조"><button className="button small" onClick={() => { documents.reload(); versions.reload(); }}>추출 상태 조회</button></PanelHeading>
      <ErrorNotice error={documents.error} retry={documents.reload} />{documents.loading && <Loading />}
      {documents.data && !document && <Empty title="기준 문서를 등록해 주세요">문서가 등록되면 추출 원문과 조건 후보를 함께 확인할 수 있습니다.</Empty>}
      {document && <><div className="padded form-stack"><Field label="기준 문서 버전"><select value={document.id} onChange={event => changeSelection(() => { setDocumentId(event.target.value); setCriteriaId(''); })}>
        {orderedDocuments.map(item => <option value={item.id} key={item.id}>문서 v{item.version} · {item.filename}{project.current_document_id === item.id ? ' · 현재 문서' : ''}</option>)}
      </select></Field><div className="metadata-line"><Status value={document.status} /><span>{dateTime(document.created_at)}</span></div>
        <IssueList issues={document.errors} /><FilePreview fileId={document.file_id} kind="document" title={document.filename} />
      </div><div className="document-blocks"><h3>추출 원문</h3>{document.blocks?.length ? document.blocks.map(block => <article className={`source-block${sourceBlock === block.id ? ' selected' : ''}`} key={block.id} id={`block-${block.id}`} tabIndex={-1}>
        <span className="source-location">{block.page != null ? `${block.page}페이지` : '페이지 정보 없음'} · 원문 블록</span><p>{block.text}</p>
      </article>) : <Empty title="추출 원문이 아직 없습니다">문서 추출 상태와 오류를 확인하고 다시 조회해 주세요.</Empty>}</div></>}
    </section>
    <section className={`sheet criteria-pane density-${density}`}><PanelHeading title="검사 조건"><label className="density-control">표시 밀도<select value={density} onChange={event => setDensity(event.target.value)}><option value="comfortable">보통</option><option value="compact">촘촘하게</option></select></label></PanelHeading>
      <ErrorNotice error={versions.error} retry={versions.reload} />{versions.loading && <Loading />}
      {document && <><div className="padded form-stack"><Field label="조건 버전"><select value={version?.id ?? ''} disabled={!applicable.length} onChange={event => changeSelection(() => setCriteriaId(event.target.value))}>
        {!applicable.length && <option value="">새 조건 초안</option>}{applicable.map(item => <option value={item.id} key={item.id}>조건 v{item.version}{item.status === 'approved' ? ' · 승인됨' : ' · 초안'}{item.id === project.current_criteria_id ? ' · 현재 기준' : ''}</option>)}
      </select></Field><div className="metadata-line">{version ? <><Status value={version.status} />{version.approved_at && <span>승인 {dateTime(version.approved_at)}</span>}</> : <span>추출 후보 또는 직접 입력한 조건을 새 버전으로 저장해 주세요.</span>}{dirty && <strong className="unsaved">저장하지 않은 편집</strong>}</div>
        <p className="hint">새 기준 버전은 승인을 새로 받아야 합니다. 검사 미지원·불확실한 조건과 미확정 관계는 확인 대상으로 남습니다.</p>
      </div>
      <ErrorNotice error={assets.error} retry={assets.reload} />
      <div className="criteria-list">{items.length ? items.map((item, index) => <article className="criterion-editor" key={item.id}>
        <div className="criterion-title"><h3>조건 {index + 1}</h3><div className="actions"><span className="status neutral">{item.required ? '필수' : '권장'}</span>{!item.supported && <Status value="unsupported" />}
          {canEdit && <button className="text-button danger-text" onClick={() => { setItems(current => current.filter(value => value.id !== item.id)); setGroups(current => current.map(group => ({ ...group, criterion_ids: group.criterion_ids.filter(id => id !== item.id) }))); setDirty(true); setConfirmed(false); }}>조건 제거</button>}</div></div>
        <Field label="조건 내용"><textarea rows={2} value={item.text} disabled={!canEdit} onChange={event => patchItem(item.id, { text: event.target.value })} /></Field>
        <div className="form-grid three"><Field label="업무 구분"><select value={item.category} disabled={!canEdit} onChange={event => patchItem(item.id, { category: event.target.value as Criterion['category'] })}><option value="creative">크리에이티브</option><option value="business">업무</option><option value="other">기타</option></select></Field>
          <Field label="검사 유형"><select value={item.kind} disabled={!canEdit} onChange={event => patchItem(item.id, { kind: event.target.value as CriterionKind, supported: event.target.value === 'unsupported' ? false : item.supported })}>{Object.entries(kindLabels).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></Field>
          <Field label="적용 매체"><select value={item.target} disabled={!canEdit} onChange={event => patchItem(item.id, { target: event.target.value as Criterion['target'] })}><option value="all">전체</option><option value="poster">포스터</option><option value="video">영상</option></select></Field></div>
        <div className="expected-editor"><Field label="기대값"><textarea rows={2} disabled={!canEdit} value={item.expectedInput} onChange={event => patchItem(item.id, { expectedInput: event.target.value })} /></Field><Field label="기대값 형식"><select value={item.expectedFormat} disabled={!canEdit} onChange={event => patchItem(item.id, { expectedFormat: event.target.value as DraftCriterion['expectedFormat'] })}><option value="text">문자열</option><option value="json">구조화 JSON</option></select></Field></div>
        <div className="form-grid"><Field label="특정 산출물 지정" hint="선택하지 않으면 적용 매체 전체에 해당합니다."><select multiple value={item.asset_ids} disabled={!canEdit} onChange={event => patchItem(item.id, { asset_ids: Array.from(event.target.selectedOptions, option => option.value) })}>
          {(assets.data ?? []).map(asset => <option key={asset.id} value={asset.id}>{asset.name}</option>)}{item.asset_ids.filter(id => !assets.data?.some(asset => asset.id === id)).map(id => <option key={id} value={id}>산출물 {id}</option>)}
        </select></Field><Field label="검사 참고·제한"><textarea rows={2} disabled={!canEdit} value={item.notes} onChange={event => patchItem(item.id, { notes: event.target.value })} /></Field></div>
        <label className="check"><input type="checkbox" checked={item.required} disabled={!canEdit} onChange={event => patchItem(item.id, { required: event.target.checked })} />필수 조건</label>
        <div className="criterion-source"><strong>{item.source.kind === 'document' ? '문서 근거' : '사용자 추가 조건'}</strong>{item.source.page != null && <span> · {item.source.page}페이지</span>}{item.source.quote && <blockquote>{item.source.quote}</blockquote>}
          {item.source.block_id && <button className="text-button" onClick={() => { setSourceBlock(item.source.block_id ?? null); window.setTimeout(() => window.document.getElementById(`block-${item.source.block_id}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 0); }}>원문 위치 보기</button>}</div>
      </article>) : <Empty title="조건 후보가 없습니다">추출이 완료되지 않았거나 후보가 없습니다. 원문을 대조해 조건을 직접 추가할 수 있습니다.</Empty>}</div>
      {canEdit && <div className="padded"><button className="button" disabled={items.length >= 128} onClick={addItem}>조건 추가</button>{items.length >= 128 && <p className="hint">조건은 기준 버전당 최대 128개까지 등록할 수 있습니다.</p>}</div>}
      <div className="groups-section"><PanelHeading title="조건 관계 그룹">{canEdit && <button className="button small" disabled={groups.length >= 128} onClick={() => { setGroups(current => [...current, toDraftGroup({ id: crypto.randomUUID(), operator: 'all', criterion_ids: [], condition: '', exception: '' })]); setDirty(true); setConfirmed(false); }}>그룹 추가</button>}</PanelHeading>
        <p className="hint">모두 충족(AND)·하나 이상(OR), 적용 조건, 예외를 구분해 기록합니다. 미확정 조건·예외는 임의로 충족 처리하지 않습니다.</p>
        {!groups.length && <Empty title="등록된 관계 그룹이 없습니다">각 조건의 적용 관계를 지정하려면 그룹을 추가해 주세요.</Empty>}
        {groups.map((group, index) => <div className="group-editor" key={group.id}><div className="criterion-title"><h3>관계 그룹 {index + 1}</h3>{canEdit && <button className="text-button danger-text" onClick={() => { setGroups(current => current.filter(item => item.id !== group.id)); setDirty(true); setConfirmed(false); }}>그룹 제거</button>}</div>
          <Field label="충족 관계"><select value={group.operator} disabled={!canEdit} onChange={event => patchGroup(group.id, { operator: event.target.value as CriteriaGroup['operator'] })}><option value="all">모두 충족 (AND)</option><option value="any">하나 이상 충족 (OR)</option></select></Field>
          <fieldset className="group-members"><legend>그룹에 포함할 조건</legend>{items.map((item, itemIndex) => <label className="check" key={item.id}><input type="checkbox" disabled={!canEdit} checked={group.criterion_ids.includes(item.id)} onChange={event => patchGroup(group.id, { criterion_ids: event.target.checked ? [...group.criterion_ids, item.id] : group.criterion_ids.filter(id => id !== item.id) })} />조건 {itemIndex + 1}: {item.text || '내용 입력 전'}</label>)}</fieldset>
          <div className="expected-editor"><Field label="적용 조건"><textarea rows={2} disabled={!canEdit} value={group.conditionInput} onChange={event => patchGroup(group.id, { conditionInput: event.target.value })} /></Field><Field label="적용 조건 형식"><select value={group.conditionFormat} disabled={!canEdit} onChange={event => patchGroup(group.id, { conditionFormat: event.target.value as DraftGroup['conditionFormat'] })}><option value="text">문자열</option><option value="json">구조화 JSON</option></select></Field></div>
          <div className="expected-editor"><Field label="예외"><textarea rows={2} disabled={!canEdit} value={group.exceptionInput} onChange={event => patchGroup(group.id, { exceptionInput: event.target.value })} /></Field><Field label="예외 형식"><select value={group.exceptionFormat} disabled={!canEdit} onChange={event => patchGroup(group.id, { exceptionFormat: event.target.value as DraftGroup['exceptionFormat'] })}><option value="text">문자열</option><option value="json">구조화 JSON</option></select></Field></div>
        </div>)}
      </div>
      <div className="approval-strip"><ErrorNotice error={save.error} /><Success>{save.success}</Success><ErrorNotice error={approval.error} /><Success>{approval.success}</Success>
        {canEdit && <button className="button primary" disabled={save.pending || !items.length || !!versions.error} onClick={() => void save.run(saveVersion, '새 조건 버전을 저장했습니다. 내용을 확인한 뒤 승인해 주세요.')}>{save.pending ? '저장 중…' : '새 조건 버전 저장'}</button>}
        {canApprove && version?.status !== 'approved' && version && <div className="approval-confirm"><label className="check"><input type="checkbox" checked={confirmed} disabled={dirty} onChange={event => setConfirmed(event.target.checked)} />저장된 조건과 관계 그룹을 원문과 대조했습니다.</label>
          <button className="button primary" disabled={approval.pending || dirty || !confirmed} onClick={() => void approval.run(async () => { await post(`/criteria/${encodeURIComponent(version.id)}/approve`); versions.reload(); refreshProject(); }, '조건 기준을 승인했습니다.')}>조건 v{version.version} 승인</button></div>}
        {version?.status === 'approved' && <p className="hint">승인된 버전입니다. 편집한 내용은 새 버전으로 저장하고 다시 승인해 주세요.</p>}
        {!canApprove && canEdit && <p className="hint">조건 승인은 프로젝트 승인자가 수행합니다.</p>}
      </div></>}
    </section></div></>;
}
