import { useState } from 'react';
import { Link, NavLink, Outlet, useNavigate, useOutletContext, useParams } from 'react-router';
import { post, projectUrl, put } from './api';
import { useApi, useAction } from './useApi';
import { useAuth } from './auth';
import type { Member, Project, Role } from './types';
import { dateTime, Empty, ErrorNotice, Field, Loading, PanelHeading, ReadOnly, Status, submit, Success } from './ui';

export function ProjectList() {
  const query = useApi<Project[]>('/projects', true); const action = useAction(); const navigate = useNavigate();
  const [search, setSearch] = useState(''); const [showArchived, setShowArchived] = useState(false); const [creating, setCreating] = useState(false);
  const [name, setName] = useState(''); const [description, setDescription] = useState('');
  const projects = (query.data ?? []).filter(project => (showArchived || !project.archived) && `${project.name} ${project.description}`.toLowerCase().includes(search.toLowerCase()));
  return <main id="main" className="page project-list"><div className="page-heading"><div><h1>프로젝트</h1><p>문서 기준과 산출물 버전을 모아 검수와 승인을 이어갑니다.</p></div>
    <button className="button primary" onClick={() => setCreating(value => !value)} aria-expanded={creating}>{creating ? '등록 닫기' : '프로젝트 만들기'}</button></div>
    {creating && <section className="sheet"><PanelHeading title="새 프로젝트" /><form className="form-stack padded" onSubmit={submit(() => action.run(async () => {
      const project = await post<Project>('/projects', { name: name.trim(), description: description.trim() }); navigate(`/projects/${project.id}/documents`);
    }))}><Field label="프로젝트 이름"><input required value={name} onChange={event => setName(event.target.value)} /></Field>
      <Field label="업무 설명"><textarea rows={3} value={description} onChange={event => setDescription(event.target.value)} /></Field>
      <ErrorNotice error={action.error} /><div><button className="button primary" disabled={action.pending}>{action.pending ? '만드는 중…' : '만들고 문서 등록하기'}</button></div>
    </form></section>}
    <section className="sheet"><div className="toolbar"><label className="search-field"><span className="sr-only">프로젝트 검색</span><input type="search" placeholder="프로젝트 이름·설명 검색" value={search} onChange={event => setSearch(event.target.value)} /></label>
      <label className="check"><input type="checkbox" checked={showArchived} onChange={event => setShowArchived(event.target.checked)} />보관 포함</label><button className="button small" onClick={query.reload}>새로 조회</button></div>
      <ErrorNotice error={query.error} retry={query.reload} />{query.loading && <Loading />}
      {query.data && (projects.length ? <div className="table-scroll"><table><thead><tr><th>프로젝트</th><th>내 권한</th><th>상태</th><th>등록일</th></tr></thead><tbody>
        {projects.map(project => <tr key={project.id}><td><Link className="table-link" to={`/projects/${project.id}/documents`}>{project.name}</Link><p className="cell-description">{project.description}</p></td>
          <td><Status value={project.role} /></td><td>{project.archived ? '보관됨' : '진행 중'}</td><td className="nowrap">{dateTime(project.created_at)}</td></tr>)}
      </tbody></table></div> : <Empty title={search || showArchived ? '조회 조건에 맞는 프로젝트가 없습니다' : '아직 참여한 프로젝트가 없습니다'}>새 프로젝트를 만들거나 승인자에게 멤버 등록을 요청해 주세요.</Empty>)}
    </section></main>;
}

