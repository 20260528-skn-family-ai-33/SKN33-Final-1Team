import React from 'react';
import { Link, useNavigate } from 'react-router';
import { Brand } from '../components/Brand.jsx';
import { MissingPosterArt } from '../components/ReviewArt.jsx';
import '../styles/review-art.css';

// 없는 주소: 무엇이 잘못됐는지와 갈 수 있는 곳 두 군데를 보여 준다.
export default function NotFoundPage() {
  const navigate = useNavigate();
  return <div className="cc-missing-page">
    <header className="cc-missing-top"><Brand onClick={() => navigate('/')} /></header>
    <main className="cc-missing-sheet" aria-labelledby="cc-missing-title">
      <MissingPosterArt />
      <h1 id="cc-missing-title">페이지를 찾을 수 없습니다.</h1>
      <p>주소를 확인하거나 서비스 소개에서 다시 시작해 주세요.</p>
      <div className="cc-missing-actions">
        <Link className="rv-button primary" to="/">서비스 소개로 이동</Link>
        <Link className="rv-button secondary" to="/review/new">포스터 검수 열기</Link>
      </div>
    </main>
  </div>;
}
