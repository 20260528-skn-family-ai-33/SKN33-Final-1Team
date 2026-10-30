import React from 'react';

// 브랜드 버튼을 눌러 서비스 소개 화면으로 이동한다.
export function Brand({ onClick }) {
  return <button type="button" className="brand brand-button" onClick={onClick} aria-label="크리에이티브체커 홈">
    <svg className="brand-symbol" viewBox="0 0 28 28" aria-hidden="true">
      <path fill="currentColor" d="M3 3c3-3 8 2 11 7 3-5 8-10 11-7s-2 8-7 11c5 3 10 8 7 11s-8-2-11-7c-3 5-8 10-11 7s2-8 7-11C5 11 0 6 3 3Z" />
      <circle cx="14" cy="14" r="3" fill="var(--soft)" />
    </svg><span>Creative Checker</span>
  </button>;
}