interface WorkspaceContext { project: Project; refreshProject: () => void; canEdit: boolean; canApprove: boolean }
export const useWorkspace = () => useOutletContext<WorkspaceContext>();
export function Workspace() {
  const { projectId = '' } = useParams(); const query = useApi<Project>(projectUrl(projectId));
  if (query.loading && !query.data) return <main id="main" className="page"><Loading label="프로젝트 권한과 상태를 확인하고 있습니다." /></main>;
  if (query.error) return <main id="main" className="page"><Link className="text-link" to="/projects">프로젝트 목록</Link><ErrorNotice error={query.error} retry={query.reload} /></main>;
  if (!query.data) return null;
  const project = query.data;
  const links = [['documents', '문서·조건'], ['assets', '산출물'], ['logos', '기준 로고'], ['runs', '검사 실행'], ['approvals', '수정·승인'], ['relations', '관계'], ['history', '이력'], ['settings', '프로젝트 설정']];
  return <div className="workspace"><aside className="workspace-sidebar" aria-label="프로젝트 탐색"><Link className="back-link" to="/projects">← 프로젝트 목록</Link>
    <h2>{project.name}</h2><div className="role-line"><Status value={project.role} />{project.archived && <span className="status neutral">보관됨</span>}</div>
    <nav aria-label="프로젝트 업무">{links.map(([path, label]) => <NavLink key={path} to={`/projects/${projectId}/${path}`}>{label}</NavLink>)}</nav>
    <p className="sidebar-note">사람의 승인과 검사 판정은 별도로 기록됩니다.</p></aside>
    <main id="main" className="workspace-main"><div className="project-context"><span>{project.name}</span>{project.description && <p>{project.description}</p>}</div>
      <Outlet key={projectId} context={{ project, refreshProject: query.reload, canEdit: !project.archived && ['approver', 'editor'].includes(project.role), canApprove: !project.archived && project.role === 'approver' } satisfies WorkspaceContext} />
    </main></div>;
}

export function ProjectSettings() {
  const { project, refreshProject, canApprove } = useWorkspace(); const members = useApi<Member[]>(projectUrl(project.id, 'members'), true);
  const action = useAction(); const [userId, setUserId] = useState(''); const [role, setRole] = useState<Role>('viewer');
  async function saveMember(id: string, newRole: Role) { await put(projectUrl(project.id, 'members'), { user_id: id, role: newRole }); members.reload(); refreshProject(); }
  return <><div className="page-heading"><div><h1>프로젝트 설정</h1><p>멤버 권한과 프로젝트 보관 상태를 관리합니다.</p></div></div>
    <section className="sheet"><PanelHeading title="참여 멤버"><button className="button small" onClick={members.reload}>새로 조회</button></PanelHeading>
      <ErrorNotice error={members.error} retry={members.reload} />{members.loading && <Loading />}
      {members.data && (members.data.length ? <div className="table-scroll"><table><thead><tr><th>사용자</th><th>권한</th><th>권한 변경</th></tr></thead><tbody>{members.data.map(member => <tr key={member.user_id}>
        <td>{member.user?.name || member.name || member.user?.email || member.email || member.user_id}<small className="block muted">{member.user?.email ?? member.email}</small></td><td><Status value={member.role} /></td><td>
          {canApprove ? <label><span className="sr-only">{member.name ?? member.user_id} 권한</span><select value={member.role} disabled={action.pending} onChange={event => void action.run(() => saveMember(member.user_id, event.target.value as Role), '멤버 권한을 변경했습니다.')}>
            <option value="viewer">조회자</option><option value="editor">작업자</option><option value="approver">승인자</option></select></label> : '승인자만 변경 가능'}</td>
      </tr>)}</tbody></table></div> : <Empty title="멤버 기록이 없습니다" />)}
      {canApprove && <form className="inline-form padded" onSubmit={submit(() => action.run(async () => { await saveMember(userId.trim(), role); setUserId(''); }, '멤버를 등록했습니다.'))}>
        <Field label="등록할 사용자 ID" hint="운영자가 발급한 계정의 사용자 ID를 입력해 주세요."><input required value={userId} onChange={event => setUserId(event.target.value)} /></Field>
        <Field label="프로젝트 권한"><select value={role} onChange={event => setRole(event.target.value as Role)}><option value="viewer">조회자</option><option value="editor">작업자</option><option value="approver">승인자</option></select></Field>
        <button className="button primary" disabled={action.pending}>멤버 등록</button></form>}
    </section>
    <ErrorNotice error={action.error} /><Success>{action.success}</Success>
    <section className="sheet padded"><h2>프로젝트 보관</h2><p className="muted">보관한 프로젝트는 조회할 수 있으며, 보관 해제 후 업무를 이어갈 수 있습니다.</p>
      {project.role === 'approver' ? <button className="button" disabled={action.pending} onClick={() => void action.run(async () => { await post(projectUrl(project.id, 'archive'), { archived: !project.archived }); refreshProject(); }, project.archived ? '보관을 해제했습니다.' : '프로젝트를 보관했습니다.')}>{project.archived ? '보관 해제' : '프로젝트 보관'}</button> : <ReadOnly />}
    </section></>;
}

