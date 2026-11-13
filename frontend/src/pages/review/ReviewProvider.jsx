import React, { createContext, useCallback, useContext, useEffect, useReducer, useRef, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router';
import { fetchJson, fetchResponse } from '../../api/client.js';
import samplePoster from '../../assets/sample-poster.png';
import sampleLogo from '../../assets/sample-organizer-logo.png';
import { initialState, workflowReducer, canConfirm, canRun, logoDetectorFor, modelRouteFor } from './workflow.js';
import { combinedRequirementLines, mergeDocumentRequirements } from './documentRequirements.js';
import { addDirectRequirements, extractDocumentWithProgress, extractTextWithProgress, requirementDocument, DOCX_MEDIA_TYPE, watchDocumentDeadline } from './frameworkDocument.js';
import { frameworkJson, requirePosterReview } from './frameworkClient.js';
import { useWorkspaceReset } from '../../workspace/WorkspaceSession.jsx';
import { makeInspectionCopy } from './posterImageCopy.js';
import { submitPosterLogoJob } from './posterLogoFlow.js';

const ReviewContext = createContext(null);
const bundledSample = {
  project_name: '빛의 기록 · 전시 포스터',
  requirements: '전시 제목에 ‘빛의 기록’을 표시합니다.\n하단에 지정한 주최 로고를 사용합니다.\n전시 기간을 ‘11.20–11.28’로 표시합니다.',
};

// 업로드 제한과 실제 파일 서명을 확인하고 원본 이미지 크기를 반환한다.
async function validateImage(file, limits = {}) {
  const maxBytes = limits.upload_bytes || 20 * 1024 * 1024;
  const maxPixels = limits.image_pixels || 10_000_000;
  const maxSide = limits.image_side || 8192;
  if (!file) throw new Error('검수할 이미지 1개를 선택해 주세요.');
  if (file.size > maxBytes) throw new Error(`이미지는 ${Math.floor(maxBytes / 1024 / 1024)}MiB 이하로 올려 주세요.`);
  const bytes = new Uint8Array(await file.slice(0, 12).arrayBuffer());
  const isPng = [137, 80, 78, 71, 13, 10, 26, 10].every((byte, index) => bytes[index] === byte);
  const isJpg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
  const isWebp = String.fromCharCode(...bytes.slice(0, 4)) === 'RIFF'
    && String.fromCharCode(...bytes.slice(8, 12)) === 'WEBP';
  if (!isPng && !isJpg && !isWebp) throw new Error('JPG, PNG, WEBP 이미지 파일만 사용할 수 있습니다. 확장자와 실제 내용을 확인해 주세요.');
  const decoded = await createImageBitmap(file).catch(() => {
    throw new Error('이미지를 열 수 없습니다. 손상되지 않은 파일을 선택해 주세요.');
  });
  const dimensions = { width: decoded.width, height: decoded.height };
  decoded.close();
  if (dimensions.width * dimensions.height > maxPixels || Math.max(dimensions.width, dimensions.height) > maxSide) {
    const error = new Error(`이미지는 ${(maxPixels / 10_000).toLocaleString()}만 픽셀 이하, 한 변 ${maxSide.toLocaleString()}px 이하로 줄여 주세요.`);
    error.dimensions = dimensions;
    throw error;
  }
  return dimensions;
}

// 같은 검수의 단계 이동에는 상태를 유지하고 상단 도구 이동에는 초기화한다.
export function ReviewProvider({ children }) {
  const [state, dispatch] = useReducer(workflowReducer, initialState);
  const [health, setHealth] = useState(null);
  const [healthBusy, setHealthBusy] = useState(false);
  const [sampleBusy, setSampleBusy] = useState(false);
  const [fileBusy, setFileBusy] = useState(false);
  const [documentBusy, setDocumentBusy] = useState(false);
  // documentBusy 가운데 요구사항 문서 추출 중인 경우. 추출은 서버에서 따로 돌아 포스터·로고·요구사항 입력은 막지 않는다.
  const [documentExtracting, setDocumentExtracting] = useState(false);
  const [documentCancelBusy, setDocumentCancelBusy] = useState(false);
  const [documentProgress, setDocumentProgress] = useState(null);
  const [documentNotice, setDocumentNotice] = useState('');
  const [documentResumeId, setDocumentResumeId] = useState('');
  const [oversizeImage, setOversizeImage] = useState(null);
  const [copySource, setCopySource] = useState(null);
  const [documentError, setDocumentError] = useState('');
  const [projectId, setProjectId] = useState('');
  const [inputRunId, setInputRunId] = useState(null);
  const [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
  const [pollRetry, setPollRetry] = useState(0);
  const [preview, setPreview] = useState('');
  const [dimensions, setDimensions] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [newCriterion, setNewCriterion] = useState('');
  const [newCategory, setNewCategory] = useState('information');
  const [filter, setFilter] = useState('all');
  const [activeEvidence, setActiveEvidence] = useState(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  const routerNavigate = useNavigate();
  const fileInput = useRef(null);
  const documentInput = useRef(null);
  const documentRequest = useRef(null);
  const documentAttempts = useRef(new Map());
  const documentCancellation = useRef(false);
  const currentState = useRef(state);
  currentState.current = state;
  const generation = useRef(0);
  const fileValidation = useRef(0);
  const healthRequest = useRef(null);
  const sampleRequest = useRef(null);
  useWorkspaceReset(() => {
    generation.current += 1;
    fileValidation.current += 1;
    healthRequest.current?.abort();
    sampleRequest.current?.abort();
    documentRequest.current?.abort();
  });
  const wantsSample = location.pathname.replace(/\/$/, '') === '/review/new' && searchParams.get('sample') === '1';
  const savedReviewId = searchParams.get('review');
  const busy = state.step === 'running' && ['queued', 'running', 'submitting'].includes(state.review?.status);
  const locked = Boolean(state.snapshot);
  const selected = state.criteria.filter(item => item.selected);
  const selectedInfo = selected.filter(item => item.type === 'information').length;
  const versionText = locked ? `v${state.snapshot.version} 확정` : state.version ? `재확정 필요 · 이전 v${state.version}` : '확정 전';

  function clearSavedReview() {
    setSearchParams(params => { const next = new URLSearchParams(params); next.delete('review'); return next; }, { replace: true });
  }

  // URL에 실행 ID만 보관하고, 새로고침 때 기준·원본·결과를 서버에서 복원한다.
  useEffect(() => {
    if (!savedReviewId || currentState.current.review?.id === savedReviewId) return;
    const controller = new AbortController();
    const epoch = ++generation.current;
    setFileBusy(true);
    async function restore() {
      try {
        const review = await fetchJson(`/api/reviews/${encodeURIComponent(savedReviewId)}`, { signal: controller.signal });
        const response = await fetchResponse(`/api/reviews/${encodeURIComponent(savedReviewId)}/original`, { signal: controller.signal });
        const blob = await response.blob();
        const format = review.image.actual_format.toLowerCase();
        const file = new File([blob], `검수원본.${format === 'jpeg' ? 'jpg' : format}`, { type: `image/${format}` });
        if (controller.signal.aborted || epoch !== generation.current) return;
        setDimensions({ width: review.image.width, height: review.image.height });
        dispatch({ type: 'restore', review, file });
        setError('');
      } catch (err) {
        if (!controller.signal.aborted) setError(`저장된 검수를 불러오지 못했습니다. ${err.message}`);
      } finally {
        if (!controller.signal.aborted) setFileBusy(false);
      }
    }
    restore();
    return () => controller.abort();
  }, [savedReviewId]);

  // 이전 연결 조회를 취소해 가장 최근 서버 상태만 반영한다.
  const checkHealth = useCallback(async () => {
    healthRequest.current?.abort();
    const controller = new AbortController();
    healthRequest.current = controller;
    setHealthBusy(true);
    try {
      const body = await frameworkJson('/health', { signal: controller.signal });
      if (!controller.signal.aborted) setHealth({ ...body, status: 'ok', ready: body.poster_review_ready === true });
    } catch {
      if (!controller.signal.aborted) setHealth({ status: 'offline', ready: false });
    } finally {
      if (!controller.signal.aborted) setHealthBusy(false);
    }
  }, []);

  // 새 입력이 시작되면 늦게 도착하는 샘플 응답이 초안을 덮어쓰지 못하게 한다.
  const invalidateSample = useCallback(() => {
    generation.current += 1;
    sampleRequest.current?.abort();
    sampleRequest.current = null;
    setSampleBusy(false);
  }, []);

  const cancelDocument = useCallback((options = {}) => {
    // 입력 반영만 중단한 경우 서버에 접수된 실행을 다시 열 수 있도록 ID를 남긴다.
    const resumeId = options?.preserveResume === false ? '' : documentResumeId || documentProgress?.runId || '';
    documentRequest.current?.abort();
    documentRequest.current = null;
    setDocumentBusy(false);
    setDocumentExtracting(false);
    setDocumentProgress(null);
    setDocumentNotice('');
    setDocumentResumeId(resumeId);
    setDocumentError('');
  }, [documentProgress, documentResumeId]);

  async function cancelQueuedDocument() {
    const controller = documentRequest.current;
    const runId = documentProgress?.runId;
    if (!controller || !runId || documentProgress.stage !== 'queued' || documentCancellation.current) return;
    documentCancellation.current = true; setDocumentCancelBusy(true); setDocumentError('');
    try {
      await frameworkJson(`/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST', signal: controller.signal });
      if (controller.signal.aborted || (documentRequest.current && documentRequest.current !== controller)) return;
      for (const [key, attempt] of documentAttempts.current) {
        if (attempt.runId !== runId) continue;
        if (attempt.body) documentAttempts.current.delete(key);
        else { delete attempt.runId; delete attempt.extractionKey; }
      }
      cancelDocument({ preserveResume: false });
      setDocumentNotice('대기 작업을 취소했습니다. 원본과 기록은 보존됩니다.');
    } catch (err) {
      if (!controller.signal.aborted) setDocumentError(err.message);
    } finally { documentCancellation.current = false; setDocumentCancelBusy(false); }
  }

  async function selectDocument(files, projectId) {
    cancelDocument({ preserveResume: false });
    invalidateSample();
    if (documentInput.current) documentInput.current.value = '';
    if (files.length !== 1) { setDocumentError('요구사항 문서(PDF·DOCX)는 한 번에 1개만 선택해 주세요.'); return; }
    if (!projectId) { setDocumentError('문서를 저장할 프로젝트를 선택해 주세요.'); return; }
    const file = files[0];
    if (!file.size || file.size > 10 * 1024 * 1024) { setDocumentError('내용이 있는 10MiB 이하 PDF 또는 DOCX를 선택해 주세요.'); return; }
    // 보안 여부에 따라 문서 분석(A02)을 OpenAI(일반 자료) 또는 RunPod(보안 자료)로 보낸다.
    const modelRoute = modelRouteFor(state.securityMode);
    const controller = new AbortController();
    documentRequest.current = controller;
    setDocumentBusy(true);
    setDocumentExtracting(true);
    setDocumentProgress({ percent: 0, stage: 'uploading', message: '문서를 업로드하고 있습니다.', startedAt: Date.now(), source: file.name });
    setDocumentNotice(`${file.name} · 분석이 끝나면 추출된 요구사항을 확인할 수 있습니다.`);
    const deadline = watchDocumentDeadline(controller);
    try {
      const requirementFile = await requirementDocument(file);
      if (controller.signal.aborted) return;
      const isDocx = requirementFile.type === DOCX_MEDIA_TYPE;
      const result = await extractDocumentWithProgress(requirementFile, { projectId, attempts: documentAttempts.current, signal: controller.signal,
        modelRoute, onAccepted: id => { deadline.runId = id; setDocumentResumeId(id); }, onProgress: update => {
        if (!controller.signal.aborted && documentRequest.current === controller) {
          const progress = deadline.update(update);
          setDocumentProgress(previous => ({ ...previous, ...progress }));
        }
      } });
      if (controller.signal.aborted || documentRequest.current !== controller) return;
      const { sources } = mergeDocumentRequirements('', result, file.name);
      const criterionIdsByText = new Map();
      for (const condition of result.conditions) {
        const text = condition.text.trim();
        const ids = Array.isArray(condition.criterion_ids)
          ? condition.criterion_ids.filter(id => typeof id === 'string' && id) : [];
        criterionIdsByText.set(text, [...new Set([...(criterionIdsByText.get(text) || []), ...ids])]);
      }
      // 추출한 항목은 처음부터 모두 검수에 포함한다. 뺄 항목만 화면에서 체크를 푼다.
      const rows = sources.map((row, index) => ({ ...row, id: index, originalText: row.text, selected: true,
        criterionIds: criterionIdsByText.get(row.text) || [] }));
      dispatch({ type: 'input', field: 'documentRun', value: { id: result.framework.run_id,
        originalCount: rows.length, rows, filename: file.name, projectId, modelRoute } });
      setDocumentProgress(null);
      setDocumentResumeId('');
      const unresolved = result.pending?.length ? ` · 미확정 ${result.pending.length}개` : '';
      setDocumentNotice(`추출을 완료했어요. ${isDocx ? 'Word 문서' : `${result.page_count}페이지`} · ${rows.length}개 추출${unresolved}${result.warnings?.length ? ` · ${result.warnings.join(' ')}` : ''}`);
    } catch (err) {
      if (documentRequest.current === controller && (!controller.signal.aborted || deadline.expired)) {
        setDocumentNotice('');
        setDocumentProgress(null);
        setDocumentResumeId(err.runId || deadline.runId);
        setDocumentError(deadline.expired ? '화면 대기 시간이 끝났습니다. 접수된 분석은 기존 실행에서 확인할 수 있어요.' : err.message);
      }
    } finally {
      deadline.stop();
      if (documentRequest.current === controller) { documentRequest.current = null; setDocumentBusy(false); setDocumentExtracting(false); }
    }
  }

  // 샘플 이미지와 요구사항을 함께 읽고 최신 요청일 때만 화면에 적용한다.
  const sample = useCallback(async () => {
    if (sampleRequest.current) return;
    cancelDocument({ preserveResume: false });
    const controller = new AbortController();
    sampleRequest.current = controller;
    const epoch = ++generation.current;
    fileValidation.current += 1;
    setSampleBusy(true);
    setFileBusy(false);
    setError('');
    try {
      const [image, logo] = await Promise.all([
        fetch(samplePoster, { signal: controller.signal }),
        fetch(sampleLogo, { signal: controller.signal }),
      ]);
      if (!image.ok || !logo.ok) throw new Error('예시 자료를 불러오지 못했습니다. 다시 시도해 주세요.');
      const [posterBlob, logoBlob] = await Promise.all([image.blob(), logo.blob()]);
      const file = new File([posterBlob], '빛의 기록_포스터.png', { type: 'image/png' });
      const referenceLogos = [{ file: new File([logoBlob], '주최 로고.png', { type: 'image/png' }), text: '' }];
      const dims = await validateImage(file, health?.limits);
      if (controller.signal.aborted || epoch !== generation.current) return;
      dispatch({ type: 'sample', file, referenceLogos, projectName: bundledSample.project_name, requirements: bundledSample.requirements });
      setSearchParams(params => { const next = new URLSearchParams(params); next.delete('review'); return next; }, { replace: true });
      setDimensions(dims);
    } catch (err) {
      if (!controller.signal.aborted && epoch === generation.current) setError(err.message);
    } finally {
      if (sampleRequest.current === controller) {
        sampleRequest.current = null;
        setSampleBusy(false);
      }
    }
  }, [health?.limits, cancelDocument, setSearchParams]);

  // 상단 메뉴 이동·로그아웃·계정 변경으로 해제되면 요청과 파일 검증을 무효화한다.
  useEffect(() => {
    checkHealth();
    return () => {
      generation.current += 1;
      fileValidation.current += 1;
      healthRequest.current?.abort();
      sampleRequest.current?.abort();
      documentRequest.current?.abort();
      sampleRequest.current = null;
    };
  }, [checkHealth]);

  // 쿼리를 소비한 뒤 샘플을 한 번만 읽으며 개발 모드의 효과 재실행도 취소한다.
  useEffect(() => {
    if (!wantsSample) return;
    const timer = setTimeout(() => {
      setSearchParams(params => {
        const next = new URLSearchParams(params);
        next.delete('sample');
        return next;
      }, { replace: true });
      sample();
    }, 0);
    return () => clearTimeout(timer);
  }, [wantsSample, sample, setSearchParams]);

  // 선택 파일이 바뀔 때만 미리보기 URL을 생성하고 사용이 끝나면 해제한다.
  useEffect(() => {
    if (!state.file) { setPreview(''); return; }
    const url = URL.createObjectURL(state.file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [state.file]);

  // 동일한 실행을 조회하며 통신 실패 시 사용자가 다시 조회할 수 있게 한다.
  useEffect(() => {
    const id = state.review?.id;
    if (!id || state.step !== 'running' || state.review.status === 'failed') return;
    const epoch = generation.current;
    const controller = new AbortController();
    let timer;
    // 화면을 떠났거나 초안이 교체된 경우 응답과 추가 조회를 폐기한다.
    async function poll() {
      try {
        const review = await fetchJson(`/api/reviews/${encodeURIComponent(id)}`, { signal: controller.signal });
        if (controller.signal.aborted || epoch !== generation.current) return;
        setPollError('');
        dispatch({ type: 'progress', review });
        if (review.status === 'completed') { setFilter('all'); setActiveEvidence(null); }
        else if (review.status !== 'failed') timer = setTimeout(poll, 1200);
      } catch (err) {
        if (!controller.signal.aborted && epoch === generation.current) setPollError(`진행 상태를 가져오지 못했습니다. ${err.message}`);
      }
    }
    poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [state.review?.id, state.step, pollRetry]);

  // 초안 입력이 바뀌면 이전 기준 확정과 샘플 요청을 무효화한다.
  function inputChange(field, value) {
    clearSavedReview();
    invalidateSample();
    setError('');
    dispatch({ type: 'input', field, value });
  }

  function clearDocument() {
    cancelDocument({ preserveResume: false });
    inputChange('documentRun', null);
  }

  // 가장 마지막에 선택한 파일만 반영하고 오래된 검증 오류는 표시하지 않는다.
  async function selectFile(files) {
    invalidateSample();
    const request = ++fileValidation.current;
    setError('');
    setOversizeImage(null);
    if (fileInput.current) fileInput.current.value = '';
    if (files.length !== 1) { setFileBusy(false); setError('이미지는 한 번에 1개만 선택해 주세요.'); return; }
    setFileBusy(true);
    try {
      const dims = await validateImage(files[0], health?.limits);
      if (request !== fileValidation.current) return;
      dispatch({ type: 'input', field: 'file', value: files[0] });
      setDimensions(dims);
      setCopySource(null);
    } catch (err) {
      if (request === fileValidation.current) {
        setError(err.message);
        if (err.dimensions) setOversizeImage({ file: files[0], dimensions: err.dimensions });
      }
    } finally {
      if (request === fileValidation.current) setFileBusy(false);
    }
  }

  async function useInspectionCopy() {
    if (!oversizeImage) return;
    const request = ++fileValidation.current;
    setError('');
    setFileBusy(true);
    try {
      const copy = await makeInspectionCopy(oversizeImage.file, health?.limits);
      const dims = await validateImage(copy, health?.limits);
      if (request !== fileValidation.current) return;
      dispatch({ type: 'input', field: 'file', value: copy });
      setDimensions(dims);
      setCopySource(oversizeImage.dimensions);
      setOversizeImage(null);
    } catch (failure) {
      if (request === fileValidation.current) setError(failure.message);
    } finally {
      if (request === fileValidation.current) setFileBusy(false);
    }
  }

  // 파일 삭제는 아직 끝나지 않은 이미지 검증도 함께 취소한다.
  function removeFile() {
    fileValidation.current += 1;
    setFileBusy(false);
    inputChange('file', null);
    setDimensions(null);
    setOversizeImage(null);
    setCopySource(null);
  }

  function openDocumentRun() {
    navigateToDocumentRun();
  }

  function navigateToDocumentRun(criterionIds = null, reviewStarting = false) {
    if (documentRequest.current || sampleBusy || fileBusy) return;
    setError('');
    if (!state.documentRun) return;
    if (projectId !== state.documentRun.projectId) {
      setError('이 원문을 저장한 프로젝트를 선택해 주세요. 다른 프로젝트에서는 원문을 새로 등록해야 합니다.');
      return;
    }
    if ((state.documentRun.modelRoute || 'default') !== modelRouteFor(state.securityMode)) {
      setError('문서를 분석한 뒤 보안 여부가 바뀌었어요. 요구사항 문서를 다시 첨부해 주세요.');
      return;
    }
    // Reviewing an extracted draft does not require the poster model to be ready.
    // Registration and review execution still perform their readiness checks.
    setInputRunId(state.documentRun.id);
    const runId = state.documentRun.id;
    setSearchParams({ framework: runId }, {
      state: {
        reviewStarting,
        ...(criterionIds ? { pdfSelection: { runId, criterionIds } } : {}),
      },
    });
  }

  function prepare(event) {
    event?.preventDefault();
    if (sampleBusy || fileBusy || documentBusy) return;
    setError('');
    if (!state.file) { setError('검수할 이미지를 먼저 선택해 주세요.'); return; }
    const mode = state.reviewMode;
    if ((mode === 'logo' || mode === 'both') && !state.referenceLogos?.length) { setError('대조할 원본 로고를 1개 이상 첨부해 주세요.'); return; }
    if (mode === 'logo') { startLogoReview(); return; }
    const direct = state.requirements.trim();
    const doc = state.documentRun;
    const selectedRows = doc?.rows.filter(row => row.selected && row.text.trim()) || [];
    if (!direct && selectedRows.length && selectedRows.every(row => row.text.trim() === row.originalText)) {
      if (doc.originalCount === selectedRows.length) {
        navigateToDocumentRun(null, true);
      } else if (selectedRows.every(row => row.criterionIds?.length)) {
        navigateToDocumentRun([...new Set(selectedRows.flatMap(row => row.criterionIds))], true);
      } else {
        setError('선택한 항목의 원문 연결을 확인할 수 없습니다. PDF 원문과 기준 확인에서 항목을 선택해 주세요.');
      }
      return;
    }
    if (selectedRows.length && projectId !== doc.projectId) {
      setError('첨부한 문서를 저장한 프로젝트를 선택해 주세요.');
      return;
    }
    if (selectedRows.length && (doc.modelRoute || 'default') !== modelRouteFor(state.securityMode)) {
      setError('문서를 분석한 뒤 보안 여부가 바뀌었어요. 요구사항 문서를 다시 첨부해 주세요.');
      return;
    }
    if (direct && selectedRows.length && selectedRows.every(row => row.text.trim() === row.originalText)) {
      if (direct.length > 6000 || direct.split(/\r?\n/).filter(line => line.trim()).length > 100) {
        setError('직접 입력한 요구사항을 6,000자 및 100개 이내로 정리해 주세요.');
      } else if (doc.originalCount !== selectedRows.length && !selectedRows.every(row => row.criterionIds?.length)) {
        setError('선택한 항목의 원문 연결을 확인할 수 없습니다. PDF 원문과 기준 확인에서 항목을 선택해 주세요.');
      } else {
        const criterionIds = doc.originalCount === selectedRows.length
          ? null : [...new Set(selectedRows.flatMap(row => row.criterionIds))];
        startTextReview(direct, { runId: doc.id, criterionIds });
      }
      return;
    }
    // 자료 입력에서 문서 문구 자체를 바꾼 경우에는 바뀐 문구로 새 기준 초안을 만든다.
    const lines = combinedRequirementLines(direct, selectedRows);
    const requirements = lines.join('\n');
    if (!requirements) { setError('요구사항을 입력하거나 문서에서 추출한 항목을 선택해 주세요.'); return; }
    if (requirements.length > 6000 || lines.length > 100) { setError('검수 요구사항을 6,000자 및 100개 이내로 정리해 주세요.'); return; }
    startTextReview(requirements);
  }

  // 로고만 검수: 요구사항 실행 없이 포스터와 원본 로고로 로고 작업을 만들고 위치 확정 화면으로 이동한다.
  async function startLogoReview() {
    if (documentRequest.current) return;
    const controller = new AbortController();
    documentRequest.current = controller;
    setDocumentBusy(true); setError('');
    try {
      const job = await submitPosterLogoJob({ poster: state.file, logos: state.referenceLogos,
        title: `${(state.projectName || '').trim() || '포스터'} 로고 검수`, detector: logoDetectorFor(state.securityMode),
        signal: controller.signal });
      if (controller.signal.aborted || documentRequest.current !== controller) return;
      if (!job?.id) throw new Error('로고 검수를 접수하지 못했습니다. 다시 시도해 주세요.');
      routerNavigate(`/review/logo?job=${encodeURIComponent(job.id)}`);
    } catch (err) {
      if (documentRequest.current === controller && !controller.signal.aborted) setError(err.message);
    } finally {
      if (documentRequest.current === controller) { documentRequest.current = null; setDocumentBusy(false); }
    }
  }

  async function startTextReview(text, addition = null) {
    if (documentRequest.current) return;
    if (!projectId) { setError('요구사항을 저장할 프로젝트를 선택해 주세요.'); return; }
    const controller = new AbortController();
    documentRequest.current = controller;
    setDocumentBusy(true); setDocumentError(''); setDocumentNotice(''); setDocumentResumeId(''); setError('');
    setDocumentProgress({ percent: 0, stage: 'preparing', message: '검수 준비 상태를 확인하고 있습니다.', startedAt: Date.now(), source: '직접 입력한 요구사항' });
    const deadline = watchDocumentDeadline(controller);
    const onProgress = update => {
      if (!controller.signal.aborted && documentRequest.current === controller) {
        const progress = deadline.update(update);
        setDocumentProgress(previous => ({ ...previous, ...progress }));
      }
    };
    try {
      await requirePosterReview({ signal: controller.signal, freeText: true });
      const options = { attempts: documentAttempts.current, signal: controller.signal, onProgress,
        onAccepted: id => { deadline.runId = id; setDocumentResumeId(addition?.runId || id); } };
      let runId = addition?.runId;
      if (addition) {
        await addDirectRequirements(runId, addition.criterionIds, text, options);
      } else {
        const result = await extractTextWithProgress(text, { ...options, projectId, modelRoute: modelRouteFor(state.securityMode) });
        runId = result.framework.run_id;
      }
      if (controller.signal.aborted || documentRequest.current !== controller) return;
      setInputRunId(runId);
      setDocumentProgress(null);
      setDocumentResumeId('');
      setSearchParams({ framework: runId }, { state: { reviewStarting: true } });
    } catch (err) {
      if (documentRequest.current === controller && (!controller.signal.aborted || deadline.expired)) {
        setDocumentProgress(null);
        setDocumentResumeId(addition?.runId || err.runId || deadline.runId);
        setError(deadline.expired ? '화면 대기 시간이 끝났습니다. 접수된 분석은 기존 실행에서 확인할 수 있어요.' : err.message);
      }
    } finally {
      deadline.stop();
      if (documentRequest.current === controller) { documentRequest.current = null; setDocumentBusy(false); }
    }
  }

  // 선택한 항목을 현재 샘플 서버에서 사용하는 고정 기준으로 확정한다.
  function confirm() {
    if (!canConfirm(state)) { setError('내용이 있는 검수 항목을 1개 이상 선택해 주세요.'); return; }
    setError('');
    dispatch({ type: 'confirm' });
  }

  // 확정을 풀어 기존 동작대로 기준을 다시 편집할 수 있게 한다.
  function unlock() {
    clearSavedReview();
    generation.current += 1;
    setError('');
    setPollError('');
    dispatch({ type: 'unlock' });
  }

  // 과거 결과에서 다시 검수해도 새 원문·기준 승인 흐름을 거친다.
  async function run() {
    if (!canRun(state) || documentBusy) return;
    dispatch({ type: 'navigate', step: 'input' });
    await startTextReview(state.snapshot.criteria.map(item => item.text).join('\n\n'));
  }

  // 직접 작성한 검수 항목을 최대 개수 내에서 추가한다.
  function addCriterion(event) {
    event.preventDefault();
    if (!newCriterion.trim()) return;
    if (state.criteria.length >= 100) { setError('검수 항목은 최대 100개까지 추가할 수 있습니다.'); return; }
    dispatch({ type: 'add', id: `custom-${crypto.randomUUID()}`, text: newCriterion, category: newCategory });
    setNewCriterion('');
    setError('');
  }

  // 기존 상태 전이 규칙을 통해 이동 가능한 검수 단계로 전환한다.
  function navigate(step) {
    if (sampleBusy || fileBusy || documentBusy) return;
    setError('');
    dispatch({ type: 'navigate', step });
  }

  // 조회 오류만 해제하고 동일한 실행의 진행 상태를 다시 확인한다.
  function retryPoll() { setPollError(''); setPollRetry(value => value + 1); }

  // 새 검수를 시작할 때 이전 요청과 화면 보조 상태를 모두 정리한다.
  function newReview() {
    clearSavedReview();
    invalidateSample();
    cancelDocument({ preserveResume: false });
    fileValidation.current += 1;
    documentAttempts.current.clear();
    dispatch({ type: 'reset' });
    setFileBusy(false);
    setError('');
    setPollError('');
    setDimensions(null);
    setOversizeImage(null);
    setCopySource(null);
    setDragging(false);
    setNewCriterion('');
    setNewCategory('information');
    setFilter('all');
    setActiveEvidence(null);
    setInputRunId(null);
  }

  // 결과 필터를 바꾸면 이전 항목의 이미지 근거 강조도 함께 지운다.
  function changeFilter(value) { setFilter(value); setActiveEvidence(null); }

  const value = {
    projectId, setProjectId, inputRunId, clearDocument,
    documentCancelBusy, cancelQueuedDocument,
    documentBusy, documentExtracting, documentProgress, documentNotice, documentError, documentResumeId, documentInput, selectDocument, cancelDocument,
    state, dispatch, health, healthBusy, sampleBusy, fileBusy, error, pollError, preview,
    dimensions, oversizeImage, copySource, useInspectionCopy, dragging, setDragging, newCriterion, setNewCriterion, newCategory, setNewCategory,
    filter, activeEvidence, setActiveEvidence, fileInput, busy, locked, selected, selectedInfo, versionText,
    checkHealth, sample, inputChange, selectFile, removeFile, prepare, confirm, unlock, run,
    addCriterion, navigate, retryPoll, newReview, changeFilter, openDocumentRun,
  };
  return <ReviewContext.Provider value={value}>{children}</ReviewContext.Provider>;
}

// 같은 검수의 단계 사이에서 공유하는 상태와 기능을 전달한다.
export function useReviewContext() {
  const value = useContext(ReviewContext);
  if (!value) throw new Error('useReviewContext는 ReviewProvider 안에서 사용해야 합니다.');
  return value;
}
