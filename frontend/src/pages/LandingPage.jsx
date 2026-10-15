import React from 'react';
import { Link, useNavigate } from 'react-router';
import { Brand } from '../components/Brand.jsx';
import { Icon } from '../components/Icon.jsx';

// 서비스 소개에서 검수 또는 샘플 진입 경로로 이동한다.
export default function LandingPage() {
  const navigate = useNavigate();
  // 보호 경로로 이동하면 로그인 여부에 맞춰 진입 화면이 결정된다.
  function start() { navigate('/review/new'); }
  // 로그인 뒤에도 샘플 선택 의도를 유지한다.
  function sample() { navigate('/review/new?sample=1'); }
  return <section aria-label="서비스 소개">
    <header className="landing-nav"><Brand onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })} /><nav className="nav-links" aria-label="메인 메뉴"><a href="#features">서비스 소개</a><a href="#how-it-works">이용 방법</a><Link className="login-link" to="/login">로그인</Link><button className="btn primary" onClick={start}>검수 시작하기</button></nav></header>
    <div className="landing-hero"><div className="hero-grid"><div className="hero-copy"><span className="pill">이미지 · 포스터 검수 자동화</span><h1>광고물 검수,<br /><em>기준부터 확실하게.</em></h1><p>이미지와 요구사항을 올리고 검수 기준을 확정하세요.<br />문구부터 시각 요소까지, 놓치기 쉬운 항목을<br className="desktop-break" /> 한곳에서 확인할 수 있습니다.</p><div className="hero-actions"><button className="btn primary" onClick={start}>검수 시작하기<Icon name="arrow" /></button><button className="btn secondary" onClick={sample}>샘플로 체험하기</button></div><p className="hero-footnote"><Icon name="shield" />확정한 기준과 실제 이미지 근거를 함께 확인하세요.</p></div>
      <div className="hero-preview" aria-label="김천김밥축제 샘플과 검수 순서"><div className="preview-top"><span>김천김밥축제 · 포스터 검수</span><span className="pill">실행 전</span></div><div className="preview-body"><div className="landing-sample"><img src="/api/sample/image" alt="2026 김천김밥축제 샘플 포스터" /><span>검수 대상 이미지</span></div><div className="preview-list"><div className="preview-label">내가 정하는 검수 기준</div>{[['01', '이미지와 요구사항', '한 줄에 하나씩, 구체적인 조건'], ['02', '검수 기준 확정', '정보와 시각 항목을 직접 선택'], ['03', '문구와 위치 추출', '실제 이미지에서 근거 확인'], ['04', '항목별 결과 검토', '충족 · 위반 · 확인 필요']].map(([num, title, detail]) => <div className="preview-line" key={num}><span className="preview-number">{num}</span><div><strong>{title}</strong><small>{detail}</small></div></div>)}</div></div><div className="preview-note"><Icon name="lock" />기준을 확정한 뒤 검수를 실행합니다.</div><div className="preview-float"><Icon name="search" /><div>작은 오류도, 근거와 함께<small>항목별 결과와 위치를 한눈에</small></div></div></div>
    </div></div>
    <section className="landing-section" id="features"><div className="section-heading"><div><span className="eyebrow">WHY 크리에이티브체커</span><h2>반복 확인은 줄이고, 기준은 명확하게.</h2></div><p>납품 전 확인부터 수정 후 재검수까지</p></div><div className="feature-grid">{[['list', '요구사항을 개별 검수 항목으로', '행사명, 기간, 장소 등의 요구사항을 항목별로 정리하고 필요한 기준을 직접 선택합니다.'], ['search', '결과를 이미지 근거와 함께', '이미지에서 읽은 문구와 위치를 확인합니다. 자동 판단이 어려운 시각 요소는 담당자가 검토합니다.'], ['shield', '확정한 기준으로 일관된 검수', '선택한 검수 기준을 버전으로 고정합니다. 기준을 변경하면 다시 확정하고 새로 검수합니다.']].map(([icon, title, text]) => <article key={icon}><div className="feature-icon"><Icon name={icon} /></div><h3>{title}</h3><p>{text}</p></article>)}</div></section>
    <section className="how-section" id="how-it-works"><div className="landing-section"><div className="section-heading"><div><span className="eyebrow">HOW IT WORKS</span><h2>검수 시작까지, 간단한 네 단계.</h2></div><button className="text-btn" onClick={sample}>샘플로 직접 확인하기</button></div><div className="how-grid">{[['이미지 업로드', '검수할 이미지나 포스터를 올립니다.'], ['요구사항 입력', '확인할 문구와 시각 조건을 입력합니다.'], ['검수 기준 확정', '개별 항목을 검토하고 선택합니다.'], ['결과와 근거 확인', '확정한 기준으로 실제 검수를 실행합니다.']].map(([title, text], i) => <article className="how-item" key={title}><span>0{i + 1}</span><h3>{title}</h3><p>{text}</p></article>)}</div></div></section>
    <footer className="landing-footer"><span className="brand">크리에이티브체커</span><span>OCR 근거 기반 검수 · 최종 승인은 담당자가 결정합니다.</span><span>SKN33 · 1TEAM</span></footer>
  </section>;
}