export function Account() {
  const { user } = useAuth(); const action = useAction(); const [current, setCurrent] = useState(''); const [password, setPassword] = useState(''); const [confirm, setConfirm] = useState('');
  return <main id="main" className="page narrow"><div className="page-heading"><div><h1>내 계정</h1><p>{user?.name} · {user?.email}</p></div></div>
    <section className="sheet"><PanelHeading title="비밀번호 변경" /><form className="form-stack padded" onSubmit={submit(() => action.run(async () => {
      if (password !== confirm) throw new Error('새 비밀번호와 확인 값이 일치하지 않습니다.');
      await post('/auth/password', { current_password: current, new_password: password }); setCurrent(''); setPassword(''); setConfirm('');
    }, '비밀번호를 변경했습니다.'))}>
      <Field label="현재 비밀번호"><input type="password" required autoComplete="current-password" value={current} onChange={event => setCurrent(event.target.value)} /></Field>
      <Field label="새 비밀번호" hint="12~128자로 입력해 주세요."><input type="password" required minLength={12} maxLength={128} autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)} /></Field>
      <Field label="새 비밀번호 확인"><input type="password" required autoComplete="new-password" value={confirm} onChange={event => setConfirm(event.target.value)} /></Field>
      <ErrorNotice error={action.error} /><Success>{action.success}</Success><div><button className="button primary" disabled={action.pending}>비밀번호 변경</button></div>
    </form></section></main>;
}

export function AdminUsers() {
  const { user } = useAuth(); const users = useApi<import('./types').User[]>(user?.is_admin ? '/admin/users' : null, true); const action = useAction();
  const [email, setEmail] = useState(''); const [name, setName] = useState(''); const [password, setPassword] = useState('');
  if (!user?.is_admin) return <main id="main" className="page"><h1>계정 발급</h1><p className="notice caution">운영자만 계정을 발급할 수 있습니다.</p><Link to="/projects">프로젝트 목록으로 이동</Link></main>;
  return <main id="main" className="page"><div className="page-heading"><div><h1>계정 발급</h1><p>사용자를 등록한 뒤 프로젝트 설정에서 역할을 지정해 주세요.</p></div></div>
    <div className="settings-grid"><section className="sheet"><PanelHeading title="사용자 등록" /><form className="form-stack padded" onSubmit={submit(() => action.run(async () => {
      await post('/admin/users', { email: email.trim(), name: name.trim(), password, is_admin: false }); setEmail(''); setName(''); setPassword(''); users.reload();
    }, '계정을 발급했습니다. 목록에서 사용자 ID를 확인할 수 있습니다.'))}>
      <Field label="이름"><input required value={name} autoComplete="off" onChange={event => setName(event.target.value)} /></Field>
      <Field label="이메일"><input type="email" required value={email} autoComplete="off" onChange={event => setEmail(event.target.value)} /></Field>
      <Field label="초기 비밀번호" hint="12~128자로 입력해 주세요."><input type="password" required minLength={12} maxLength={128} value={password} autoComplete="new-password" onChange={event => setPassword(event.target.value)} /></Field>
      <ErrorNotice error={action.error} /><Success>{action.success}</Success><button className="button primary" disabled={action.pending}>사용자 발급</button>
    </form></section><section className="sheet"><PanelHeading title="발급한 사용자"><button className="button small" onClick={users.reload}>새로 조회</button></PanelHeading>
      <ErrorNotice error={users.error} retry={users.reload} />{users.loading && <Loading />}{users.data && (users.data.length ? <div className="table-scroll"><table><thead><tr><th>이름·이메일</th><th>사용자 ID</th><th>운영자</th></tr></thead><tbody>
        {users.data.map(item => <tr key={item.id}><td>{item.name}<small className="block muted">{item.email}</small></td><td className="id-cell">{item.id}</td><td>{item.is_admin ? '예' : '아니요'}</td></tr>)}
      </tbody></table></div> : <Empty title="발급된 계정이 없습니다" />)}</section></div></main>;
}
