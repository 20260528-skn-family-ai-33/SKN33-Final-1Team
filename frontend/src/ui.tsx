import { useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { ApiError, fileUrl } from './api';
import type { Evidence, Json, ServerIssue } from './types';

export function ErrorNotice({ error, retry }: { error: unknown; retry?: () => void }) {
  if (!error) return null;
  const failure = error instanceof ApiError ? error : null;
  const titles: Record<number, string> = { 0: '연결 오류', 401: '세션 만료', 403: '권한 부족', 404: '조회 불가', 409: '상태 충돌', 413: '파일 제한', 422: '입력 확인', 501: '지원 준비 필요', 503: '서비스 준비 필요' };
  const messages: Record<string, string> = {
    invalid_credentials: '이메일 또는 비밀번호가 올바르지 않습니다.', invalid_password: '현재 비밀번호가 올바르지 않습니다.',
    unauthenticated: '세션이 만료되었거나 로그인이 필요합니다. 다시 로그인해 주세요.', forbidden: '이 작업을 수행할 프로젝트 권한이 없습니다.',
    admin_required: '운영자 계정으로 이용할 수 있는 기능입니다.', not_found: '대상을 찾을 수 없거나 접근할 수 없습니다.',
    invalid_input: '입력한 값이 요청 형식과 맞지 않습니다. 필수 값과 길이를 확인해 주세요.',
    database_unavailable: '데이터베이스 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.', database_not_ready: '데이터베이스가 아직 준비되지 않았습니다.',
    graph_unavailable: '관계 저장소에 연결할 수 없습니다. 잠시 후 다시 조회해 주세요.', approved_criteria_required: '현재 문서의 조건 기준을 먼저 승인해 주세요.',
    stale_criteria: '현재 문서의 최신 조건 기준만 승인할 수 있습니다. 새로 조회해 주세요.', stale_review: '최신 산출물과 현재 기준으로 재검수한 뒤 승인해 주세요.',
    stale_logos: '승인된 로고가 변경되었습니다. 다시 검사한 뒤 승인해 주세요.', changes_requested: '수정 요청이 있습니다. 수정 버전을 등록하고 전체 재검수를 완료해 주세요.',
    exception_required: '미충족·미검사 필수 조건과 미확정 관계에 대한 예외 대상과 사유가 필요합니다.', already_approved: '이미 최종 승인된 실행입니다. 새 검사를 진행해 주세요.',
    unapproved_change_request: '수정 요청을 승인한 뒤 수정 버전을 연결해 주세요.', no_applicable_criteria: '이 산출물에 적용되는 조건이 없습니다. 적용 매체와 지정 산출물을 확인해 주세요.',
    upload_size_limit: '파일이 비어 있거나 업로드 크기 제한을 초과했습니다.', previous_run_active: '연결하려는 이전 검사가 아직 진행 중입니다.',
    unrelated_previous_run: '동일한 산출물의 이전 실행을 선택해 주세요.', change_request_mismatch: '연결한 수정 요청과 이전 실행이 일치하지 않습니다.',
    csrf_required: '요청 보안 확인에 실패했습니다. 세션을 새로 확인한 뒤 다시 시도해 주세요.', invalid_csrf: '요청 보안 확인에 실패했습니다. 다시 로그인해 주세요.',
    internal_error: '서버가 요청을 처리하지 못했습니다. 요청 번호와 함께 운영자에게 문의해 주세요.',
  };
  const original = error instanceof Error ? error.message : '요청을 처리하지 못했습니다. 다시 시도해 주세요.';
  const message = (failure && messages[failure.code]) || (/[가-힣]/.test(original) ? original : '요청을 처리하지 못했습니다. 아래 서버 상세와 입력·권한·서비스 상태를 확인해 주세요.');
  const title = failure?.code === 'invalid_credentials' ? '로그인 실패' : failure?.code === 'invalid_password' ? '비밀번호 확인' : failure ? titles[failure.status] ?? '요청 실패' : '요청 실패';
  return <div className="notice error" role="alert"><strong>{title}</strong>
    <p>{message}</p>{message !== original && <details><summary>서버 상세</summary><p>{original}</p></details>}
    {failure?.requestId && <small>요청 번호: {failure.requestId}</small>}
    {retry && <button type="button" className="button small" onClick={retry}>다시 조회</button>}
  </div>;
}
export function Success({ children }: { children: ReactNode }) { return children ? <p className="notice success" role="status">{children}</p> : null; }
export function Loading({ label = '자료를 조회하고 있습니다.' }: { label?: string }) {
  return <div className="loading" role="status" aria-live="polite"><span />{label}</div>;
}
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><h3>{title}</h3>{children && <p>{children}</p>}</div>;
}
export function PanelHeading({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="panel-heading"><h2>{title}</h2><div className="actions">{children}</div></div>;
}
export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}
export function JsonView({ value }: { value: unknown }) {
  return <pre className="json-value">{value === undefined || value === null ? '기록 없음' : typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</pre>;
}
export function formatValue(value: Json | undefined): string {
  if (value === undefined || value === null) return '기록 없음';
  return typeof value === 'string' ? value : JSON.stringify(value);
}
export function dateTime(value?: string | null) {
  if (!value) return '기록 없음';
  const time = new Date(value);
  return Number.isNaN(time.valueOf()) ? value : time.toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' });
}
export function frameTime(ms?: number | null) {
  if (ms === undefined || ms === null) return '';
  const seconds = Math.floor(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}.${String(Math.floor(ms % 1000)).padStart(3, '0')}`;
}
const labels: Record<string, string> = {
  queued: '대기', pending: '대기', running: '실행 중', processing: '처리 중', extracting: '문서 추출 중',
  waiting_review: '사람 확인 대기', completed: '실행 완료', failed: '실행 실패', cancel_requested: '취소 요청 중', cancelled: '취소됨',
  pass: '충족', fail: '위반', review: '확인 필요', unsupported: '검사 미지원', approved: '승인됨', draft: '초안',
  ready: '준비됨', extracted: '추출 완료', pending_approval: '승인 대기', synced: '동기화됨', stale: '동기화 지연', unavailable: '조회 불가',
  approver: '승인자', editor: '작업자', viewer: '조회자', poster: '포스터', video: '영상',
};
export function Status({ value }: { value?: string | null }) {
  const state = value ?? 'none';
  const tone = ['pass', 'approved', 'ready', 'synced', 'extracted'].includes(state) ? 'positive'
    : ['fail', 'failed', 'unavailable'].includes(state) ? 'negative'
    : ['review', 'waiting_review', 'unsupported', 'stale', 'pending_approval'].includes(state) ? 'caution' : 'neutral';
  return <span className={`status ${tone}`}>{labels[state] ?? (state === 'none' ? '미판정' : state)}</span>;
}
export function IssueList({ issues }: { issues?: (ServerIssue | string)[] }) {
  return issues?.length ? <ul className="issue-list">{issues.map((issue, index) => <li key={index}>{typeof issue === 'string' ? issue : issue.message ?? JSON.stringify(issue)}</li>)}</ul> : null;
}
export function ReadOnly({ archived = false }: { archived?: boolean }) {
  return <p className="notice info">{archived ? '보관된 프로젝트입니다. 수정하려면 프로젝트 설정에서 보관을 해제해 주세요.' : '조회 권한입니다. 등록·수정은 작업자, 승인은 승인자에게 요청해 주세요.'}</p>;
}
export function submit(action: () => void | Promise<unknown>) { return (event: FormEvent) => { event.preventDefault(); void action(); }; }

export function FilePreview({ fileId, kind, title, evidence }: { fileId: string; kind?: 'poster' | 'video' | 'document' | 'image'; title: string; evidence?: Evidence }) {
  const [resource, setResource] = useState<{ url: string; mime: string } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [revision, setRevision] = useState(0);
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = '';
    setResource(null); setError(null);
    (async () => {
      const response = await fetch(fileUrl(fileId), { credentials: 'include', cache: 'no-store', signal: controller.signal,
        headers: kind === 'video' ? { Range: 'bytes=0-0' } : undefined });
      if (!response.ok) {
        const envelope = await response.json().catch(() => null);
        throw new ApiError(response.status, envelope?.error?.code ?? 'file_error', envelope?.error?.message ?? '파일에 접근할 수 없습니다. 권한과 파일 상태를 확인해 주세요.', envelope?.request_id);
      }
      const mime = response.headers.get('Content-Type') ?? '';
      if (mime.includes('application/json')) throw new ApiError(200, 'invalid_file', '파일 대신 다른 형식의 응답을 받았습니다.');
      if (kind === 'video') {
        await response.body?.cancel();
        if (!controller.signal.aborted) setResource({ url: fileUrl(fileId), mime: 'video/mp4' });
      } else {
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob); setResource({ url: objectUrl, mime: blob.type || mime });
      }
    })().catch(failure => { if (!controller.signal.aborted) setError(failure); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [fileId, kind, revision]);
  useEffect(() => { if (video.current && evidence?.frame_ms != null) video.current.currentTime = evidence.frame_ms / 1000; }, [evidence?.frame_ms, resource]);
  const box = evidence?.bbox;
  const validBox = box?.length === 4 && box.every(Number.isFinite) && box[0] >= 0 && box[1] >= 0 && box[2] > 0 && box[3] > 0 && box[0] + box[2] <= 1.001 && box[1] + box[3] <= 1.001;
  return <div className="file-preview">
    <ErrorNotice error={error} retry={() => setRevision(value => value + 1)} />
    {!resource && !error && <Loading label="파일 권한과 내용을 확인하고 있습니다." />}
    {resource && <>
      <div className="media-stage">
        {kind === 'video' ? <video ref={video} controls preload="metadata" src={resource.url}
          onLoadedMetadata={() => { if (video.current && evidence?.frame_ms != null) video.current.currentTime = evidence.frame_ms / 1000; }}
          onError={() => setError(new Error('영상을 재생할 수 없습니다. 지원 형식과 파일 상태를 확인해 주세요.'))} aria-label={title} />
          : resource.mime.startsWith('image/') ? <img src={resource.url} alt={title} onError={() => setError(new Error('이미지를 표시할 수 없습니다.'))} />
          : resource.mime.includes('pdf') ? <iframe title={title} src={resource.url} className="document-frame" />
          : <p className="preview-unavailable">이 파일 형식은 브라우저에서 미리 볼 수 없습니다. 파일을 내려받거나 아래 추출 원문을 대조해 주세요.</p>}
        {validBox && <span className="evidence-box" aria-label="서버가 제공한 근거 위치" style={{ left: `${box[0] * 100}%`, top: `${box[1] * 100}%`, width: `${box[2] * 100}%`, height: `${box[3] * 100}%` }} />}
      </div>
      <div className="preview-toolbar"><a className="text-link" href={resource.url} download={title}>파일 내려받기</a>
        {evidence?.frame_ms != null && <span>영상 시점 {frameTime(evidence.frame_ms)}</span>}</div>
      {box && !validBox && <p className="notice info">근거 좌표가 유효하지 않아 위치를 표시하지 않았습니다.</p>}
    </>}
    {evidence?.text && <blockquote className="evidence-quote">{evidence.text}</blockquote>}
  </div>;
}
