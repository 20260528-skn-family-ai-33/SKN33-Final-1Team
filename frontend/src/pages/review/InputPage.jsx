import React, { useEffect, useState } from 'react';
import { Link } from 'react-router';
import DocumentInput from './DocumentInput.jsx';
import ImageStage from '../../components/ImageStage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { LogoInputs } from '../video/LogoFlowParts.jsx';
import { parseRequirements } from './workflow.js';
import '../../styles/review-editor-v2.css';

// 기존 업로드·문서 처리 흐름을 큰 이미지 캔버스와 입력 inspector에 연결한다.
export default function InputPage({ state, projectId, setProjectId, prepare, openDocumentRun, inputChange, sampleBusy, fileBusy, fileInput, selectFile, preview, dimensions, removeFile, dragging, setDragging, health, error, versionText, documentBusy, documentProgress, documentNotice, documentError, documentInput, selectDocument, cancelDocument, previewMode = false }) {
  const [referenceLogos, setReferenceLogos] = useState([]);
  const requirementCount = parseRequirements(state.requirements).length;
  const mixed = state.documentRun && state.requirements.trim() !== state.documentRun.paragraphs;
  const uploadMiB = Math.floor((health?.limits?.upload_bytes || 20 * 1024 * 1024) / 1024 / 1024);
  const imagePixels = ((health?.limits?.image_pixels || 10_000_000) / 10_000).toLocaleString();
  const imageSide = (health?.limits?.image_side || 8192).toLocaleString();
  const busy = sampleBusy || fileBusy || documentBusy;
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
          <input type="file" ref={fileInput} accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" className="sr-only" aria-label="검수 이미지 선택" onChange={e => selectFile([...e.target.files])} />
          <div className={`rv-input-drop-target${dragging ? ' is-dragging' : ''}`} onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget)) setDragging(false); }} onDrop={e => { e.preventDefault(); setDragging(false); selectFile([...e.dataTransfer.files]); }}>
            <ImageStage src={preview} alt="업로드한 검수 이미지 미리보기" title="검수할 포스터" subtitle={state.file ? state.file.name : '이미지 파일 1개'} width={dimensions?.width} height={dimensions?.height} empty={<div className="rv-input-empty"><span className="rv-input-empty-icon" aria-hidden="true"><Icon name="image" /></span><h2>{dragging ? '놓으면 바로 올라가요.' : '여기에 포스터를 놓아 주세요.'}</h2><p>이미지를 끌어다 놓거나 파일을 선택하세요.<br />복사한 이미지는 {pasteKey}로 붙여넣을 수 있어요.</p><button type="button" className="rv-button primary" disabled={busy} onClick={chooseFile}><Icon name="upload" />파일 선택</button><span>JPG, PNG, WEBP · 최대 {uploadMiB}MiB</span></div>} />
          </div>
          <div className="rv-input-filebar">
            <div className="rv-input-filemeta"><Icon name={state.file ? 'file' : 'info'} /><div><strong>{state.file ? state.file.name : '원본 이미지로 준비해 주세요.'}</strong><span>{state.file ? `${dimensions ? `${dimensions.width} × ${dimensions.height}px · ` : ''}${(state.file.size / 1024 / 1024).toFixed(2)}MB` : `${imagePixels}만 픽셀 · 한 변 ${imageSide}px 이하`}</span></div></div>
            {state.file && <div className="rv-input-fileactions"><button type="button" onClick={chooseFile}>다른 이미지 선택</button><button type="button" onClick={removeFile}>삭제</button></div>}
          </div>
          {state.file && <p className="rv-input-limits">JPG, PNG, WEBP · 최대 {uploadMiB}MiB · {imagePixels}만 픽셀 · 한 변 {imageSide}px 이하</p>}
          {fileBusy && <p className="rv-input-file-status" role="status">이미지를 확인하고 있어요.</p>}
        </div>

        <aside className="rv-panel rv-input-inspector" aria-label="프로젝트와 검수 요구사항">
          <div className="rv-panel-heading"><h2>검수 요구사항</h2><span className="rv-input-count">{requirementCount}개 항목</span></div>
          <div className="rv-input-fields">
            <div className="rv-input-field rv-input-requirements"><div className="rv-input-field-heading"><label htmlFor="requirements">검수 요구사항 <b>*</b></label><span>한 줄에 한 항목</span></div><p className="rv-input-writing-hint">행사명·기간·장소, 필수 문구와 시각 조건을 구체적으로 적어 주세요.</p><textarea id="requirements" disabled={documentBusy} value={state.requirements} maxLength={6000} rows={5} onChange={e => inputChange('requirements', e.target.value)} placeholder={'행사명: 김천김밥축제\n기간: 2026.10.23 ~ 10.25\n장소: 직지문화공원\n김천시 로고가 포함되어야 합니다.'} aria-describedby="requirements-hint" /><div className="rv-input-counter"><span id="requirements-hint">다음 단계에서 항목을 수정하거나 제외할 수 있어요.</span><span>{state.requirements.length.toLocaleString()} / 6,000</span></div></div>
            <div className="rv-input-document">{previewMode ? <p className="document-input">미리보기에서는 이미지·PDF·입력 없이 다음 화면으로 넘어갈 수 있습니다.</p> : <DocumentInput {...{ projectId, setProjectId, documentBusy, documentProgress, documentNotice, documentError, documentInput, selectDocument, cancelDocument, sampleBusy }} projectName={state.projectName} />}</div>
            {state.documentRun && !documentBusy && <div className="rv-document-ready" role="status">
              <strong><Icon name="check" />요구사항 분석을 완료했습니다.</strong>
              <p>{state.documentRun.filename}의 원문과 추출된 기준을 확인해 주세요. 미정 항목과 승인 여부도 다음 화면에서 검토합니다.</p>
              <button type="button" className="rv-button secondary" disabled={busy} onClick={openDocumentRun}>추출된 검수 기준 확인</button>
            </div>}
            <LogoInputs logos={referenceLogos} onChange={setReferenceLogos} disabled={busy} referenceOnly />
          </div>
        </aside>
      </div>
      <div className="rv-input-error"><ErrorMessage message={error} /></div>
      {mixed && <p role="note">원문 기준을 수정하려면 ‘{state.documentRun.filename} 원문 기준 승인하기’에서 기준 보완과 변경 출처를 기록하세요. ‘입력문으로 새 기준 분석’은 입력란 전체를 별도 원문으로 저장하며, 기존 원문의 승인과 출처를 승계하지 않습니다.</p>}
      <div className="rv-actionbar rv-input-actionbar"><div className="rv-input-next-note"><Icon name="lock" /><div><strong>자료 준비 후, 검수 기준을 확인합니다.</strong><p>{requirementCount}개 입력 · {versionText} · 기준을 확정한 뒤 검수가 실행됩니다.</p></div></div>{mixed && <button type="button" className="rv-button primary" disabled={busy} onClick={openDocumentRun}>{state.documentRun.filename} 원문 기준 승인하기</button>}
        <button type="submit" className={mixed ? 'rv-button secondary' : 'rv-button primary'} disabled={busy}>{mixed ? '입력문으로 새 기준 분석' : state.documentRun ? state.documentRun.kind === 'text' ? '입력 기준 확인하기' : 'PDF 검수 기준 확인하기' : '검수 항목 확인하기'}<Icon name="arrow" /></button></div>
    </form>
  </section>;
}
