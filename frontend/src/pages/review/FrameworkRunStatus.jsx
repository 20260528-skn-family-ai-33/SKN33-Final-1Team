import React from 'react';
import { Link } from 'react-router';

export default function FrameworkRunStatus({ state, onRetry }) {
  const denied = state === 'denied';
  const checking = state === 'checking';
  return <section className={`rv-panel rv-framework-status-state${checking ? ' is-checking' : ''}`}
    aria-labelledby="page-title">
    {checking && <span className="rv-framework-status-spinner" aria-hidden="true" />}
    <h1 id="page-title" tabIndex={-1}>
      {checking ? '검수 상태를 확인하고 있어요.' : denied ? '이 작업을 열 수 없습니다.' : '작업 상태를 불러오지 못했습니다.'}
    </h1>
    <p role={checking ? 'status' : undefined}>
      {checking ? '계정 접근 권한과 저장된 진행 상태를 확인하고 있습니다.'
        : denied ? '현재 계정에서 접근할 수 없는 작업입니다.'
          : '연결 상태를 확인한 뒤 다시 시도해 주세요.'}
    </p>
    <div className="rv-framework-status-actions">
      {denied ? <Link className="rv-button secondary" to="/review/tasks">내 작업으로 돌아가기</Link>
        : !checking && <>
          <button type="button" className="rv-button secondary" onClick={onRetry}>상태 다시 확인</button>
          <Link className="text-btn" to="/review/tasks">내 작업으로 돌아가기</Link>
        </>}
    </div>
  </section>;
}
