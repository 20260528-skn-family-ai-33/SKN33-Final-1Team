import React, { Suspense, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router';
import { useOptionalAuth } from '../auth/AuthProvider.jsx';
import { Icon } from '../components/Icon.jsx';
import { PixelField } from '../components/PixelField.jsx';
import { PosterLayers, VideoLayers } from '../components/LayerFigure.jsx';
import { SiteHeader } from '../components/SiteHeader.jsx';
import { mediaChoicePath, mediaChoiceState } from '../workspace/mediaChoice.js';
import { useWorkspaceReset } from '../workspace/WorkspaceSession.jsx';
import demoPoster from '../assets/landing-demo-poster.jpg';
import '../styles/landing.css';

// 기능 소개의 세 그림(Stageshow 장면)은 소개 화면에서만 쓰므로 따로 불러와 첫 화면 글과 버튼을 먼저 보여 준다.
const tiles = () => import('../stageshow/FeatureTiles.tsx');
const CriteriaTile = React.lazy(() => tiles().then(scenes => ({ default: scenes.CriteriaTile })));
const EvidenceTile = React.lazy(() => tiles().then(scenes => ({ default: scenes.EvidenceTile })));
const DecisionTile = React.lazy(() => tiles().then(scenes => ({ default: scenes.DecisionTile })));

// 검수 예시의 세 단계. 포스터의 표시 상자는 '근거를 살펴보고'에서만 켠다.
const examples = [
  { kind: 'criteria', label: '기준을 정리하고', title: '무엇을 확인할지, 먼저 명확하게.',
    description: 'PDF 문서와 직접 입력한 요구사항을 검수할 항목으로 정리합니다. 실행 전, 담당자가 내용을 확인하고 기준을 확정합니다.',
    tag: '검수 기준 예시', expected: '전시 기간: 2025.11.19 — 2026.3.22', actual: '전시 기간이 포스터에 정확하게 표기되었는지 확인', status: '기준 확정 전' },
  { kind: 'evidence', label: '근거를 살펴보고', title: '판정 옆에, 눈으로 볼 수 있는 근거.',
    description: '기준 문구와 포스터에 적힌 전시 기간을 나란히 놓습니다. 같은 내용인지, 어디에 적혀 있는지 함께 확인하세요.',
    tag: '전시 기간 확인 예시', expected: '2025.11.19 — 2026.3.22', actual: '2025.11.19 — 2026.3.22', status: '전시 기간 일치 · 설명용 예시' },
  { kind: 'decision', label: '사람이 결정합니다', title: '마지막 판단은, 작업을 아는 사람에게.',
    description: '충족·위반·확인 필요를 검토하고, 보완할 내용을 결정합니다. 자동 판단이 어려운 부분도 구분해 담당자에게 전달합니다.',
    tag: '담당자 검토 예시', expected: '일치하는 항목과 보완할 항목을 함께 검토', actual: '수정할 내용과 판단 이유를 확인', status: '담당자 확인 대기' },
];
const features = [
  { icon: 'file', title: '흩어진 조건을 한곳에.', text: 'PDF 문서와 자유 입력으로 검수할 내용을 준비하고, 실행 전에 기준을 확인합니다.', tag: 'BRIEF → CRITERIA', Art: CriteriaTile },
  { icon: 'search', title: '눈으로 확인하는 근거.', text: '포스터에서 읽은 문구와 위치를 함께 보고, 기준과 다른 부분을 살펴봅니다.', tag: 'IMAGE → EVIDENCE', Art: EvidenceTile },
  { icon: 'eye', title: '판단의 주도권은 사람에게.', text: '자동 판단이 어려운 항목은 구분해 보여주고, 담당자가 최종 검토합니다.', tag: 'EVIDENCE → DECISION', Art: DecisionTile },
];

// 소개 화면은 API 요청 없이 렌더링하고, 실제 작업은 각 프로필의 진입 경로에 맡긴다.
export default function LandingPage({ profile = 'legacy' }) {
  const navigate = useNavigate();
  const resetWorkspace = useWorkspaceReset();
  const { hash, key, state } = useLocation();
  const auth = useOptionalAuth();
  const [logoutPending, setLogoutPending] = useState(false);
  const [example, setExample] = useState(1);
  const root = useRef(null);
  const title = useRef(null);
  const loginLink = useRef(null);
  const isV1 = profile === 'v1' || profile === 'rebuild';
  const sampleLabel = isV1 ? '프로젝트 둘러보기' : '샘플로 체험하기';
  const shown = examples[example];

  useEffect(() => {
    if (hash) {
      // 다른 페이지의 상단 메뉴에서 넘어와도 해당 안내 구역으로 이동한다.
      document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView();
      return;
    }
    window.scrollTo({ top: 0, behavior: 'instant' });
    (state?.focusLogin ? loginLink : title).current?.focus({ preventScroll: true });
  }, [hash, key, state]);

  // 구역이 화면에 들어올 때 한 번만 또렷해진다. 움직임을 줄인 환경에서는 처음부터 다 보인다.
  useEffect(() => {
    const page = root.current;
    if (!page) return undefined;
    const sections = [...page.querySelectorAll('[data-lp-reveal]')];
    const show = node => { node.dataset.lpShown = 'true'; };
    if (!('IntersectionObserver' in window) || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      sections.forEach(show);
      return undefined;
    }
    page.dataset.lpMotion = 'on';
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        show(entry.target);
        observer.unobserve(entry.target);
      }
    }, { threshold: 0.12 });
    sections.forEach(node => observer.observe(node));
    // 키보드로 먼저 도착한 구역은 기다리지 않고 보여 준다.
    const focused = event => { const section = event.target.closest?.('[data-lp-reveal]'); if (section) show(section); };
    page.addEventListener('focusin', focused);
    return () => {
      observer.disconnect();
      page.removeEventListener('focusin', focused);
      delete page.dataset.lpMotion;
    };
  }, []);

  function start() {
    if (isV1) navigate('/projects');
    else if (auth?.status === 'anonymous') navigate('/login');
    // 포스터인지 동영상인지 정하지 않고 시작하므로 자료 종류부터 고른다.
    // 새로 시작하는 것이므로 앞서 보던 검수(입력·결과)는 비운다. 비우지 않으면 저장된 결과를 보다 온 경우 그 결과가 다시 나온다.
    else { resetWorkspace(); navigate(mediaChoicePath, { state: mediaChoiceState }); }
  }
  // 샘플 체험은 로그인 없이 /sample에서 미리 만든 예시로 검수 흐름을 보여 준다(/review/new?sample=1의 예시 자료 불러오기는 그대로 둔다).
  function sample() { navigate(isV1 ? '/projects' : '/sample'); }
  function home() {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    title.current?.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: reduced ? 'instant' : 'smooth' });
  }
  // 예시 탭은 ←·→ 로 옮긴다.
  function moveExample(event, index) {
    if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
    event.preventDefault();
    const next = (index + (event.key === 'ArrowRight' ? 1 : -1) + examples.length) % examples.length;
    setExample(next);
    document.getElementById(`lp-tab-${next}`)?.focus();
  }

  return <section className="lp-page" ref={root} aria-label="서비스 소개">
    <a className="lp-skip" href="#lp-main">본문으로 이동</a>
    <SiteHeader onHome={home} onStart={start} loginRef={loginLink} onLogoutPending={setLogoutPending}
      afterLogout={() => { if (window.location.pathname === '/') navigate('/', { replace: true, state: { focusLogin: true } }); }} />
    <main id="lp-main" tabIndex={-1}>
      <section className="lp-entry-hero lp-container" aria-labelledby="lp-hero-title">
        <PixelField monochrome />
        <div className="lp-entry-copy">
          <p className="lp-entry-eyebrow"><span aria-hidden="true" />A workspace for your next creative</p>
          <h1 id="lp-hero-title" ref={title} tabIndex={-1}>CREATIVE<br /><span>CHECKER</span></h1>
          <p className="lp-entry-description">좋은 디자인의 마지막 확인.<br />포스터와 요구사항을 함께 놓고, 디테일까지 살펴보세요.</p>
          <div className="lp-entry-actions">
            <button type="button" className="lp-entry-start" disabled={logoutPending} onClick={start}>시작하기<Icon name="arrow" /></button>
            <button type="button" className="lp-text-link" disabled={logoutPending} onClick={sample}>{sampleLabel}</button>
          </div>
        </div>
        <a href="#lp-about" className="lp-entry-scroll">SCROLL TO DISCOVER <span aria-hidden="true">↓</span></a>
      </section>

      <section className="lp-about lp-container" id="lp-about" aria-labelledby="lp-about-title">
        <span className="lp-kicker">WHAT IS CREATIVE CHECKER?</span>
        <div className="lp-about-copy">
          <h2 id="lp-about-title">포스터와 요구사항,<br />같은 기준으로 확인하세요.</h2>
          <p>Creative Checker는 포스터가 제작 요구사항을 충족하는지 확인하는 검수 시스템입니다.<br /><br />PDF 문서나 직접 적은 조건을 기준으로 정리하고, 이미지에서 읽은 문구와 위치를 함께 보여줍니다. AI가 찾은 근거를 살펴보고, 최종 판단은 담당자가 내립니다.</p>
        </div>
        <div className="lp-formats" data-lp-reveal>
          <article className="lp-format">
            <div className="lp-format-top"><span className="lp-kicker">POSTER / 포스터 검수</span><span className="lp-format-legend">01 원본 · 02 읽은 자리 · 03 판정</span></div>
            <PosterLayers />
            <div className="lp-format-foot">
              <p>포스터 한 장을 세 겹으로 읽습니다. 올린 이미지 위에 문구와 로고를 읽은 자리를 표시하고, 기준과 대조한 판정을 그 위에 얹습니다.</p>
              <Link className="lp-text-link" to="/review/new">포스터 검수<Icon name="arrow" /></Link>
            </div>
          </article>
          <article className="lp-format">
            <div className="lp-format-top"><span className="lp-kicker">VIDEO / 동영상 검수</span><span className="lp-format-legend">01 장면 · 02 읽은 자리 · 03 판정</span></div>
            <VideoLayers />
            <div className="lp-format-foot">
              <p>영상은 장면을 따라 읽습니다. 자막과 로고가 나온 자리와 시각을 장면마다 표시하고, 같은 방식으로 판정합니다.</p>
              <Link className="lp-text-link" to="/video">동영상 검수<Icon name="arrow" /></Link>
            </div>
          </article>
        </div>
      </section>

      <section className="lp-demo-section" id="lp-demo" aria-labelledby="lp-demo-title">
        <div className="lp-container">
          <div className="lp-section-top"><span className="lp-kicker">01 / SEE THE DIFFERENCE</span><span className="lp-example-note">설명을 위한 예시 · 실제 검수 결과가 아닙니다.</span></div>
          <div className="lp-demo-heading" data-lp-reveal>
            <h2 id="lp-demo-title">결과만 말하지 않고,<br /><span>이유를 보여줍니다.</span></h2>
            <p>기준, 포스터, 관측한 내용을 한 화면에.<br />직접 비교하고 다음 작업을 결정하세요.</p>
          </div>
          <div className="lp-demo-grid">
            <figure className="lp-demo-image">
              <div className="lp-demo-poster-surface">
                <img src={demoPoster} alt="국립한글박물관 글(자)감(각): 쓰기와 도구 전시 포스터" width="1200" height="1200" loading="lazy" />
                <div className={`lp-demo-highlight${example === 1 ? ' is-active' : ''}`}><span>전시 기간</span></div>
              </div>
              <figcaption className="lp-image-corner">글(자)감(각): 쓰기와 도구 · <a href="https://www.griun.co.kr/fonts/hangeul-fter" target="_blank" rel="noreferrer">이미지 출처 ↗</a></figcaption>
            </figure>
            <div className="lp-demo-inspector">
              <div className="lp-demo-tabs" role="tablist" aria-label="검수 과정 예시">
                {examples.map((item, index) => <button key={item.kind} type="button" role="tab" id={`lp-tab-${index}`} aria-selected={example === index}
                  aria-controls="lp-example-panel" tabIndex={example === index ? 0 : -1} onClick={() => setExample(index)} onKeyDown={event => moveExample(event, index)}>
                  <span>0{index + 1}</span>{item.label}
                </button>)}
              </div>
              <div id="lp-example-panel" role="tabpanel" aria-labelledby={`lp-tab-${example}`} aria-live="polite">
                <span className="lp-inspector-tag">{shown.tag}</span>
                <h3>{shown.title}</h3>
                <p>{shown.description}</p>
                <dl>
                  <div><dt>검수 기준</dt><dd>{shown.expected}</dd></div>
                  <div><dt>{example === 1 ? '포스터에 적힌 내용' : '확인할 내용'}</dt><dd>{shown.actual}</dd></div>
                </dl>
                <div className={`lp-inspector-status is-${shown.kind}`}><Icon name={example === 1 ? 'check' : 'eye'} />{shown.status}</div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="lp-features lp-container" id="lp-features" aria-labelledby="lp-features-title">
        <div className="lp-section-top"><span className="lp-kicker">02 / BUILT FOR THE LAST MILE</span><span className="lp-section-index" aria-hidden="true">✳</span></div>
        <div className="lp-section-heading" data-lp-reveal>
          <h2 id="lp-features-title">좋은 작업에 필요한<br />명확한 마무리.</h2>
          <p>따로 보던 자료를 하나의 흐름으로 연결합니다.</p>
        </div>
        <div className="lp-feature-grid">{features.map(({ icon, title: heading, text, tag, Art }, index) => <article key={icon} data-lp-reveal>
          <div className="lp-feature-icon"><Icon name={icon} /><span>0{index + 1}</span></div>
          <div className="lp-feature-art" aria-hidden="true"><Suspense fallback={<div className="lp-feature-hold" />}><Art /></Suspense></div>
          <h3>{heading}</h3>
          <p>{text}</p>
          <span className="lp-feature-tag">{tag}</span>
        </article>)}</div>
      </section>

      <section className="lp-final-cta lp-container" aria-labelledby="lp-cta-title">
        <span className="lp-kicker">YOUR NEXT CREATIVE, CLEARER.</span>
        <h2 id="lp-cta-title">좋은 작업의<br />마지막 체크.</h2>
        <button type="button" className="lp-button lp-button-dark" disabled={logoutPending} onClick={start}>검수 시작하기<Icon name="arrow" /></button>
        <span className="lp-cta-note">포스터와 요구사항을 준비하고 검수를 시작하세요.</span>
      </section>
    </main>
  </section>;
}
