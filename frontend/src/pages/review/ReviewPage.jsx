import React, { useEffect, useState } from 'react';
import { useLocation, useSearchParams } from 'react-router';
import { Icon } from '../../components/Icon.jsx';
import FrameworkReviewPage from './FrameworkReviewPage.jsx';
import { DocumentProjectPicker } from './DocumentInput.jsx';
import { useReviewContext } from './ReviewProvider.jsx';
import ReviewLayout from '../../layouts/ReviewLayout.jsx';
import InputPage from './InputPage.jsx';
import CriteriaPage from './CriteriaPage.jsx';
import RunningPage from './RunningPage.jsx';
import ResultsPage from './ResultsPage.jsx';

// 공유된 검수 상태를 현재 단계에 맞는 화면으로 표시한다.
export default function ReviewPage() {
  const {
    projectId, setProjectId, inputRunId, clearDocument,
    documentCancelBusy, cancelQueuedDocument,
    documentBusy, documentProgress, documentNotice, documentError, documentInput, selectDocument, cancelDocument,
    state, dispatch, health, healthBusy, sampleBusy, fileBusy, error, pollError, preview,
    dimensions, dragging, setDragging, newCriterion, setNewCriterion, newCategory, setNewCategory,
    filter, activeEvidence, setActiveEvidence, fileInput, busy, locked, selected, selectedInfo, versionText,
    checkHealth, sample, inputChange, selectFile, removeFile, prepare, confirm, unlock, run,
    addCriterion, navigate, retryPoll, newReview, changeFilter, openDocumentRun,
  } = useReviewContext();
  const [projectStatus, setProjectStatus] = useState({ ready: false, busy: true });
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const runId = params.get('framework');

  // 단계 이동 후 제목으로 초점을 옮겨 키보드와 스크린 리더 사용을 돕는다.
  useEffect(() => {
    if (runId) return;
    document.getElementById('page-title')?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [state.step, runId]);

  if (runId) return <FrameworkReviewPage key={runId} runId={runId}
    projectName={state.projectName || location.state?.projectName}
    initialFile={runId === inputRunId ? state.file : null}
    referenceLogos={runId === inputRunId ? state.referenceLogos : null}
    initialCriterionIds={location.state?.pdfSelection?.runId === runId ? location.state.pdfSelection.criterionIds : null}
    onInput={() => setParams({})}
    openRun={id => setParams({ framework: id }, { state: location.state })} onNew={() => { newReview(); setParams({}); }} />;

  const input = state.step === 'input';
  return <ReviewLayout state={state} navigate={navigate} busy={busy || sampleBusy || fileBusy || documentBusy}
    projectEditor={state.step === 'input' ? { id: 'project-name', maxLength: 60, onChange: value => inputChange('projectName', value) } : undefined}
    projectTools={input && <DocumentProjectPicker {...{ projectId, setProjectId }} projectName={state.projectName}
      disabled={sampleBusy || fileBusy || documentBusy} onStatus={setProjectStatus} />}
    projectActions={input && <button type="button" className="rv-button secondary" onClick={sample} disabled={busy || sampleBusy || fileBusy || documentBusy}>
      {sampleBusy ? '불러오는 중…' : '예시 자료 불러오기'}<Icon name="arrow" /></button>}>
    {input && <InputPage {...{ state, projectId, projectStatus, prepare, openDocumentRun, inputChange, sampleBusy, fileBusy, fileInput, selectFile,
      preview, dimensions, removeFile, dragging, setDragging, health, healthBusy, checkHealth, error, documentBusy, documentProgress,
      documentNotice, documentError, documentInput, selectDocument, cancelDocument, clearDocument, documentCancelBusy, cancelQueuedDocument }} />}
    {state.step === 'criteria' && <CriteriaPage {...{ state, locked, selected, selectedInfo, versionText, unlock, dispatch, addCriterion, newCriterion, setNewCriterion, newCategory, setNewCategory, navigate, confirm, error, run, preview }} />}
    {state.step === 'running' && <RunningPage {...{ state, pollError, retryPoll, navigate, run, preview, dimensions }} />}
    {state.step === 'results' && state.review?.status === 'completed' && <ResultsPage
      review={state.review} filter={filter} setFilter={changeFilter} activeEvidence={activeEvidence}
      setActiveEvidence={setActiveEvidence} edit={() => navigate('criteria')} newReview={newReview} />}
  </ReviewLayout>;
}
