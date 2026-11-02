import React, { useEffect, useRef, useState } from 'react';
import DocumentConditionDetails from './DocumentConditionDetails.jsx';
import ImageStage from '../../components/ImageStage.jsx';
import { Icon } from '../../components/Icon.jsx';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { canConfirm, canRun } from '../../workflow.js';
import '../../styles/review-editor-v2.css';

// 포함 여부와 현재 편집 항목을 분리해, 기준 전체를 펼치지 않고 한 항목씩 검토한다.
export default function CriteriaPage({ state, locked, selected, selectedInfo, versionText, unlock, dispatch, addCriterion, newCriterion, setNewCriterion, newCategory, setNewCategory, navigate, confirm, error, run, preview }) {
  const [activeId, setActiveId] = useState(() => state.criteria[0]?.id);
  const previousCount = useRef(state.criteria.length);
  const previousLocked = useRef(locked);
  const requestedFocus = useRef(null);
  const editorRef = useRef(null);
  const detailRef = useRef(null);
  const runRef = useRef(null);
  const activeIndex = Math.max(0, state.criteria.findIndex(item => item.id === activeId));
  const active = state.criteria[activeIndex];
  const activeNumber = activeIndex + 1;

  useEffect(() => {
    if (state.criteria.length > previousCount.current) {
      const addedId = state.criteria.at(-1)?.id;
      requestedFocus.current = addedId;
      setActiveId(addedId);
    }
    previousCount.current = state.criteria.length;
  }, [state.criteria]);
  useEffect(() => {
    if (requestedFocus.current && requestedFocus.current === active?.id) {
      requestedFocus.current = null;
      (locked ? detailRef.current : editorRef.current)?.focus();
    }
  }, [active?.id, locked]);
  // 확정/재편집으로 눌렀던 버튼이 사라져도 키보드 작업을 이어갈 수 있게 한다.
  useEffect(() => {
    if (previousLocked.current !== locked) {
      previousLocked.current = locked;
      (locked ? runRef.current : editorRef.current || detailRef.current)?.focus();
    }
  }, [locked]);
  function inspectCriterion(id) {
    if (id === active?.id) {
      (locked ? detailRef.current : editorRef.current)?.focus();
      return;
    }
    requestedFocus.current = id;
    setActiveId(id);
  }

  return <section className="rv-criteria-page" aria-labelledby="page-title">
    <div className="rv-workspace-intro"><div><span className="rv-eyebrow">02 / 기준 확정</span><h1 id="page-title" tabIndex={-1}>검수할 항목을 확정해 주세요.</h1><p>이미지를 보며 항목을 검토하세요. 체크한 항목만 검수에 포함됩니다.</p></div><span className={`rv-criteria-version${locked ? ' is-locked' : ''}`}><Icon name={locked ? 'lock' : 'list'} />{versionText}</span></div>
    <div className="rv-editor-grid">
      <div className="rv-criteria-canvas-column">
        <ImageStage src={preview} alt="검수 대상 이미지" title={state.projectName || '검수 대상 이미지'} subtitle={state.file?.name || '이미지 1개'} />
        <div className={`rv-criteria-context${locked ? ' is-locked' : ''}`}><Icon name={locked ? 'lock' : 'info'} /><div>{locked ? <><strong>검수 기준 v{state.snapshot.version}이 확정되었습니다.</strong><p>검수 시작 시 이 기준 버전을 사용합니다. 변경하려면 확정을 풀고 다시 검토하세요.</p></> : <><strong>{state.version ? '기준을 수정하고 다시 확정해 주세요.' : '입력한 요구사항을 항목별로 정리했어요.'}</strong><p>{state.version ? '이전 결과는 무효화되었습니다. 재확정 후 새 기준으로 검수할 수 있습니다.' : '모호한 문구는 구체적인 값으로 보완하세요. 원문과 상세 기준도 함께 확인할 수 있습니다.'}</p></>}</div>{locked && <button type="button" onClick={unlock}>기준 다시 편집</button>}</div>
      </div>

      <aside className="rv-panel rv-criteria-inspector" aria-label="검수 기준 검토">
        <div className="rv-panel-heading"><div><span className="rv-eyebrow">REVIEW CRITERIA</span><h2>검수 항목 <span>{state.criteria.length}</span></h2></div><span className="rv-criteria-selection-count">{selected.length}개 선택</span></div>
        <div className="rv-criteria-toolbar"><span>정보 {selectedInfo} · 시각 {selected.length - selectedInfo} · 제외 {state.criteria.length - selected.length}</span>{!locked && <div><button type="button" onClick={() => dispatch({ type: 'select', selected: true })}>전체 선택</button><button type="button" onClick={() => dispatch({ type: 'select', selected: false })}>전체 해제</button></div>}</div>
        <ul className="rv-criteria-list" aria-label="검수 항목 목록">{state.criteria.map((item, i) => <li className={`rv-criteria-item${item.id === active?.id ? ' is-active' : ''}${item.selected ? '' : ' is-excluded'}`} key={item.id}>
          <label className="rv-criteria-check"><input type="checkbox" checked={item.selected} disabled={locked} onChange={e => { setActiveId(item.id); dispatch({ type: 'update', id: item.id, patch: { selected: e.target.checked } }); }} /><span className="sr-only">검수 항목 {String(i + 1).padStart(2, '0')}</span></label>
          <button type="button" className="rv-criteria-select" aria-current={item.id === active?.id ? 'true' : undefined} aria-controls="rv-active-criterion" aria-label={`검수 항목 ${i + 1} ${locked ? '상세 보기' : '편집'}`} onClick={() => inspectCriterion(item.id)}><span className="rv-criteria-item-index">{String(i + 1).padStart(2, '0')}</span><span className="rv-criteria-item-text">{item.text || '내용을 입력해 주세요.'}</span><span className="rv-criteria-item-type">{item.type === 'visual' ? '시각' : '정보'}</span><span className="rv-criteria-item-arrow" aria-hidden="true">↗</span></button>
        </li>)}</ul>
        {active ? <div className="rv-criteria-detail" id="rv-active-criterion" key={active.id} ref={detailRef} tabIndex={-1} aria-label={`검수 항목 ${activeNumber} 상세`}>
          <div className="rv-criteria-detail-heading"><div><span className="rv-eyebrow">SELECTED CRITERION</span><h3>검수 항목 {String(activeNumber).padStart(2, '0')}</h3></div><select aria-label={`검수 항목 ${activeNumber} 유형`} value={active.type} disabled={locked} onChange={e => dispatch({ type: 'update', id: active.id, patch: { type: e.target.value } })}><option value="information">정보 검수</option><option value="visual">시각 검수</option></select></div>
          <textarea ref={editorRef} aria-label={`검수 항목 ${activeNumber} 내용`} value={active.text} maxLength={500} disabled={locked} onChange={e => dispatch({ type: 'update', id: active.id, patch: { text: e.target.value } })} />
          <div className="rv-criteria-detail-meta"><span>{active.selected ? '검수에 포함되는 항목' : '검수에서 제외한 항목'}</span><span>{active.text.length} / 500</span></div>
          <DocumentConditionDetails condition={active.document_condition} />
          <div className="rv-criteria-source"><Icon name="file" /><details><summary>요구사항 원문</summary><p>{active.source}</p></details></div>
        </div> : <div className="rv-criteria-empty"><Icon name="list" /><p>검수할 항목이 없습니다.<br />자료를 다시 입력하거나 항목을 추가해 주세요.</p></div>}
        {!locked && <details className="rv-criteria-add" open={newCriterion.trim() ? true : undefined}><summary><span aria-hidden="true">＋</span> 검수 항목 직접 추가</summary><form onSubmit={addCriterion}><input value={newCriterion} onChange={e => setNewCriterion(e.target.value)} maxLength={500} aria-label="추가할 검수 항목" placeholder="필요한 검수 항목 추가" /><div><select aria-label="추가할 검수 유형" value={newCategory} onChange={e => setNewCategory(e.target.value)}><option value="information">정보 검수</option><option value="visual">시각 검수</option></select><button type="submit" className="rv-button secondary" disabled={!newCriterion.trim()}>항목 추가</button></div></form></details>}
      </aside>
    </div>
    <div className="rv-criteria-error"><ErrorMessage message={error} /></div>
    <div className="rv-actionbar rv-criteria-actionbar"><div className="rv-criteria-action-context"><button type="button" className="rv-button secondary" onClick={() => navigate('input')}>자료 다시 입력</button><div><strong>{locked ? `v${state.snapshot.version} 기준 확정 완료` : `${selected.length}개 항목을 검수에 포함합니다.`}</strong><p>{locked ? '확정한 기준 전체로 검수합니다.' : '먼저 선택한 기준을 확정해 주세요.'}</p></div></div><div className="rv-criteria-action-buttons">{!locked && <button type="button" className="rv-button primary" onClick={confirm} disabled={!canConfirm(state)}>선택한 기준 확정하기</button>}<button type="button" className={`rv-button ${locked ? 'primary' : 'secondary'}`} ref={runRef} onClick={run} disabled={!canRun(state)}>검수 시작하기<Icon name="arrow" /></button></div></div>
  </section>;
}
