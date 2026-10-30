import React from 'react';
import { Link, useNavigate } from 'react-router';
import { Brand } from '../components/Brand.jsx';
import { Icon } from '../components/Icon.jsx';
import { useScrollScene } from '../hooks/useScrollScene.js';
import skyPreview from '../assets/fairweather-public-preview.png';
import '../styles/landing-fairweather.css';

const features = [
  { number: '01', label: 'SET THE STANDARD', title: <>기준이 되는 내용을<br />한곳에 모으고.</>, text: '포스터와 요구사항을 준비하세요. 행사명, 기간, 필수 문구를 검수할 항목으로 정리합니다.', kind: 'criteria' },
  { number: '02', label: 'LOOK AT THE EVIDENCE', title: <>결과를 넘어<br />근거까지 살펴보고.</>, text: '이미지에서 읽은 문구와 위치를 함께 확인하세요. 자동 판단이 어려운 항목도 구분해 보여줍니다.', kind: 'evidence' },
  { number: '03', label: 'MAKE THE CALL', title: <>놓친 곳을 확인하고<br />다음 작업을 결정해요.</>, text: '충족, 위반, 확인 필요를 항목별로 검토하세요. 결과를 바탕으로 마지막 판단은 담당자가 내립니다.', kind: 'review' },
];

function WindowDots() {
  return <span className="fw-window-dots" aria-hidden="true"><i /><i /><i /></span>;
}

function PosterExample() {
  return <div className="fw-poster" aria-label="가상의 가을 문화축제 포스터">
    <small>A SEASON TOGETHER</small><strong>가을<br />문화축제</strong>
    <span className="fw-poster-leaves" aria-hidden="true"><i /><i /><i /></span>
    <span className="fw-poster-date">10.30 — 11.02</span><small>함께 만드는 가을의 순간</small>
  </div>;
}

function ReviewWindow() {
  return <div className="fw-review-window">
    <WindowDots />
    <div className="fw-window-heading"><span>Creative Checker</span><span className="fw-example-label">화면 예시</span></div>
    <div className="fw-window-body">
      <PosterExample />
      <div className="fw-window-results">
        <div className="fw-window-project"><span>포스터 · 가을 문화축제</span><strong>기준과 실제 내용을 나란히.</strong></div>
        <div className="fw-window-result"><span className="fw-example-status">확인할 내용</span><strong>행사 기간</strong><dl><div><dt>기준</dt><dd>10.30 — 11.01</dd></div><div><dt>포스터</dt><dd>10.30 — 11.02</dd></div></dl></div>
        <div className="fw-window-row"><span><i aria-hidden="true" />행사명 문구</span><span>일치</span></div>
        <div className="fw-window-row"><span>로고 가독성</span><span>담당자 확인</span></div>
      </div>
    </div>
  </div>;
}

function FeatureArtwork({ kind }) {
  return <div className={`fw-feature-art fw-feature-art-${kind}`} aria-hidden="true">
    <div className="fw-mini-window"><WindowDots />
      {kind === 'criteria' && <><span className="fw-mini-kicker">검수 기준</span><div className="fw-mini-rule"><i>✓</i><span>행사명과 기간</span></div><div className="fw-mini-rule"><i>✓</i><span>필수 안내 문구</span></div><div className="fw-mini-rule"><i>✓</i><span>로고와 배치</span></div></>}
      {kind === 'evidence' && <><span className="fw-mini-kicker">포스터에서 확인한 근거</span><div className="fw-mini-quote">10.30 — <mark>11.02</mark></div><div className="fw-mini-source"><span>행사 기간</span><span>위치와 문구 함께 보기 ↗</span></div></>}
      {kind === 'review' && <><span className="fw-mini-kicker">항목별 검토</span><div className="fw-mini-verdict"><i />충족<span>기준과 일치</span></div><div className="fw-mini-verdict">↗ 위반<span>수정할 내용</span></div><div className="fw-mini-verdict">○ 확인 필요<span>담당자 판단</span></div></>}
    </div>
  </div>;
}

