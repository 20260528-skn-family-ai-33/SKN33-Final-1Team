import React, { useEffect } from 'react';
import { useSearchParams } from 'react-router';
import { Icon } from '../../components/Icon.jsx';
import FrameworkReviewPage from './FrameworkReviewPage.jsx';
import ReviewProgress from './ReviewProgress.jsx';
import { documentSteps } from './progressScan.js';
import { useReviewContext } from './ReviewProvider.jsx';
import ReviewLayout from '../../layouts/ReviewLayout.jsx';
import InputPage from './InputPage.jsx';
import CriteriaPage from './CriteriaPage.jsx';
import RunningPage from './RunningPage.jsx';
import ResultsPage from './ResultsPage.jsx';

// 공유된 검수 상태를 현재 단계에 맞는 화면으로 표시한다.
export default function ReviewPage() {
  const {
    projectId, setProjectId,
    documentCancelBusy, cancelQueuedDocument,
    documentBusy, documentProgress, documentNotice, documentError, documentInput, selectDocument, cancelDocument,
    state, dispatch, health, healthBusy, sampleBusy, fileBusy, error, pollError, preview,
    dimensions, dragging, setDragging, newCriterion, setNewCriterion, newCategory, setNewCategory,
    filter, activeEvidence, setActiveEvidence, fileInput, busy, locked, selected, selectedInfo, versionText,
    checkHealth, sample, inputChange, selectFile, removeFile, prepare, confirm, unlock, run,
    addCriterion, navigate, retryPoll, newReview, changeFilter, openDocumentRun,
  } = useReviewContext();

  // 단계 이동 후 제목으로 초점을 옮겨 키보드와 스크린 리더 사용을 돕는다.
  useEffect(() => {
    document.getElementById(documentBusy ? 'review-process-title' : 'page-title')?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [state.step, documentBusy]);

  const [params, setParams] = useSearchParams();
  const runId = params.get('framework');
  if (runId) return <FrameworkReviewPage key={runId} runId={runId}
    projectName={state.projectName}
    initialFile={runId === state.documentRun?.id ? state.file : null}
    openRun={id => setParams({ framework: id })} onNew={() => { newReview(); setParams({}); }} />;

  return <ReviewLayout {...{ state, health, healthBusy, checkHealth, navigate }} busy={busy || sampleBusy || fileBusy || documentBusy}
    projectEditor={state.step === 'input' ? { id: 'project-name', maxLength: 60, onChange: value => inputChange('projectName', value) } : undefined}
    projectActions={state.step === 'input' && <button type="button" className="rv-button secondary" onClick={sample} disabled={busy || sampleBusy || fileBusy || documentBusy}>
      {sampleBusy ? '불러오는 중…' : '예시 자료 불러오기'}<Icon name="arrow" /></button>}>
    {documentBusy && documentProgress && <ReviewProgress title="검수 요구사항을 정리하고 있어요."
      message={documentProgress.message} detail={documentNotice} percent={documentProgress.percent}
      steps={documentSteps(documentProgress.stage)} facts={documentProgress.source ? [['입력 자료', documentProgress.source]] : []}
      startedAt={documentProgress.startedAt} error={documentError} cancelBusy={documentCancelBusy}
      cancelLabel={documentProgress.stage === 'queued' && documentProgress.runId ? '대기 작업 취소' : '입력 반영 취소'}
      onCancel={documentProgress.stage === 'queued' && documentProgress.runId ? cancelQueuedDocument : cancelDocument} />}
    {state.step === 'input' && <div hidden={documentBusy && !!documentProgress}><InputPage {...{ state, projectId, setProjectId, prepare, openDocumentRun, inputChange, sampleBusy, fileBusy, fileInput, selectFile, preview, dimensions, removeFile, dragging, setDragging, health, error, versionText, documentBusy, documentProgress, documentNotice, documentError, documentInput, selectDocument, cancelDocument }} /></div>}
    {state.step === 'criteria' && <CriteriaPage {...{ state, locked, selected, selectedInfo, versionText, unlock, dispatch, addCriterion, newCriterion, setNewCriterion, newCategory, setNewCategory, navigate, confirm, error, run, preview }} />}
    {state.step === 'running' && <RunningPage {...{ state, pollError, retryPoll, navigate, run, preview, dimensions }} />}
    {state.step === 'results' && state.review?.status === 'completed' && <ResultsPage
      review={state.review} filter={filter} setFilter={changeFilter} activeEvidence={activeEvidence}
      setActiveEvidence={setActiveEvidence} edit={() => navigate('criteria')} newReview={newReview} />}
  </ReviewLayout>;
}
