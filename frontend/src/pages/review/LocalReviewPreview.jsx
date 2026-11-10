import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router';
import ReviewLayout from '../../layouts/ReviewLayout.jsx';
import InputPage from './InputPage.jsx';
import CriteriaPage from './CriteriaPage.jsx';
import ResultsPage from './ResultsPage.jsx';
import ReviewProgress from './ReviewProgress.jsx';
import { initialState, parseRequirements, workflowReducer } from './workflow.js';
import { frameworkResults } from './frameworkResults.js';
import { frameworkProgress } from './frameworkProgress.js';
import { labels } from './reviewConstants.js';
import '../../styles/review-preview.css';

// This module is lazy-loaded only by Vite DEV. It never submits a real review.
const steps = Object.entries(labels);
const brief = '행사명: 봄의 기록\n개막일: 2026.05.01\n주최 로고가 표시되어야 합니다.';
const poster = `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1600" viewBox="0 0 1200 1600">
<rect width="1200" height="1600" fill="#174e4a"/><circle cx="950" cy="1000" r="490" fill="#e5ba79"/>
<rect x="140" y="720" width="640" height="700" fill="#ece8db" transform="rotate(-12 460 1070)"/>
<circle cx="460" cy="1090" r="190" fill="#b5573d"/><path d="M320 1090H600M460 950V1230" stroke="#ece8db" stroke-width="8"/>
<g fill="#f4f0df" font-family="sans-serif"><text x="100" y="130" font-size="30" letter-spacing="8">POSTER ARCHIVE / EXHIBITION</text>
<text x="90" y="350" font-size="125" font-weight="700">봄의 기록</text><text x="100" y="455" font-size="45">2026.05.02 — 05.04</text>
<text x="100" y="535" font-size="32">미리보기용 예시 포스터</text><text x="100" y="1510" font-size="26">LOCAL PREVIEW · 실제 의뢰 문서가 아닙니다.</text></g></svg>`;
const posterUrl = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(poster);
const criterionData = [
  ['title', '행사명: 봄의 기록', 'text', '봄의 기록', '봄의 기록', 'pass', [.07, .13, .86, .24]],
  ['date', '개막일: 2026.05.01', 'text', '2026.05.01', '2026.05.02', 'fail', [.07, .24, .84, .3]],
  ['logo', '주최 로고가 표시되어야 합니다.', 'visual', '주최 로고', '원본에서 확인 필요', 'review', null],
];
const exampleRun = {
  context: { run_id: 'local-preview-run', asset_version_id: 'local-preview-poster' },
  synthetic: true, execution_status: 'reviewing', baseline: {},
  criteria: { criteria: criterionData.map(([id, text, kind, expected]) => ({ criterion_id: id, text, kind, expected,
    required: true, source: { page: 1, quote: '화면 미리보기용 예시: ' + text } })) },
  validated: { items: criterionData.map(([id, text, kind, expected, observed, verdict, bbox]) => ({ criterion_id: id,
    criterion_text: text, kind, expected, observed, verdict,
    issues: verdict === 'pass' ? [] : [verdict === 'fail' ? '예시 판정: 개막일 표기가 기준과 다릅니다.' : '예시 판정: 로고를 직접 확인해 주세요.'],
    image: { asset_version_id: 'local-preview-poster', bbox } })) },
  step_records: [{ role: 'A04_A05', status: 'completed' }, { role: 'A06', status: 'completed' }, { role: 'A07', status: 'running' }],
};

// 진행 화면 미리보기에서 돋보기 위치를 단계별로 바꿔 볼 수 있게 서버 상태 예시를 둔다.
const done = role => ({ role, status: 'completed' }), running = role => ({ role, status: 'running' });
const progressStages = [
  ['queued', '접수 대기', { execution_status: 'queued', step_records: [] }],
  ['A04_A05', '포스터 검사', { step_records: [running('A04_A05')] }],
  ['A06', '근거 대조', { step_records: [done('A04_A05'), running('A06')] }],
  ['A07', '보고서 정리', { step_records: [done('A04_A05'), done('A06'), running('A07')] }],
];

function seed(state) {
  const requirements = state.requirements.trim() ? state.requirements : brief;
  return { ...state, projectName: state.projectName || '포스터 화면 미리보기', requirements,
    file: state.file || new File([poster], '미리보기_포스터.svg', { type: 'image/svg+xml' }),
    criteria: state.criteria.length ? state.criteria : parseRequirements(requirements) };
}

