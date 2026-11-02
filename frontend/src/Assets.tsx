import { useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { post, projectUrl } from './api';
import { useAction, useApi, inputError } from './useApi';
import { useWorkspace } from './Projects';
import type { Asset, AssetVersion, ChangeRequest, Logo } from './types';
import { dateTime, Empty, ErrorNotice, Field, FilePreview, JsonView, Loading, PanelHeading, ReadOnly, Status, submit, Success } from './ui';

function validateFile(file: File | null, kind: 'poster' | 'video'): File {
  if (!file) return inputError('파일을 선택해 주세요.');
  if (file.size > (kind === 'poster' ? 20 : 100) * 1024 * 1024) return inputError(kind === 'poster' ? '이미지는 20MiB 이하여야 합니다.' : '영상은 100MiB 이하여야 합니다.');
  return file;
}
export function Assets() {
  const { project, canEdit } = useWorkspace(); const [params] = useSearchParams();
  const assets = useApi<Asset[]>(projectUrl(project.id, 'assets'), true); const upload = useAction();
  const [name, setName] = useState(''); const [kind, setKind] = useState<'poster' | 'video'>('poster'); const [file, setFile] = useState<File | null>(null); const [fileKey, setFileKey] = useState(0);
  const [selected, setSelected] = useState(params.get('asset_id') ?? ''); const asset = assets.data?.find(item => item.id === selected) ?? assets.data?.[0];
  return <><div className="page-heading"><div><h1>산출물</h1><p>포스터와 영상을 등록하고 수정본을 새 버전으로 이어갑니다.</p></div><Link className="button" to="../runs">검사 실행으로 이동</Link></div>
    {!canEdit && <ReadOnly archived={project.archived} />}
    {canEdit && <section className="sheet"><PanelHeading title="새 산출물 등록" /><form className="form-stack padded" onSubmit={submit(() => upload.run(async () => {
      const chosen = validateFile(file, kind); const form = new FormData(); form.append('file', chosen); form.append('name', name.trim()); form.append('kind', kind);
      const created = await post<Asset>(projectUrl(project.id, 'assets'), form); setSelected(created.id); setName(''); setFile(null); setFileKey(value => value + 1); assets.reload();
    }, '산출물을 등록했습니다. 승인된 기준으로 검사를 실행해 주세요.'))}>
      <div className="form-grid"><Field label="산출물 이름"><input required value={name} onChange={event => setName(event.target.value)} /></Field><Field label="매체"><select value={kind} onChange={event => { setKind(event.target.value as 'poster' | 'video'); setFile(null); setFileKey(value => value + 1); }}><option value="poster">포스터</option><option value="video">영상</option></select></Field></div>
      <Field label="산출물 파일" hint={kind === 'poster' ? 'PNG·JPEG·WEBP, 20MiB·1,000만 픽셀 이하.' : 'MP4·WebM, 60초·100MiB·1080p 수준. 음성은 검사하지 않습니다.'}><input key={fileKey} type="file" required accept={kind === 'poster' ? '.png,.jpg,.jpeg,.webp' : '.mp4,.webm'} onChange={event => setFile(event.target.files?.[0] ?? null)} /></Field>
      <ErrorNotice error={upload.error} /><Success>{upload.success}</Success><div><button className="button primary" disabled={upload.pending || !file}>{upload.pending ? '업로드 중…' : '산출물 등록'}</button></div>
    </form></section>}
    <section className="sheet"><PanelHeading title="등록된 산출물"><button className="button small" onClick={assets.reload}>새로 조회</button></PanelHeading>
      <ErrorNotice error={assets.error} retry={assets.reload} />{assets.loading && <Loading />}
      {assets.data && (assets.data.length ? <div className="table-scroll"><table><thead><tr><th>산출물</th><th>매체</th><th>현재 버전</th><th>재검수</th></tr></thead><tbody>{assets.data.map(item => <tr key={item.id} className={asset?.id === item.id ? 'selected-row' : ''}>
        <td><button className="table-button" onClick={() => setSelected(item.id)} aria-pressed={asset?.id === item.id}>{item.name}</button></td><td><Status value={item.kind} /></td><td>{item.versions.length ? `v${Math.max(...item.versions.map(version => version.version))}` : '버전 없음'}</td><td>{item.needs_review ? <Status value="review" /> : '재검수 요청 없음'}</td>
      </tr>)}</tbody></table></div> : <Empty title="등록된 산출물이 없습니다">포스터 또는 영상 파일을 등록하면 버전별로 검사할 수 있습니다.</Empty>)}
    </section>
    {asset && <AssetDetail key={asset.id} asset={asset} refresh={assets.reload} requestId={params.get('change_request_id') ?? ''} requestRunId={params.get('run_id') ?? ''} />}
  </>;
}
function AssetDetail({ asset, refresh, requestId, requestRunId }: { asset: Asset; refresh: () => void; requestId: string; requestRunId: string }) {
  const { canEdit } = useWorkspace(); const action = useAction(); const [versionId, setVersionId] = useState('');
  const versions = [...asset.versions].sort((a, b) => b.version - a.version); const version = versions.find(item => item.id === versionId) ?? versions[0];
  const [file, setFile] = useState<File | null>(null); const [key, setKey] = useState(0); const [changeRequest, setChangeRequest] = useState(requestId);
  const requests = useApi<ChangeRequest[]>(requestRunId ? `/runs/${encodeURIComponent(requestRunId)}/change-requests` : null, true);
  return <section className="sheet"><PanelHeading title={asset.name}><Link className="button small" to={`../runs?asset_version_id=${encodeURIComponent(version?.id ?? '')}`}>이 버전 검사</Link></PanelHeading>
    <div className="asset-detail-grid"><div className="padded form-stack"><Field label="조회할 산출물 버전"><select value={version?.id ?? ''} onChange={event => setVersionId(event.target.value)} disabled={!versions.length}>{versions.map(item => <option key={item.id} value={item.id}>v{item.version} · {item.filename}</option>)}</select></Field>
      {version ? <><div className="metadata-line"><span>등록 {dateTime(version.created_at)}</span></div><FilePreview fileId={version.file_id} kind={asset.kind} title={version.filename} />
        <details><summary>파일 정보</summary><JsonView value={version.metadata} /></details></> : <Empty title="파일 버전이 없습니다" />}
    </div><div className="padded version-pane"><h3>버전 이력</h3><ol className="version-list">{versions.map(item => <li key={item.id}><button className="text-button" onClick={() => setVersionId(item.id)}>v{item.version} · {item.filename}</button><small>{dateTime(item.created_at)}</small>{item.change_request_id && <small>수정 요청 연결: {item.change_request_id}</small>}</li>)}</ol>
      {canEdit && <form className="form-stack" onSubmit={submit(() => action.run(async () => {
        const chosen = validateFile(file, asset.kind); const form = new FormData(); form.append('file', chosen); if (changeRequest.trim()) form.append('change_request_id', changeRequest.trim());
        const created = await post<AssetVersion>(`/assets/${encodeURIComponent(asset.id)}/versions`, form); setVersionId(created.id); setFile(null); setKey(value => value + 1); refresh();
      }, '새 산출물 버전을 등록했습니다. 기존 승인은 승계되지 않으며 전체 재검수가 필요합니다.'))}>
        <h3>수정 버전 업로드</h3><p className="hint">수정 요청이 있다면 연결해 주세요. 이전 파일과 승인은 그대로 기록됩니다.</p>
        <ErrorNotice error={requests.error} retry={requests.reload} />
        {requests.data ? <Field label="연결할 승인된 수정 요청"><select value={changeRequest} onChange={event => setChangeRequest(event.target.value)}><option value="">연결하지 않음</option>{requests.data.filter(item => item.status === 'approved').map(item => <option value={item.id} key={item.id}>{item.message || item.id}</option>)}</select></Field>
          : <Field label="수정 요청 ID (선택)"><input value={changeRequest} onChange={event => setChangeRequest(event.target.value)} /></Field>}
        <Field label="새 버전 파일"><input key={key} type="file" required accept={asset.kind === 'poster' ? '.png,.jpg,.jpeg,.webp' : '.mp4,.webm'} onChange={event => setFile(event.target.files?.[0] ?? null)} /></Field>
        <ErrorNotice error={action.error} /><Success>{action.success}</Success><button className="button primary" disabled={action.pending || !file}>{action.pending ? '업로드 중…' : '새 버전 등록'}</button>
      </form>}
    </div></div></section>;
}

export function Logos() {
  const { project, canEdit, canApprove } = useWorkspace(); const logos = useApi<Logo[]>(projectUrl(project.id, 'logos'), true); const upload = useAction(); const approval = useAction();
  const [name, setName] = useState(''); const [file, setFile] = useState<File | null>(null); const [key, setKey] = useState(0); const [selected, setSelected] = useState(''); const [confirmed, setConfirmed] = useState(false);
  const logo = logos.data?.find(item => item.id === selected) ?? logos.data?.[0];
  return <><div className="page-heading"><div><h1>기준 로고</h1><p>승인된 로고만 검사 기준에 고정됩니다. 기준이 없으면 로고 조건은 확인이 필요합니다.</p></div></div>
    {!canEdit && <ReadOnly archived={project.archived} />}
    {canEdit && <section className="sheet"><PanelHeading title="로고 등록" /><form className="inline-form padded" onSubmit={submit(() => upload.run(async () => {
      const chosen = validateFile(file, 'poster'); const form = new FormData(); form.append('file', chosen); form.append('name', name.trim());
      const created = await post<Logo>(projectUrl(project.id, 'logos'), form); setSelected(created.id); setConfirmed(false); setName(''); setFile(null); setKey(value => value + 1); logos.reload();
    }, '로고를 등록했습니다. 승인자가 기준 로고를 확인하고 승인해 주세요.'))}>
      <Field label="로고 이름"><input required value={name} onChange={event => setName(event.target.value)} /></Field>
      <Field label="로고 이미지" hint="PNG·JPEG·WEBP, 20MiB·1,000만 픽셀 이하."><input key={key} type="file" required accept=".png,.jpg,.jpeg,.webp" onChange={event => setFile(event.target.files?.[0] ?? null)} /></Field>
      <button className="button primary" disabled={upload.pending || !file}>{upload.pending ? '등록 중…' : '로고 등록'}</button>
    </form><ErrorNotice error={upload.error} /><Success>{upload.success}</Success></section>}
    <section className="sheet"><PanelHeading title="로고 목록"><button className="button small" onClick={logos.reload}>새로 조회</button></PanelHeading><ErrorNotice error={logos.error} retry={logos.reload} />{logos.loading && <Loading />}
      {logos.data && (logos.data.length ? <div className="table-scroll"><table><thead><tr><th>로고</th><th>버전</th><th>승인 상태</th><th>승인일</th></tr></thead><tbody>{logos.data.map(item => <tr key={item.id} className={logo?.id === item.id ? 'selected-row' : ''}>
        <td><button className="table-button" onClick={() => { setSelected(item.id); setConfirmed(false); approval.clear(); }}>{item.name}</button></td><td>{item.version != null ? `v${item.version}` : '기록 없음'}</td><td><Status value={item.status} /></td><td>{dateTime(item.approved_at)}</td>
      </tr>)}</tbody></table></div> : <Empty title="승인할 기준 로고가 없습니다">로고 조건을 검사하려면 원본 로고를 등록한 뒤 승인해 주세요.</Empty>)}
    </section>
    {logo && <section className="sheet"><PanelHeading title={logo.name} /><div className="logo-review padded"><FilePreview fileId={logo.file_id} kind="image" title={logo.filename ?? logo.name} /><div className="form-stack"><h3>기준 로고 승인</h3><Status value={logo.status} />
      {logo.status === 'approved' ? <p>승인된 기준 로고입니다. 새 로고를 등록하면 다시 승인이 필요합니다.</p> : canApprove ? <><label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />검사에 사용할 기준 로고가 맞는지 확인했습니다.</label>
        <button className="button primary" disabled={!confirmed || approval.pending} onClick={() => void approval.run(async () => { await post(`/logos/${encodeURIComponent(logo.id)}/approve`); setConfirmed(false); logos.reload(); }, '기준 로고를 승인했습니다.')}>기준 로고 승인</button></> : <p className="muted">프로젝트 승인자가 로고를 승인할 수 있습니다.</p>}
      <ErrorNotice error={approval.error} /><Success>{approval.success}</Success>
    </div></div></section>}
  </>;
}
