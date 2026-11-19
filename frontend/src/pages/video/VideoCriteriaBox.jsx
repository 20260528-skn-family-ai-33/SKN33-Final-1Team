import React, { useState } from 'react';
import { Icon } from '../../components/Icon.jsx';
import { usePaged } from '../../components/Pager.jsx';
import CriteriaPanel from '../review/CriteriaPanel.jsx';

// 동영상 검수 자료 입력의 '검수 항목' 상자(2026-11-17). 포스터 자료 입력의 검수 항목 상자(InputPage · CriteriaPanel)와
// 같은 부품·같은 자리(직접 입력란 아래, 문서 첨부 위)·같은 동작으로 문서에서 추출한 기준을 보인다.
//   · 추출한 항목은 모두 검수에 포함하고, 뺄 항목만 체크를 푼다(삭제 단추는 없다. 포스터와 같다).
//   · 항목을 누르면 그 아래에 판단 기준 · 원문을 펼치고, '기준 보완'으로 기준 문장을 고친다.
// 동영상 기준은 서버가 문장(text)으로 받으므로, 보완은 포스터처럼 값·비교 방법을 나누지 않고 문장 한 개를 고친다.
// rows: 문서 요구사항 줄 { id, text, type, source, selected, document_condition? } · onChange(rows) · disabled
const PER_PAGE = 6;   // 포스터 검수 항목 상자와 같은 쪽 크기(FrameworkReviewPage CRITERIA_PER_PAGE)

// 줄의 source는 '파일 이름 · 3페이지 · …\n원문'(포스터 A02 경로) 또는 원문만(영상 전용 분석)이다.
function sourceOf(row) {
  const text = String(row.source || '');
  const split = text.indexOf('\n');
  const head = split >= 0 ? text.slice(0, split) : '';
  const quote = split >= 0 ? text.slice(split + 1) : text;
  const location = head.split(' · ')[1] || '';
  const pages = /^([\d·]+)페이지$/.exec(location)?.[1];
  const label = pages ? `원문 ${pages}쪽` : location === '본문' ? '원문 본문' : '원문';
  return { kind: 'document', page: pages || null, label, quote: quote || row.text };
}

// CriteriaPanel이 읽는 기준 모양으로 바꾼다. 포스터 A02의 이름·기준값·검사 방법이 있으면 포스터처럼 이름과 값을 먼저 보인다.
function criterionOf(row) {
  const condition = row.document_condition;
  return {
    criterion_id: row.id, text: row.text, kind: row.type === 'visual' ? 'visual' : 'text',
    expected: condition?.requirement || row.text, required: !['권장', '선택'].includes(condition?.level),
    details: { ...(condition?.label ? { presentation: { label: condition.label } } : {}),
      qualifiers: condition?.inspection ? [{ quote: condition.inspection }] : [] },
    source: sourceOf(row),
  };
}

export default function VideoCriteriaBox({ rows, onChange, disabled = false }) {
  const criteria = rows.map(criterionOf);
  const excluded = rows.filter(row => !row.selected).map(row => row.id);
  const paged = usePaged(criteria, PER_PAGE);
  const [activeId, setActiveId] = useState(null);
  const [detailOpen, setDetailOpen] = useState(true);
  const [quoteOpen, setQuoteOpen] = useState(false);
  const [amending, setAmending] = useState(null);   // { id, text }
  const selected = criteria.find(item => item.criterion_id === activeId) || paged.shown[0] || null;

  function edit(id, change) { onChange(rows.map(row => row.id === id ? { ...row, ...change } : row)); }
  function open(id) {
    if (selected?.criterion_id === id && detailOpen) { if (!amending) setDetailOpen(false); return; }
    setActiveId(id); setDetailOpen(true); setQuoteOpen(false);
  }
  function turn(page) {
    paged.setPage(page);
    if (!amending) { setActiveId(criteria[page * PER_PAGE]?.criterion_id || null); setQuoteOpen(false); }
  }
  // 고친 문장은 포스터 A02의 이름·기준값과 맞지 않게 되므로, 그 표시값(document_condition)은 내려놓고 문장으로 보인다.
  function save() {
    const text = amending.text.replace(/\s+/g, ' ').trim();
    if (!text) return;
    edit(amending.id, { text, document_condition: undefined });
    setAmending(null);
  }

  return <section className="rv-criteria-box rv-criteria-inspector" aria-label="검수 항목">
    {criteria.length ? <CriteriaPanel criteria={criteria} excluded={excluded} selected={selected}
      detailOpen={detailOpen} onOpen={open} quoteOpen={quoteOpen} onQuote={() => setQuoteOpen(value => !value)}
      onToggle={(id, included) => edit(id, { selected: included })}
      paged={paged} onPage={turn} editable={!disabled} busy={disabled} amendingId={amending?.id}
      detailActions={!disabled && selected && (amending?.id === selected.criterion_id
        // 동영상 자료 입력 전체가 하나의 <form>이라 여기에는 form을 두지 않는다(겹친 form은 저장 단추가 바깥 form을 제출한다).
        ? <div className="rv-criteria-amend-form">
          <fieldset className="rv-criteria-amendment">
            <legend>기준 보완</legend>
            <label>기준 문장 <textarea rows={3} maxLength={500} value={amending.text}
              onChange={event => setAmending({ ...amending, text: event.target.value.replace(/[\r\n]+/g, ' ') })} /></label>
          </fieldset>
          <div className="rv-criteria-amend-actions">
            <button type="button" className="rv-button primary" disabled={!amending.text.trim()} onClick={save}>보완 내용 저장</button>
            <button type="button" className="rv-button secondary" onClick={() => setAmending(null)}>보완 취소</button>
          </div>
        </div>
        : <button type="button" className="rv-button secondary rv-compact rv-framework-amend" disabled={excluded.includes(selected.criterion_id)}
          onClick={() => setAmending({ id: selected.criterion_id, text: selected.text })}><Icon name="pencil" />기준 보완</button>)} />
      : <p className="rv-criteria-empty">문서에서 검수할 항목을 찾지 못했어요. 직접 입력란에 적어 주세요.</p>}
  </section>;
}
