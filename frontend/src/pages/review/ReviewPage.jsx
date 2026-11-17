import React, { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router';
import { Icon } from '../../components/Icon.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import FrameworkReviewPage from './FrameworkReviewPage.jsx';
import { DocumentProjectPicker } from './DocumentInput.jsx';
import { useReviewContext } from './ReviewProvider.jsx';
import ReviewLayout, { logoSteps } from '../../layouts/ReviewLayout.jsx';
import { MediaPicker } from './ReviewTypePicker.jsx';
import { mediaChoicePath, mediaChoiceState, wantsMediaChoice } from '../../workspace/mediaChoice.js';
import { logoDetectorFor } from './workflow.js';
import CriteriaPage from './CriteriaPage.jsx';
import RunningPage from './RunningPage.jsx';
import ResultsPage from './ResultsPage.jsx';

// 공유된 검수 상태를 현재 단계에 맞는 화면으로 표시한다.
export default function ReviewPage() {
  const {
    projectId, setProjectId, inputRunId, clearDocument,
    documentCancelBusy, cancelQueuedDocument,
    documentBusy, documentExtracting, documentProgress, documentNotice, documentError, documentResumeId, documentInput, selectDocument, cancelDocument,
    state, dispatch, health, healthBusy, sampleBusy, fileBusy, error, extractionFailure, pollError, preview,
    dimensions, oversizeImage, copySource, useInspectionCopy, dragging, setDragging, newCriterion, setNewCriterion, newCategory, setNewCategory,
    filter, activeEvidence, setActiveEvidence, fileInput, busy, locked, selected, selectedInfo, versionText,
    checkHealth, inputChange, selectFile, removeFile, prepare, confirm, unlock, run,
    addCriterion, navigate, retryPoll, newReview, changeFilter, addTyped,
  } = useReviewContext();
  const [projectStatus, setProjectStatus] = useState({ ready: false, busy: true });
  // 검수 이름 칸에서 Enter를 누르면 그 이름으로 저장할 프로젝트를 만들고 선택한다(DocumentProjectPicker.createFromName).
  const projectPicker = useRef(null);
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const go = useNavigate();
  const runId = params.get('framework');
  // 종류가 정해지지 않은 진입(로그인 직후 · 소개 화면의 시작하기 · 내 작업의 새 검수)은 자료 종류부터 고른다.
  const mediaChoice = !runId && wantsMediaChoice(location) && state.step === 'input' && !state.securityMode;

  // 단계 이동 후 제목으로 초점을 옮겨 키보드와 스크린 리더 사용을 돕는다.
  useEffect(() => {
    if (runId) return;
    document.getElementById('page-title')?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [state.step, runId, mediaChoice]);

  // 포스터를 고르면 같은 주소에서 고르기 표시만 지워 보안 여부로 넘어가고, 동영상을 고르면 동영상 검수 화면으로 간다.
  if (mediaChoice) return <ReviewLayout state={state} busy={false} steps={null} showWorkspaceBar={false} headerCurrent={null}>
    <section className="rv-input-page rv-media-choice" aria-labelledby="page-title">
      <MediaPicker onSelect={media => media === 'video' ? go('/video') : go(mediaChoicePath, { replace: true })} />
    </section>
  </ReviewLayout>;

  const input = state.step === 'input';
  // 자료 입력(보안 여부·유형 고르기 → 한 화면 입력)과 접수된 검수 한 건은 FrameworkReviewPage가 그린다.
  // 자료 입력은 한 화면이다: 문서를 추출해 초안이 생기면 주소에 실행 번호(framework)가 붙고, 같은 화면에 검수 항목이 나타난다.
  if (runId || input) {
    const submitting = input && documentBusy && !documentExtracting;
    const queued = documentProgress?.stage === 'queued' && documentProgress.runId;
    // 보안 여부·유형을 고르는 동안에는 검수 이름·프로젝트 줄과 단계 표시를 숨기고, 로고 검수는 로고 대조 단계를 보여 준다.
    const choosing = !state.securityMode || !state.reviewMode;
    // 이 화면에서 만든 초안(또는 아직 실행이 없음)인가. 주소로 다시 연 초안·접수된 검수는 아니다.
    const fresh = !runId || runId === inputRunId;
    const layout = {
      navigate, busy: busy || sampleBusy || fileBusy || documentBusy,
      steps: choosing ? null : state.reviewMode === 'logo' ? logoSteps : undefined,
      showWorkspaceBar: !choosing,
      // 검수 시작(직접 입력 분석·로고 접수)의 진행과 취소는 준비 창에 둔다.
      startMessage: submitting ? documentProgress?.message || '로고 검수를 접수하고 있어요.' : null,
      startActions: submitting && state.reviewMode !== 'logo' && <>
        <ErrorMessage message={documentError} />
        <button type="button" className="rv-button secondary" disabled={documentCancelBusy}
          onClick={queued ? cancelQueuedDocument : cancelDocument}>{queued ? '대기 작업 취소' : '입력 반영 취소'}</button>
      </>,
      // 초안이 생긴 뒤에는 그 실행의 프로젝트에 묶인다(Enter로 프로젝트를 새로 만들거나 다른 프로젝트로 바꾸지 않는다).
      projectEditor: { id: 'project-name', maxLength: 60, onChange: value => inputChange('projectName', value),
        onEnter: state.reviewMode === 'logo' || runId ? undefined : name => projectPicker.current?.createFromName(name) },
      // 개발 서버에서만 보이는 미리보기 입구. 저장할 프로젝트 칸 오른쪽에 둔다.
      projectAside: import.meta.env.DEV && <p className="rv-preview-entry"><Link to="/review/preview">데이터 없이 화면 둘러보기 ↗</Link></p>,
      projectTools: state.reviewMode === 'logo'
        // 로고만 검수는 요구사항 실행 없이 로고 작업만 접수하므로 프로젝트에 저장하지 않는다.
        ? <div className="rv-project-field"><span className="rv-project-field-badge">저장할 프로젝트</span>
          <span className="rv-project-none"><Icon name="folder" />로고 검수는 프로젝트 없이 진행돼요</span></div>
        : <DocumentProjectPicker ref={projectPicker} {...{ projectId, setProjectId }} projectName={state.projectName}
          disabled={sampleBusy || fileBusy || documentBusy || Boolean(runId)} onStatus={setProjectStatus}
          // 저장된 프로젝트를 고르면 그 이름을 검수 이름에도 넣는다(반대 방향: 검수 이름 Enter → 프로젝트 만들기).
          onPick={project => { if (project.name !== state.projectName) inputChange('projectName', project.name.slice(0, 60)); }} />,
    };
    const page = { state, projectId, projectStatus, prepare, inputChange, sampleBusy, fileBusy, fileInput, selectFile,
      preview, dimensions, oversizeImage, copySource, useInspectionCopy, removeFile, dragging, setDragging, health, healthBusy, checkHealth, error, extractionFailure,
      documentBusy, documentExtracting, documentProgress, documentNotice, documentError, documentResumeId, documentInput, selectDocument, cancelDocument,
      clearDocument, documentCancelBusy, cancelQueuedDocument,
      onChooseMedia: () => go(mediaChoicePath, { replace: true, state: mediaChoiceState }) };
    // 자료 입력 동안(실행이 없거나 이 화면에서 만든 초안)은 같은 화면으로 남는다. 접수된 검수·다시 연 초안으로 가면 새 화면이다.
    return <FrameworkReviewPage key={fresh ? 'input' : runId} runId={runId}
      health={health}
      reviewStarting={location.state?.reviewStarting === true}
      projectName={state.projectName || location.state?.projectName}
      initialFile={runId && runId === inputRunId ? state.file : null}
      referenceLogos={fresh && state.reviewMode !== 'text' ? state.referenceLogos : null}
      logoDetector={fresh ? logoDetectorFor(state.securityMode) : 'runpod'}
      input={{ page, layout, file: state.file, typed: state.requirements.trim(), addTyped, fresh,
        autoStart: fresh && location.state?.autoStart === true, setProject: setProjectId }}
      onInput={clearDocument}
      openRun={(id, reviewStarting = false) => setParams({ framework: id }, { state: { ...location.state, reviewStarting, autoStart: false } })}
      onNew={() => { newReview(); setParams({}); }} />;
  }

  // 아래는 저장된 예전 검수를 다시 열었을 때의 화면이다(?review=).
  return <ReviewLayout state={state} navigate={navigate} busy={busy || sampleBusy || fileBusy || documentBusy}>
    {state.step === 'criteria' && <CriteriaPage {...{ state, locked, selected, selectedInfo, versionText, unlock, dispatch, addCriterion, newCriterion, setNewCriterion, newCategory, setNewCategory, navigate, confirm, error, run, preview }} />}
    {state.step === 'running' && <RunningPage {...{ state, pollError, retryPoll, navigate, run, preview, dimensions }} />}
    {state.step === 'results' && state.review?.status === 'completed' && <ResultsPage
      review={state.review} filter={filter} setFilter={changeFilter} activeEvidence={activeEvidence}
      setActiveEvidence={setActiveEvidence} edit={() => navigate('criteria')} newReview={newReview} />}
  </ReviewLayout>;
}
