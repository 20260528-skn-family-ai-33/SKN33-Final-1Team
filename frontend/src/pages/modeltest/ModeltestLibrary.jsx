import React, { useEffect, useRef, useState } from 'react';
import { sshRequest } from './modeltest.js';
import { MODEL_STATUS } from './modeltestCatalog.js';
import ModeltestHelp from './ModeltestHelp.jsx';

const base = '/api/modeltest/ssh/models';
const pretty = value => JSON.stringify(value || {}, null, 2);
export default function ModeltestLibrary({ catalog, reload, error, disabled, canRun, onAction, open, onClose, confirmAction }) {
  const dialog = useRef(null);
  const closeButton = useRef(null);
  useEffect(() => {
    if (open && !dialog.current.open) { dialog.current.showModal(); closeButton.current.focus(); }
    else if (!open && dialog.current.open) dialog.current.close();
  }, [open]);
  function keepFocus(event) {
    if (event.key !== 'Tab') return;
    const controls = [...dialog.current.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),summary')].filter(node => node.getClientRects().length);
    const first = controls[0]; const last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }
  const [value, setValue] = useState(''); const [revision, setRevision] = useState(''); const [draft, setDraft] = useState(null);
  const [options, setOptions] = useState('{}'); const [training, setTraining] = useState('{}'); const [message, setMessage] = useState(''); const [busy, setBusy] = useState(false); const [confirmThresholds, setConfirmThresholds] = useState(false);
  const families = catalog.families || {};
  function edit(model) { setDraft(model); setOptions(pretty(model.options)); setTraining(pretty(model.training)); setConfirmThresholds(false); setMessage(''); }
  async function action(fn) { setBusy(true); setMessage(''); try { await fn(); } catch (cause) { setMessage(cause.message); } finally { setBusy(false); } }
  const request = (path, method, body) => sshRequest(path, { method, timeout: 120000, headers: { 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) });
  async function inspect() { await action(async () => edit(await request(`${base}/inspect`, 'POST', { value, revision: revision || undefined }))); }
  async function save() {
    await action(async () => {
      let config, learned; try { config = JSON.parse(options); learned = JSON.parse(training); } catch { throw new Error('모델 설정 또는 학습 설정의 JSON 형식을 확인해 주세요.'); }
      const saved = await request(`${base}${draft.id ? `/${draft.id}` : ''}`, draft.id ? 'PUT' : 'POST', { value: draft.model_id, revision: draft.requestedRevision || draft.resolved_revision, name: draft.name, family: draft.family, options: config, training: learned, thresholds_confirmed: confirmThresholds });
      await reload(); edit(saved); setMessage('등록 설정을 저장했습니다. 가중치 다운로드·추론은 수행하지 않았습니다.');
    });
  }
  function remove(model) {
    confirmAction({ title: '모델 등록 삭제', message: `${model.name} 등록을 삭제할까요? 연결된 노드는 다시 모델을 선택해야 합니다. Pod의 캐시와 기존 결과는 유지됩니다.`, confirmLabel: '삭제', danger: true }, () => action(async () => { await request(`${base}/${model.id}`, 'DELETE'); if (draft?.id === model.id) setDraft(null); await reload(); }));
  }
  return <dialog ref={dialog} id="mt-model-library" className="mt-library-dialog" aria-labelledby="mt-library-title" onClose={onClose} onKeyDown={keepFocus}>
    <section className="mt-panel mt-library">
    <div className="mt-panel-heading"><div><div className="mt-title-help"><h2 id="mt-library-title">모델 관리</h2><ModeltestHelp variant="dialog" label="모델 관리"><p>HF 링크 또는 owner/model을 확인해 실행 계열을 등록합니다. 등록 시 메타데이터만 조회하며 새로고침 후에도 로컬 백엔드에 유지됩니다. 가중치 준비와 샘플 실행은 별도 버튼으로 요청합니다.</p><p>준비·샘플 실행은 연결된 SSH Pod에서 수행됩니다. 샘플 실행은 현재 워크플로우에 이 모델을 선택한 뒤 사용하세요. 영상은 첫 추출 프레임, 조건 추출은 첫 문서의 첫 페이지·청크만 실행합니다.</p></ModeltestHelp></div><p>Hugging Face 모델 추가 · {catalog.models?.length || 0}개 등록</p></div>
      <button ref={closeButton} type="button" className="btn secondary small" autoFocus onClick={() => dialog.current.close()} aria-label="모델 관리 닫기">닫기</button></div>
    <div className="mt-panel-body">
      <div className="mt-library-add"><label>HF 링크 또는 owner/model<input aria-label="추가할 HF 모델" value={value} disabled={disabled || busy} maxLength={512} onChange={event => setValue(event.target.value)} placeholder="https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct" /></label>
        <label>revision · 선택<input aria-label="HF revision" value={revision} disabled={disabled || busy} maxLength={128} onChange={event => setRevision(event.target.value)} placeholder="main" /></label>
        <button type="button" className="btn secondary small" disabled={disabled || busy || !value.trim()} onClick={inspect}>메타데이터 확인</button>
        <button type="button" className="text-btn" disabled={busy} onClick={() => action(reload)}>목록 새로 읽기</button></div>
      {(message || error) && <p role="status" className="mt-hint">{message || error}</p>}
      {draft && <div className="mt-library-editor">
        <div className="mt-library-add"><label>표시 이름<input aria-label="등록 모델 이름" value={draft.name} disabled={disabled || busy} onChange={event => setDraft({ ...draft, name: event.target.value })} maxLength={100} /></label>
          <label>실행 계열<select aria-label="모델 실행 계열" disabled={disabled || busy} value={draft.family || ''} onChange={event => { setDraft({ ...draft, family: event.target.value }); setOptions(pretty(families[event.target.value]?.defaults)); setConfirmThresholds(false); }}><option value="">판별되지 않음</option>{Object.entries(families).map(([id, family]) => <option key={id} value={id}>{id} · {family.engine}</option>)}</select></label></div>
        <p className="mt-hint">{draft.model_id} @ {draft.resolved_revision}<br />{families[draft.family]?.note}<br />필요 환경: {Object.entries(families[draft.family]?.requirements || {}).map(([name, version]) => `${name}${version}`).join(', ')}</p>
        {draft.id && <label>변경할 revision · 선택<input aria-label="등록 모델 revision 변경" disabled={disabled || busy} value={draft.requestedRevision || ''} placeholder="비워두면 현재 commit 유지" onChange={event => { setDraft({ ...draft, requestedRevision: event.target.value }); setConfirmThresholds(false); }} /></label>}
        <div className="mt-library-config"><label>실행 설정 · JSON<textarea aria-label="등록 모델 실행 설정" rows={8} value={options} disabled={disabled || busy} onChange={event => { setOptions(event.target.value); setConfirmThresholds(false); }} /></label>
          <div><div className="mt-title-help"><label htmlFor="mt-training-json">직접 학습한 모델 설정 · JSON</label><ModeltestHelp variant="dialog" label="학습 모델 설정 예시"><pre>{pretty({ base_model_id: draft.model_id, base_revision: draft.resolved_revision, trained: true, head: { model_id: 'owner/trained-head', revision: '40자리 commit SHA', filename: 'head.safetensors', sha256: '64자리 파일 SHA256' }, labels: ['일치', '불일치'], preprocessing: { source: 'repository' }, thresholds: { 일치: 0.8, 불일치: 0.8 } })}</pre><p>전체 checkpoint 또는 lora도 model_id·revision으로 지정합니다. 선형 head는 weight·bias만 지원합니다. 라벨 순서·학습 전처리·임계값을 실제 학습 설정으로 입력하세요. 빠진 항목은 준비 필요로 표시됩니다.</p></ModeltestHelp></div><textarea id="mt-training-json" aria-label="등록 모델 학습 설정" rows={8} value={training} disabled={disabled || busy} onChange={event => { setTraining(event.target.value); setConfirmThresholds(false); }} /></div></div>

        {draft.id && <label className="mt-toggle"><input type="checkbox" checked={confirmThresholds} disabled={disabled || busy} onChange={event => setConfirmThresholds(event.target.checked)} />현재 설정에 맞게 분류 임계값을 다시 확인했음</label>}
        <p className="mt-hint">설정을 바꾸면 이전 실행 확인이 해제됩니다. 수정 시 위 확인이 없으면 분류 임계값을 비웁니다. 노드에서도 모델을 다시 선택하고 필터 임계값을 확인하세요.</p>
        <p className={draft.reasons.length ? 'mt-error' : 'mt-hint'}>{MODEL_STATUS[draft.status]}{draft.reasons.length ? ` · ${draft.reasons.join(' · ')}` : ''}</p>
        <button type="button" className="btn primary small" disabled={disabled || busy} onClick={save}>모델 등록·저장</button> <button type="button" className="text-btn" onClick={() => setDraft(null)}>편집 닫기</button>
      </div>}
      <div className="mt-library-list">{(catalog.models || []).map(model => <article key={model.id}>
        <div><strong>{model.name}</strong><span className="mt-tag">{MODEL_STATUS[model.status]}</span><p>{model.model_id} · {model.family || '계열 미확인'} · {model.resolved_revision.slice(0, 10)}</p>
          <small>{model.reasons.join(' · ') || `지원 작업: ${model.tasks.join(', ')}`}</small>{model.prepared && <small>준비 이력 있음 · 현재 Pod의 파일·버전은 실행 시 확인</small>}{model.verification && <small>{model.verification.scope}</small>}</div>
        <div className="mt-workflow-toolbar"><button type="button" className="text-btn" disabled={disabled || busy} onClick={() => edit(model)}>편집</button><button type="button" className="text-btn mt-error" disabled={disabled || busy} onClick={() => remove(model)}>삭제</button>
          <button type="button" className="btn secondary small" disabled={disabled || !canRun || !['unverified', 'verified'].includes(model.status)} onClick={() => onAction(model, 'prepare')}>가중치·환경 준비</button>
          <button type="button" className="btn secondary small" disabled={disabled || !canRun || !['unverified', 'verified'].includes(model.status)} onClick={() => onAction(model, 'sample')}>현재 워크플로우 샘플 실행</button></div>
      </article>)}</div>
      <button type="button" className="btn secondary small" disabled={disabled || !canRun} onClick={() => onAction(null, 'prepare')}>일반 처리 · DISK + LightGlue 준비</button>
    </div>
    </section>
  </dialog>;
}