export default function LocalReviewPreview({ uiOnly = false }) {
  const [state, setState] = useState(initialState);
  const [preview, setPreview] = useState('');
  const [dragging, setDragging] = useState(false);
  const [newCriterion, setNewCriterion] = useState('');
  const [newCategory, setNewCategory] = useState('information');
  const [filter, setFilter] = useState('all');
  const [activeEvidence, setActiveEvidence] = useState(null);
  const [progressStage, setProgressStage] = useState('A07');
  const fileInput = useRef(null);
  const nextId = useRef(0);
  const result = useMemo(() => ({ ...frameworkResults(exampleRun, { url: posterUrl, width: 1200, height: 1600 }, '예시 포스터'),
    status_label: '화면 미리보기 · 예시 판정' }), []);
  const dispatch = action => setState(current => workflowReducer(current, action));
  const selected = state.criteria.filter(item => item.selected);
  const index = steps.findIndex(([step]) => step === state.step);
  useEffect(() => {
    if (!state.file) { setPreview(''); return; }
    const url = URL.createObjectURL(state.file); setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [state.file]);
  useEffect(() => { window.scrollTo({ top: 0 }); }, [state.step]);

  function navigate(step) {
    setState(current => {
      const next = step === 'input' ? current : seed(current);
      const snapshot = ['running', 'results'].includes(step) ? next.snapshot || {
        version: 1, criteria: next.criteria, projectName: next.projectName,
      } : next.snapshot;
      return { ...next, step, snapshot, version: snapshot?.version || next.version };
    });
  }
  function confirm() {
    setState(current => {
      const next = seed(current), version = next.version + 1;
      return { ...next, version, snapshot: { version, criteria: next.criteria.filter(item => item.selected), projectName: next.projectName } };
    });
  }
  function reset() { setState(initialState); setFilter('all'); setActiveEvidence(null); }
  const viewState = { ...state, review: state.step === 'running' ? { status: 'running' } : state.step === 'results' ? { status: 'completed' } : null };

  return <ReviewLayout state={viewState} busy={false} health={{ status: 'preview' }} navigate={navigate} previewMode>
    <aside className="rv-preview-toolbar" aria-label="로컬 화면 미리보기">
      <div><strong>{uiOnly ? 'UI 전용 · 서버 연결 없음' : '로컬 화면 미리보기'}</strong><p>데이터 없이 이동할 수 있습니다. 결과는 예시이며 실제 검수·승인 기록은 저장하지 않습니다.</p></div>
      <div className="rv-preview-controls">{steps.map(([step, label]) => <button type="button" key={step}
        aria-pressed={state.step === step} onClick={() => navigate(step)}>{label}</button>)}</div>
      <div className="rv-preview-shortcuts"><button type="button" className="rv-button primary" onClick={() => navigate(steps[(index + 1) % steps.length][0])}>{index === steps.length - 1 ? '처음 화면으로' : '다음 화면으로'} →</button>{!uiOnly && <Link to="/review/new">실제 검수로 돌아가기</Link>}</div>
    </aside>
    {state.step === 'input' && <InputPage state={state} previewMode prepare={event => { event?.preventDefault(); navigate('criteria'); }}
      inputChange={(field, value) => dispatch({ type: 'input', field, value })}
      sample={() => setState(seed(initialState))} fileInput={fileInput}
      selectFile={files => files[0] && dispatch({ type: 'input', field: 'file', value: files[0] })}
      removeFile={() => dispatch({ type: 'input', field: 'file', value: null })}
      preview={preview} dragging={dragging} setDragging={setDragging} versionText="화면 미리보기" />}
    {state.step === 'criteria' && <CriteriaPage state={state} locked={!!state.snapshot} selected={selected}
      selectedInfo={selected.filter(item => item.type === 'information').length} versionText="화면 미리보기"
      unlock={() => dispatch({ type: 'unlock' })} dispatch={dispatch} preview={preview}
      newCriterion={newCriterion} setNewCriterion={setNewCriterion} newCategory={newCategory} setNewCategory={setNewCategory}
      addCriterion={event => { event.preventDefault(); dispatch({ type: 'add', id: `preview-${++nextId.current}`, text: newCriterion, category: newCategory }); setNewCriterion(''); }}
      navigate={navigate} confirm={confirm} run={() => navigate('running')} />}
    {state.step === 'running' && <>
      <div className="rv-preview-scan-stages"><span>진행 단계 예시</span>
        <div className="rv-preview-controls" role="group" aria-label="진행 단계 예시">{progressStages.map(([key, label]) => <button type="button" key={key}
          aria-pressed={progressStage === key} onClick={() => setProgressStage(key)}>{label}</button>)}</div></div>
      <ReviewProgress {...frameworkProgress({ ...exampleRun, validated: null, execution_status: 'reviewing',
        ...progressStages.find(([key]) => key === progressStage)[2] })} detail={null} image={result.image}
        facts={[['검수 기준', `${criterionData.length}개`], ['포스터 크기', '1200×1600']]} title="검수 진행 화면 미리보기"
        helpText="예시 화면입니다. 상단의 다음 화면 또는 결과 확인을 눌러 바로 넘어가세요." />
    </>}
    {state.step === 'results' && <ResultsPage review={result} exportData={exampleRun} filter={filter} setFilter={setFilter}
      activeEvidence={activeEvidence} setActiveEvidence={setActiveEvidence}
      edit={() => setState(current => ({ ...current, step: 'criteria', snapshot: null }))} newReview={reset} />}
  </ReviewLayout>;
}
