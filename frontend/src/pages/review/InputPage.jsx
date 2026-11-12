import React, { useEffect } from 'react';
import { Link } from 'react-router';
import DocumentInput from './DocumentInput.jsx';
import RequirementInput, { ExtractedRequirements } from './RequirementInput.jsx';
import ImageStage from '../../components/ImageStage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { LogoInputs } from '../video/LogoFlowParts.jsx';
import { parseRequirements } from './workflow.js';
import '../../styles/review-editor-v2.css';

export default function InputPage({ state, projectId, projectStatus, prepare, openDocumentRun, inputChange, sampleBusy, fileBusy,
  fileInput, selectFile, preview, dimensions, oversizeImage, copySource, useInspectionCopy, removeFile, dragging, setDragging, health, healthBusy, checkHealth, error,
  documentBusy, documentProgress, documentNotice, documentError, documentResumeId, documentInput, selectDocument, cancelDocument,
  clearDocument, documentCancelBusy, cancelQueuedDocument, previewMode = false }) {
  const doc = state.documentRun;
  const selectedRows = doc?.rows.filter(row => row.selected && row.text.trim()) || [];
  const requirementCount = new Set([...parseRequirements(state.requirements).map(item => item.text), ...selectedRows.map(row => row.text.trim())]).size;
  const usesNewText = doc && selectedRows.length > 0 && (state.requirements.trim()
    || selectedRows.some(row => row.text.trim() !== row.originalText));
  const uploadMiB = Math.floor((health?.limits?.upload_bytes || 20 * 1024 * 1024) / 1024 / 1024);
  const imagePixels = ((health?.limits?.image_pixels || 10_000_000) / 10_000).toLocaleString();
  const imageSide = (health?.limits?.image_side || 8192).toLocaleString();
  const busy = sampleBusy || fileBusy || documentBusy;
  const ready = state.file && requirementCount && projectStatus?.ready;
  const canCancelQueued = documentProgress?.stage === 'queued' && documentProgress.runId;
  const chooseFile = () => fileInput.current?.click();
  const pasteKey = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘V' : 'Ctrl+V';

  // 복사한 포스터 이미지를 붙여넣으면 바로 검수 이미지로 쓴다. 글자 붙여넣기는 그대로 둔다.
  useEffect(() => {
    if (previewMode) return undefined;
    function paste(event) {
      const image = [...(event.clipboardData?.files || [])].find(file => file.type.startsWith('image/'));
      if (!image || busy) return;
      event.preventDefault();
      selectFile([image]);
    }
    window.addEventListener('paste', paste);
    return () => window.removeEventListener('paste', paste);
  }, [busy, previewMode, selectFile]);

  return <section className="rv-input-page" aria-labelledby="page-title">
    <h1 id="page-title" className="sr-only" tabIndex={-1}>어떤 광고물을 검수할까요?</h1>
    {import.meta.env.DEV && !previewMode && <p className="rv-preview-entry"><Link to="/review/preview">데이터 없이 화면 둘러보기 ↗</Link></p>}
    <form className="rv-input-form" onSubmit={prepare}>
      <div className="rv-editor-grid">
        <div className="rv-input-canvas-column">
          <input type="file" ref={fileInput} accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" className="sr-only"
            aria-label="검수 이미지 선택" disabled={busy} onChange={event => { if (event.target.files.length) selectFile([...event.target.files]); }} />
          <div className={`rv-input-drop-target${dragging ? ' is-dragging' : ''}`}
            onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true); }}
            onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget)) setDragging(false); }}
            onDrop={event => { event.preventDefault(); setDragging(false); if (!busy) selectFile([...event.dataTransfer.files]); }}>
            <ImageStage src={preview} alt="업로드한 검수 이미지 미리보기" title="검수할 포스터" subtitle="이미지 파일 1개"
              width={dimensions?.width} height={dimensions?.height} showControls={Boolean(preview)}
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
          <RequirementInput id="requirements" value={state.requirements} disabled={documentBusy}
            onChange={value => inputChange('requirements', value)}
            placeholder={'예: 전시 제목에 ‘빛의 기록’을 표기해 주세요.\n전시 기간은 11.20–11.28로 표기해 주세요.'}>
            {doc && <ExtractedRequirements rows={doc.rows} disabled={busy}
              onChange={rows => inputChange('documentRun', { ...doc, rows })} />}
          </RequirementInput>
          <DocumentInput {...{ documentBusy, documentProgress, documentNotice, documentError, documentResumeId, documentInput, clearDocument }}
            disabled={previewMode || sampleBusy || fileBusy || !projectStatus?.ready} documentName={doc?.filename}
            selectDocument={files => selectDocument(files, projectId)}
            cancelDocument={canCancelQueued ? cancelQueuedDocument : cancelDocument} cancelBusy={documentCancelBusy}
            cancelLabel={canCancelQueued ? '대기 작업 취소' : '입력 반영 취소'}>
            {doc && !documentBusy && <button type="button" className="rv-button secondary rv-compact rv-document-source"
              disabled={busy} onClick={openDocumentRun}>{/\.docx$/i.test(doc.filename || '') ? 'DOCX 본문과 기준 확인' : 'PDF 원문과 기준 확인'}</button>}
            {!previewMode && !projectStatus?.ready && !projectStatus?.busy && <p className="rv-form-hint">상단에서 저장할 프로젝트를 선택하거나 만들어 주세요.</p>}
          </DocumentInput>
          <LogoInputs logos={state.referenceLogos} onChange={logos => inputChange('referenceLogos', logos)} disabled={busy} referenceOnly />
        </aside>
      </div>
      <div className="rv-input-error"><ErrorMessage message={error} /></div>
      {health && !health.ready && <div className="rv-input-error" role="status">
        <p>검수 구성을 확인해 주세요. 기준 검토는 할 수 있지만 검수 실행에는 서버 연결이 필요합니다.</p>
        <button type="button" className="text-btn" disabled={busy || healthBusy} onClick={checkHealth}>검수 구성 다시 확인</button>
      </div>}
      <div className="rv-actionbar rv-input-actionbar">
        <div><p>{ready ? `검수 대상 ${requirementCount}개 · 기준 확인 후 검수를 시작합니다.` : '검수할 파일과 요구사항을 준비해 주세요.'}</p>
          {usesNewText && <p>선택·수정한 문구는 새 기준 초안으로 분석합니다. PDF 원문은 첨부 카드에서 확인할 수 있어요.</p>}</div>
        <button type="submit" className="rv-button primary" disabled={busy || (!previewMode && !ready)}>검수 시작하기<Icon name="arrow" /></button>
      </div>
    </form>
  </section>;
}
