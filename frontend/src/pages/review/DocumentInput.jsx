import React, { useEffect, useState } from 'react';
import { Icon } from '../../components/Icon.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { loadDocumentProjects, createDocumentProject } from './frameworkClient.js';

export default function DocumentInput({ documentBusy, documentProgress, documentNotice, documentError, documentInput, selectDocument, cancelDocument, sampleBusy, projectName }) {
  const [now, setNow] = useState(Date.now);
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState('');
  const [projectsBusy, setProjectsBusy] = useState(true);
  const [projectsError, setProjectsError] = useState('');
  const [reload, setReload] = useState(0);
  const [creating, setCreating] = useState(false);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setProjectsBusy(true); setProjectsError(''); setConnected(false);
    loadDocumentProjects({ signal: controller.signal }).then(items => {
      if (controller.signal.aborted) return;
      setProjects(items);
      setConnected(true);
      setProjectId(current => items.some(item => item.id === current) ? current : items.length === 1 ? items[0].id : '');
    }).catch(error => {
      if (!controller.signal.aborted) setProjectsError(error.status === 401
        ? 'PDF 분석 서버에 같은 PostgreSQL 계정으로 로그인해야 합니다. 서버 연결 설정을 확인해 주세요.' : error.message);
    }).finally(() => { if (!controller.signal.aborted) setProjectsBusy(false); });
    return () => controller.abort();
  }, [reload]);
  async function createProject() {
    if (!projectName?.trim()) { setProjectsError('위의 프로젝트 이름을 입력한 뒤 만들어 주세요.'); return; }
    setCreating(true); setProjectsError('');
    try {
      const project = await createDocumentProject(projectName.trim());
      setProjects(current => [...current, project]); setProjectId(project.id);
    } catch (error) { setProjectsError(`${error.message} 목록을 다시 불러와 생성 여부를 확인해 주세요.`); }
    finally { setCreating(false); }
  }
  useEffect(() => {
    if (!documentBusy) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [documentBusy, documentProgress?.startedAt]);
  const seconds = Math.max(0, Math.floor((now - (documentProgress?.startedAt || now)) / 1000));
  const elapsed = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  return <div className="document-input" aria-busy={documentBusy}>
    <div className="document-project"><label htmlFor="document-project">PDF를 저장할 프로젝트</label>
      <select id="document-project" value={projectId} onChange={event => setProjectId(event.target.value)} disabled={documentBusy || projectsBusy || creating}>
        <option value="">{projectsBusy ? '프로젝트 불러오는 중…' : '프로젝트 선택'}</option>
        {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
      </select>
      <div className="document-project-actions"><button type="button" className="text-btn" disabled={projectsBusy || creating || documentBusy} onClick={() => setReload(value => value + 1)}>목록 새로고침</button>
        <button type="button" className="text-btn" disabled={projectsBusy || creating || documentBusy || !connected} onClick={createProject}>{creating ? '만드는 중…' : '위 이름으로 프로젝트 만들기'}</button></div>
      <ErrorMessage message={projectsError} />
    </div>
    <div className="document-input-heading"><div><strong>요구사항 문서 불러오기</strong><p>PDF 전체에서 검수할 내용을 찾아 항목별 문단으로 추가합니다.</p></div>
      <input ref={documentInput} type="file" className="sr-only" accept="application/pdf,.pdf" aria-label="요구사항 PDF 선택" disabled={documentBusy || sampleBusy || !projectId || projectsBusy}
        onChange={event => { if (event.target.files.length) selectDocument([...event.target.files], projectId); }} />
      <button type="button" className="btn secondary small" disabled={documentBusy || sampleBusy || !projectId || projectsBusy || !connected} onClick={() => documentInput.current.click()}><Icon name="file" />{documentBusy ? '요구사항 정리 중…' : 'PDF 파일 추가'}</button>
    </div>
    <p className="help">텍스트 PDF 1개 · 최대 10MiB / 50페이지 · 기존 입력은 유지됩니다.</p>
    {documentProgress && <div className={`document-progress ${documentProgress.stage === 'completed' ? 'completed' : ''}`}>
      <div className="document-progress-heading" role="status"><span>{documentProgress.message}</span><strong>{documentProgress.percent}%</strong></div>
      <div className="document-progress-track" role="progressbar" aria-label="PDF 조건 읽기 진행률" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={documentProgress.percent} aria-valuetext={`${documentProgress.percent}% · ${documentProgress.message}`}>
        <div className="document-progress-fill" style={{ width: `${documentProgress.percent}%` }} />
      </div>
      <div className="document-progress-meta"><span>{documentBusy ? '처리 중' : '반영 완료'} · 경과 {elapsed}</span><span>처리 단계 기준</span></div>
    </div>}
    {documentNotice && <p className="document-notice" role="status">{documentNotice}</p>}
    {documentBusy && <button type="button" className="text-btn" onClick={cancelDocument}>입력 반영 취소</button>}
    <ErrorMessage message={documentError} />
  </div>;
}
