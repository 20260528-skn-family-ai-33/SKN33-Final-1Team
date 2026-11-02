import React from 'react';
import DocumentInput from './DocumentInput.jsx';
import ImageStage from '../../components/ImageStage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { parseRequirements } from '../../workflow.js';
import '../../styles/review-editor-v2.css';

// 기존 업로드·문서 처리 흐름을 큰 이미지 캔버스와 입력 inspector에 연결한다.
export default function InputPage({ state, prepare, inputChange, sample, sampleBusy, fileBusy, fileInput, selectFile, preview, dimensions, removeFile, dragging, setDragging, health, error, versionText, documentBusy, documentProgress, documentNotice, documentError, documentInput, selectDocument, cancelDocument }) {
  const requirementCount = parseRequirements(state.requirements).length;
  const uploadMiB = Math.floor((health?.limits?.upload_bytes || 20 * 1024 * 1024) / 1024 / 1024);
  const imagePixels = ((health?.limits?.image_pixels || 10_000_000) / 10_000).toLocaleString();
  const imageSide = (health?.limits?.image_side || 8192).toLocaleString();
  const busy = sampleBusy || fileBusy || documentBusy;
  const chooseFile = () => fileInput.current?.click();

  return <section className="rv-input-page" aria-labelledby="page-title">
    <div className="rv-workspace-intro"><div><span className="rv-eyebrow">01 / 자료 등록</span><h1 id="page-title" tabIndex={-1}>어떤 광고물을 검수할까요?</h1><p>포스터를 펼쳐 놓고, 확인할 내용을 준비하세요.</p></div><button type="button" className="rv-button secondary" onClick={sample} disabled={busy}>{sampleBusy ? '불러오는 중…' : '샘플 불러오기'}<Icon name="arrow" /></button></div>
    <form className="rv-input-form" onSubmit={prepare}>
      <div className="rv-editor-grid">
        <div className="rv-input-canvas-column">
          <input type="file" ref={fileInput} accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" className="sr-only" aria-label="검수 이미지 선택" onChange={e => selectFile([...e.target.files])} />
          <div className={`rv-input-drop-target${dragging ? ' is-dragging' : ''}`} onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget)) setDragging(false); }} onDrop={e => { e.preventDefault(); setDragging(false); selectFile([...e.dataTransfer.files]); }}>
            <ImageStage src={preview} alt="업로드한 검수 이미지 미리보기" title="2. 검수할 이미지" subtitle={state.file ? state.file.name : '이미지 · 포스터 1개'} width={dimensions?.width} height={dimensions?.height} empty={<div className="rv-input-empty"><div className="rv-input-empty-icon"><Icon name="image" /></div><span className="rv-eyebrow">YOUR NEXT CREATIVE</span><h2>여기에 포스터를 놓아주세요.</h2><p>이미지를 끌어다 놓거나<br />파일을 선택해 시작하세요.</p><button type="button" className="rv-button primary" onClick={chooseFile}><Icon name="upload" />파일 선택</button><span>JPG, PNG, WEBP · 최대 {uploadMiB}MiB</span></div>} />
          </div>
          <div className="rv-input-filebar">
            <div className="rv-input-filemeta"><Icon name={state.file ? 'file' : 'info'} /><div><strong>{state.file ? state.file.name : '원본 이미지로 준비해 주세요.'}</strong><span>{state.file ? `${dimensions ? `${dimensions.width} × ${dimensions.height}px · ` : ''}${(state.file.size / 1024 / 1024).toFixed(2)}MB` : `${imagePixels}만 픽셀 · 한 변 ${imageSide}px 이하`}</span></div></div>
            {state.file && <div className="rv-input-fileactions"><button type="button" onClick={chooseFile}>다른 이미지 선택</button><button type="button" onClick={removeFile}>삭제</button></div>}
          </div>
          {state.file && <p className="rv-input-limits">JPG, PNG, WEBP · 최대 {uploadMiB}MiB · {imagePixels}만 픽셀 · 한 변 {imageSide}px 이하</p>}
          {fileBusy && <p className="rv-input-file-status" role="status">이미지를 확인하고 있어요.</p>}
        </div>

        <aside className="rv-panel rv-input-inspector" aria-label="프로젝트와 검수 요구사항">
          <div className="rv-panel-heading"><div><span className="rv-eyebrow">REVIEW BRIEF</span><h2>무엇을 확인할까요?</h2></div><span className="rv-input-count">{requirementCount}개 항목</span></div>
          <div className="rv-input-fields">
            <div className="rv-input-field"><label htmlFor="project-name">1. 프로젝트 이름 <span>선택</span></label><input id="project-name" value={state.projectName} onChange={e => inputChange('projectName', e.target.value)} maxLength={60} placeholder="예: 김천김밥축제 포스터 검수" autoComplete="off" /></div>
            <div className="rv-input-field rv-input-requirements"><div className="rv-input-field-heading"><label htmlFor="requirements">3. 검수 요구사항 <b>*</b></label><span>한 줄에 한 항목</span></div><p className="rv-input-writing-hint">행사명·기간·장소, 필수 문구와 시각 조건을 구체적으로 적어 주세요.</p><textarea id="requirements" disabled={documentBusy} value={state.requirements} maxLength={6000} rows={8} onChange={e => inputChange('requirements', e.target.value)} placeholder={'행사명: 김천김밥축제\n기간: 2026.10.23 ~ 10.25\n장소: 직지문화공원\n김천시 로고가 포함되어야 합니다.'} aria-describedby="requirements-hint" /><div className="rv-input-counter"><span id="requirements-hint">다음 단계에서 항목을 수정하거나 제외할 수 있어요.</span><span>{state.requirements.length.toLocaleString()} / 6,000</span></div></div>
            <div className="rv-input-document"><DocumentInput {...{ documentBusy, documentProgress, documentNotice, documentError, documentInput, selectDocument, cancelDocument, sampleBusy }} /></div>
          </div>
        </aside>
      </div>
      <div className="rv-input-error"><ErrorMessage message={error} /></div>
      <div className="rv-actionbar rv-input-actionbar"><div className="rv-input-next-note"><Icon name="lock" /><div><strong>자료 준비 후, 검수 기준을 확인합니다.</strong><p>{requirementCount}개 입력 · {versionText} · 기준을 확정한 뒤 검수가 실행됩니다.</p></div></div><button type="submit" className="rv-button primary" disabled={busy}>검수 항목 확인하기<Icon name="arrow" /></button></div>
    </form>
  </section>;
}