// 소개 화면은 API 요청 없이 렌더링하고, 실제 작업은 각 프로필의 진입 경로에 맡긴다.
export default function LandingPage({ profile = 'legacy' }) {
  const navigate = useNavigate();
  const { rootRef, sceneRef, stageRef } = useScrollScene();
  const isV1 = profile === 'v1';
  const sampleLabel = isV1 ? '프로젝트 둘러보기' : '샘플로 체험하기';
  function start() { navigate(isV1 ? '/projects' : '/review/new'); }
  function sample() { navigate(isV1 ? '/projects' : '/review/new?sample=1'); }
  function home() {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: 0, behavior: reduced ? 'instant' : 'smooth' });
  }

  return <section className="fw-landing" ref={rootRef} aria-label="서비스 소개">
    <a className="fw-skip-link" href="#landing-content">본문으로 바로 가기</a>
    <header className="fw-nav fw-container">
      <Brand onClick={home} />
      <nav className="fw-nav-links" aria-label="메인 메뉴"><a href="#features">서비스 소개</a><a href="#how-it-works">이용 방법</a></nav>
      <div className="fw-nav-actions"><Link className="fw-login" to="/login">로그인</Link><button className="fw-button fw-button-light fw-nav-start" onClick={start}>검수 시작하기 <span aria-hidden="true">↗</span></button></div>
    </header>
    <main id="landing-content" tabIndex={-1}>
      <div className="fw-container">
        <div className="fw-intro-line"><span><i aria-hidden="true" />이미지 · 포스터 검수</span><a href="#how-it-works">기준부터 결과까지 <span aria-hidden="true">↗</span></a></div>
        <section className="fw-scene" ref={sceneRef} aria-labelledby="fw-hero-title">
          <div className="fw-scene-stage" ref={stageRef}>
            <div className="fw-hero-sky">
              <div className="fw-sky-viewport" aria-hidden="true"><img src={skyPreview} alt="" fetchPriority="high" /></div>
              <ReviewWindow />
            </div>
            <div className="fw-hero-copy">
              <div><h1 id="fw-hero-title">좋은 작업이<br />좋은 마무리를<br />만날 수 있도록.</h1><p>포스터 속 작은 문구부터 놓치기 쉬운 조건까지.<br className="fw-desktop-break" /> 확정한 기준과 이미지 근거를 함께 확인하세요.</p><div className="fw-actions"><button className="fw-button fw-button-dark" onClick={start}>검수 시작하기 <Icon name="arrow" /></button><button className="fw-button fw-button-light" onClick={sample}>{sampleLabel}</button></div></div>
              <div className="fw-hero-aside"><span className="fw-overline">A CLEARER FINISH</span><p>기준을 정하고.<br />근거를 살피고.<br />사람이 판단합니다.</p><span className="fw-scroll-hint">아래로 이어서 보기 <span aria-hidden="true">↓</span></span></div>
            </div>
          </div>
        </section>
      </div>
      <section className="fw-section fw-container" id="features" aria-labelledby="fw-features-title" data-reveal>
        <div className="fw-section-heading"><span className="fw-overline">ONE PLACE, A CLEARER VIEW</span><h2 id="fw-features-title">각각 보던 정보를,<br />하나의 흐름으로.</h2><p>반복해서 확인하던 내용을 기준, 근거, 결과로 연결합니다.</p></div>
        <div className="fw-feature-grid">{features.map(feature => <article className="fw-feature-card" key={feature.number}>
          <FeatureArtwork kind={feature.kind} />
          <div className="fw-feature-copy"><span className="fw-overline"><i aria-hidden="true" />{feature.number} · {feature.label}</span><h3>{feature.title}</h3><p>{feature.text}</p></div>
        </article>)}</div>
      </section>
      <section className="fw-section fw-container" aria-labelledby="fw-evidence-title" data-reveal>
        <div className="fw-section-heading"><span className="fw-overline">THE DETAIL MAKES THE DIFFERENCE</span><h2 id="fw-evidence-title">같은 기준을 확인하고,<br />같은 근거를 바라보세요.</h2><p>결과만으로는 알기 어려운 차이. 원래 조건과 관측한 내용을 함께 봅니다.</p></div>
        <div className="fw-evidence-grid">
          <article className="fw-detail-card"><div className="fw-card-heading"><h3>확인하려는 내용</h3><span className="fw-example-label">화면 예시</span></div><span className="fw-field-caption">검수 기준</span><div className="fw-example-field">행사 기간은 10월 30일부터 11월 1일까지입니다.</div><span className="fw-field-caption">필수 안내 문구</span><div className="fw-example-field">“사전 예약 없이 누구나 참여 가능합니다.”</div><p className="fw-detail-note"><Icon name="check" />실행 전에 담당자가 항목을 검토하고 확정합니다.</p></article>
          <article className="fw-detail-card"><div className="fw-card-heading"><h3>이미지에서 읽은 내용</h3><span className="fw-example-label">화면 예시</span></div><div className="fw-evidence-callout"><span>행사 기간</span><strong>10.30 — <mark>11.02</mark></strong><p>기준과 포스터의 종료일이 다릅니다.</p></div><p className="fw-detail-note"><Icon name="search" />문구와 위치를 확인하고 수정할 내용을 판단하세요.</p></article>
        </div>
      </section>
      <section className="fw-section fw-container" id="how-it-works" aria-labelledby="fw-how-title" data-reveal>
        <div className="fw-section-heading fw-heading-with-action"><div><span className="fw-overline">FROM A POSTER TO A CLEAR ANSWER</span><h2 id="fw-how-title">시작은 가볍게.<br />확인은 차근차근.</h2></div><button className="fw-button fw-button-light" onClick={sample}>{sampleLabel} <span aria-hidden="true">↗</span></button></div>
        <ol className="fw-how-list">{[['이미지 업로드', '검수할 이미지나 포스터를 올립니다.'], ['요구사항 입력', '확인할 문구와 시각 조건을 입력합니다.'], ['검수 기준 확정', '개별 항목을 검토하고 선택합니다.'], ['결과와 근거 확인', '확정한 기준으로 검수를 실행하고 결과를 살펴봅니다.']].map(([title, text], i) => <li key={title}><span className="fw-step-index">0{i + 1}</span><h3>{title}</h3><p>{text}</p><Icon name={i === 3 ? 'check' : 'arrow'} /></li>)}</ol>
      </section>
      <section className="fw-invite fw-container" aria-labelledby="fw-invite-title" data-reveal><span className="fw-overline">READY WHEN YOU ARE</span><h2 id="fw-invite-title">맑게 확인하고,<br />가볍게 마무리하세요.</h2><p>오늘의 포스터에 필요한 기준과 근거.<br />Creative Checker에서 함께 확인하세요.</p><button className="fw-button fw-button-dark" onClick={start}>검수 시작하기 <Icon name="arrow" /></button></section>
    </main>
    <footer className="fw-footer fw-container"><Brand onClick={home} /><p>자동 검수는 판단을 돕습니다. 최종 확인은 담당자가 합니다.</p><span>Creative Checker</span></footer>
  </section>;
}
