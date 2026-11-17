import React, { useEffect, useRef } from 'react';
import DocumentInput, { projectBlockReason } from './DocumentInput.jsx';
import RequirementInput from './RequirementInput.jsx';
import ReviewTypePicker, { reviewModeLabels, SecurityPicker } from './ReviewTypePicker.jsx';
import ImageStage from '../../components/ImageStage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import ExtractionFailure from './ExtractionFailure.jsx';
import { LogoInputs } from '../video/LogoFlowParts.jsx';
import { parseRequirements } from './workflow.js';
import '../../styles/review-editor-v2.css';
import '../../styles/review-type-picker.css';

// 포스터 검수의 자료 입력 화면. 문구·종합 검수는 이 한 화면에서 포스터, 직접 입력한 요구사항, 검수 항목, 요구사항 문서를 모두 다루고
// '검수 시작하기' 하나로 기준 승인과 검수 접수까지 간다. 기준만 따로 확인하는 화면으로 넘어가지 않는다.
// review(선택): 문서를 추출했거나 저장된 초안을 열었을 때 FrameworkReviewPage가 넘기는 검수 항목 칸과 시작 동작.
//   panel 검수 항목 목록·상세 · loading 기준을 불러오는 중 · included 검수에 포함된 기준 수 · block 시작할 수 없는 이유
//   extras 행동 줄에 덧붙일 내용(확인 필요 항목) · error 승인·접수 오류 · busy 승인·접수 처리 중 · start() 검수 시작
//   runId·projectId 이 초안의 실행과 프로젝트 · documentName 이 화면이 문서 이름을 기억하지 못할 때 쓸 이름
//   documentLock 문서를 바꾸거나 지울 수 없는 이유(저장해 둔 초안을 다시 연 경우)
export default function InputPage({ state, projectId, projectStatus, prepare, inputChange, sampleBusy, fileBusy,
  fileInput, selectFile, preview, dimensions, oversizeImage, copySource, useInspectionCopy, removeFile, dragging, setDragging, health, healthBusy, checkHealth, error, extractionFailure,
  documentBusy, documentExtracting = false, documentProgress, documentNotice, documentError, documentResumeId, documentInput, selectDocument, cancelDocument,
  clearDocument, documentCancelBusy, cancelQueuedDocument, previewMode = false, onChooseMedia, review = null }) {
  const doc = state.documentRun;
  // 직접 입력한 줄은 검수를 시작할 때 분석해 검수 항목에 더한다. 그 전에는 줄 수만 센다.
  const typedCount = new Set(parseRequirements(state.requirements).map(item => item.text)).size;
  const included = review?.included || 0;
  const requirementCount = included + typedCount;
  const uploadMiB = Math.floor((health?.limits?.upload_bytes || 20 * 1024 * 1024) / 1024 / 1024);
  const imagePixels = ((health?.limits?.image_pixels || 10_000_000) / 10_000).toLocaleString();
  const imageSide = (health?.limits?.image_side || 8192).toLocaleString();
  // 문서 추출은 서버에서 따로 돌아서, 추출하는 동안에도 포스터·로고·요구사항은 입력할 수 있다.
  // busy: 입력 잠금(검수 접수 중일 때만). locked: 시작·보안 여부·유형 바꾸기 잠금(문서 추출이 끝날 때까지).
  const submitting = (documentBusy && !documentExtracting) || Boolean(review?.busy);
  const busy = sampleBusy || fileBusy || submitting;
  const locked = busy || documentBusy;
  // 문서 첨부 카드는 문서 자신의 추출 상태만 보인다. 검수 시작(직접 입력 분석·기준 승인·접수)의 진행은 준비 창이 맡는다.
  // 시작하는 동안 카드는 추출을 마친 모습 그대로 잠가 둔다. 그 진행을 카드에 그리면 문서를 한 번 더 추출하는 것처럼 보인다.
  // 다시 열 실행이 지금 보고 있는 문서의 실행이면 같은 곳이라 '저장된 문서 분석 보기' 링크를 두지 않는다.
  const documentCard = submitting ? { documentBusy: false, documentProgress: null, documentError: '', documentResumeId: '' }
    : { documentBusy, documentProgress, documentError,
      documentResumeId: [doc?.id, review?.runId].includes(documentResumeId) ? '' : documentResumeId };
  // 보안 자료 여부 → 검수 유형 순서로 고른다. 검수 유형에 따라 받는 자료가 다르다: 자막은 요구사항, 로고는 원본 로고, 둘 다는 모두.
  const security = state.securityMode;
  const mode = state.reviewMode;
  const checksText = mode !== 'logo';
  const checksLogo = mode !== 'text';
  const logoCount = state.referenceLogos?.length || 0;
  // 초안을 열어 둔 화면은 그 실행의 프로젝트에 이미 묶여 있다.
  const projectReady = Boolean(review) || projectStatus?.ready;
  const ready = Boolean(state.file && (!checksText || (requirementCount && projectReady)) && (!checksLogo || logoCount));
  const readyText = mode === 'logo' ? `원본 로고 ${logoCount}개 · 포스터에서 로고 위치 후보를 찾습니다.`
    : [included && `검수 항목 ${included}개`, typedCount && `직접 입력 ${typedCount}개`, mode === 'both' && `원본 로고 ${logoCount}개`]
      .filter(Boolean).join(' · ') + ' · 이 기준으로 검수를 시작합니다.';
  // 빠진 자료가 하나뿐이면 그것만 짚어 준다.
  const missing = [!state.file && '검수할 포스터를 넣어 주세요.',
    checksText && !requirementCount && '요구사항을 입력하거나 요구사항 문서를 첨부해 주세요.',
    checksLogo && !logoCount && '대조할 원본 로고를 첨부해 주세요.',
    checksText && requirementCount && !projectReady && '위쪽에서 저장할 프로젝트를 골라 주세요.'].filter(Boolean);
  const missingText = missing.length === 1 ? missing[0]
    : mode === 'logo' ? '검수할 포스터와 원본 로고를 준비해 주세요.'
    : mode === 'both' ? '검수할 포스터와 요구사항, 원본 로고를 준비해 주세요.' : '검수할 포스터와 요구사항을 준비해 주세요.';
  const canCancelQueued = documentProgress?.stage === 'queued' && documentProgress.runId;
  const chooseFile = () => fileInput.current?.click();
  const pasteKey = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘V' : 'Ctrl+V';
  const screen = !security ? 'security' : !mode ? 'type' : mode;
  const shownScreen = useRef(screen);
  const startBlocked = locked || (!previewMode && !ready) || Boolean(review?.block);
  const start = review ? review.start : prepare;

  // 복사한 포스터 이미지를 붙여넣으면 바로 검수 이미지로 쓴다. 글자 붙여넣기는 그대로 둔다.
  useEffect(() => {
    if (previewMode || !mode || !security) return undefined;
    function paste(event) {
      const image = [...(event.clipboardData?.files || [])].find(file => file.type.startsWith('image/'));
      if (!image || busy) return;
      event.preventDefault();
      selectFile([image]);
    }
    window.addEventListener('paste', paste);
    return () => window.removeEventListener('paste', paste);
  }, [busy, mode, previewMode, security, selectFile]);

  // 보안 여부나 유형을 고르거나 바꾸면 새 화면의 제목으로 초점을 옮긴다.
  useEffect(() => {
    if (shownScreen.current === screen) return;
    shownScreen.current = screen;
    document.getElementById('page-title')?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [screen]);

  // '데이터 없이 화면 둘러보기'는 위쪽 검수 이름 칸 오른쪽에 둔다(ReviewPage → ReviewLayout nameAside).
  if (!security) return <section className="rv-input-page rv-choice-screen" aria-labelledby="page-title">
    <SecurityPicker disabled={locked} onSelect={value => inputChange('securityMode', value)} onBack={onChooseMedia} />
  </section>;

  if (!mode) return <section className="rv-input-page rv-choice-screen" aria-labelledby="page-title">
    <ReviewTypePicker disabled={locked} security={security} onSelect={value => inputChange('reviewMode', value)}
      onBack={() => inputChange('securityMode', null)} />
  </section>;

  // 큰 제목과 자료 구분·유형 줄은 두지 않고 위쪽 검수 이름 칸을 크게 보인다. 제목은 화면 읽기·초점 이동용으로만 남긴다.
  // 검수 항목 칸 안에 기준 보완 양식(form)이 들어가므로 바깥은 form으로 감싸지 않는다(form 안에 form을 둘 수 없다).
  return <section className="rv-input-page" aria-labelledby="page-title">
    <h1 id="page-title" className="sr-only" tabIndex={-1}>{reviewModeLabels[mode]} 자료 입력</h1>
    <div className={`rv-input-form rv-input-form--${mode}`}>
      <div className="rv-editor-grid">
        <div className="rv-input-canvas-column">
          <input type="file" ref={fileInput} accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" className="sr-only"
            aria-label="검수 이미지 선택" disabled={busy} onChange={event => { if (event.target.files.length) selectFile([...event.target.files]); }} />
          <div className={`rv-input-drop-target${dragging ? ' is-dragging' : ''}`}
            onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true); }}
            onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget)) setDragging(false); }}
            onDrop={event => { event.preventDefault(); setDragging(false); if (!busy) selectFile([...event.dataTransfer.files]); }}>
            <ImageStage src={preview} alt="업로드한 검수 이미지 미리보기" heading={false}
              width={dimensions?.width} height={dimensions?.height} showControls={false} bare={Boolean(preview)}
              empty={<div className="rv-input-empty"><span className="rv-input-empty-icon" aria-hidden="true"><Icon name="image" /></span>
                <h2>여기에 포스터를 놓아 주세요.</h2><p>파일을 끌어다 놓거나 버튼으로 선택하세요.</p>
                <button type="button" className="rv-button primary" disabled={busy} onClick={chooseFile}><Icon name="upload" />파일 선택</button>
                <span>JPG · PNG · WEBP · 최대 {uploadMiB}MiB<br />{imagePixels}만 픽셀 · 한 변 {imageSide}px 이하 · {pasteKey} 붙여넣기</span>
              </div>}>
              {fileBusy && <p className="rv-input-file-status" role="status">파일 정보를 읽고 있어요.</p>}
              {state.file && <div className="rv-input-filebar">
                <div className="rv-input-filemeta"><div><strong title={state.file.name}>{state.file.name}</strong>
                  {dimensions && <span>{dimensions.width}×{dimensions.height}</span>}</div></div>
                <div className="rv-input-fileactions">
                  <button type="button" className="rv-button secondary rv-compact" disabled={busy} onClick={chooseFile}>파일 바꾸기</button>
                  <button type="button" className="rv-button secondary rv-compact" disabled={busy} onClick={removeFile}>첨부 지우기</button>
                </div>
              </div>}
            </ImageStage>
          </div>
        </div>

        <aside className="rv-form-stack" aria-label="검수 요구사항과 첨부 자료">
          {oversizeImage && <div className="rv-form-block">
            <p>원본 {oversizeImage.dimensions.width}×{oversizeImage.dimensions.height}px. 원본은 보관하고 검수용 사본을 만들 수 있어요.</p>
            <button type="button" className="rv-button secondary" disabled={busy} onClick={useInspectionCopy}>검수용 사본으로 줄이기</button>
          </div>}
          {copySource && <p className="rv-form-hint">원본 {copySource.width}×{copySource.height}px. 결과와 승인은 축소 사본에 연결됩니다. 원본 규격과 작은 글자는 원본에서 확인해 주세요.</p>}
          {checksText && <RequirementInput id="requirements" value={state.requirements} disabled={submitting}
            onChange={value => inputChange('requirements', value)}
            placeholder={'예: 전시 제목에 ‘빛의 기록’을 표기해 주세요.\n전시 기간은 11.20–11.28로 표기해 주세요.'}>
            {/* 검수 항목: 직접 입력란 바로 아래, 문서 첨부 위(예전에 문서에서 추출한 요구사항 목록이 있던 자리).
                문서를 추출하면 그 초안의 기준이 이 상자에 나오고, 길면 쪽으로 넘겨 본다. 여기서 빼거나 보완한다.
                직접 입력한 요구사항은 시작할 때 분석해 이 목록에 더한다. 문서를 첨부하기 전에는 상자를 그리지 않는다. */}
            {(review?.panel || review?.loading) && <section className="rv-criteria-box rv-criteria-inspector" aria-label="검수 항목">
              {review.panel || <p className="rv-criteria-empty" role="status">검수 기준을 불러오고 있습니다.</p>}
            </section>}
          </RequirementInput>}
          {checksText && <DocumentInput {...documentCard} documentInput={documentInput}
            // 저장해 둔 초안을 다시 열면 문서 이름·쪽수를 기억하지 못한다. 추출을 마친 문서라는 것만 보이고 바꾸지 못하게 한다.
            documentNotice={review?.documentLock && review.documentName ? { summary: '추출 완료', warnings: [] } : documentNotice}
            clearDocument={review?.documentLock ? undefined : clearDocument}
            disabled={previewMode || sampleBusy || fileBusy || submitting || !projectReady || Boolean(review?.documentLock)}
            documentName={doc?.filename || review?.documentName}
            disabledReason={review?.documentLock ? review.documentLock
              : previewMode ? '데이터 없이 둘러보는 화면에서는 문서를 첨부할 수 없어요.'
              : sampleBusy ? '예시 자료를 불러오는 중이에요. 잠시 뒤 다시 눌러 주세요.'
              : fileBusy ? '포스터 파일을 확인하는 중이에요. 잠시 뒤 다시 눌러 주세요.'
              : submitting ? '검수를 준비하는 중이에요. 끝난 뒤 다시 눌러 주세요.' : projectBlockReason(projectStatus)}
            selectDocument={files => selectDocument(files, review?.projectId || projectId)}
            cancelDocument={canCancelQueued ? cancelQueuedDocument : cancelDocument} cancelBusy={documentCancelBusy}
            cancelLabel={canCancelQueued ? '대기 작업 취소' : '입력 반영 취소'}>
            {!previewMode && !projectReady && !projectStatus?.busy && <p className="rv-form-hint">상단에서 저장할 프로젝트를 선택하거나 만들어 주세요.</p>}
          </DocumentInput>}
          {checksLogo && <LogoInputs logos={state.referenceLogos} onChange={logos => inputChange('referenceLogos', logos)} disabled={busy} referenceOnly />}
        </aside>
      </div>
      <div className="rv-input-error"><ErrorMessage message={error} /><ExtractionFailure failure={extractionFailure} runId={extractionFailure?.runId} />{review?.error}</div>
      {checksText && health && !health.ready && <div className="rv-input-error" role="status">
        <p>검수 구성을 확인해 주세요. 기준 검토는 할 수 있지만 검수 실행에는 서버 연결이 필요합니다.</p>
        <button type="button" className="text-btn" disabled={busy || healthBusy} onClick={checkHealth}>검수 구성 다시 확인</button>
      </div>}
      <section className={`rv-actionbar rv-input-actionbar${review ? ' is-sticky' : ''}`} aria-label="검수 다음 단계">
        <div><p>{documentExtracting ? '문서를 분석하는 동안 다른 자료를 먼저 준비해 두세요. 분석이 끝나면 검수를 시작할 수 있어요.'
          : review?.block || (ready ? readyText : missingText)}</p>
          {checksText && typedCount > 0 && <p>직접 입력한 요구사항은 시작할 때 분석해 검수 항목에 더합니다.</p>}
          {review?.extras}</div>
        <button type="button" className="rv-button primary" disabled={startBlocked} onClick={start}>
          {mode === 'logo' && documentBusy ? '로고 검수 접수 중…' : '검수 시작하기'}<Icon name="arrow" /></button>
      </section>
    </div>
  </section>;
}
