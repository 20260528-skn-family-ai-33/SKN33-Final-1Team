import React, { useLayoutEffect, useRef, useState } from 'react';
import { Icon } from '../../components/Icon.jsx';
import { Pager } from '../../components/Pager.jsx';
import { criterionLine } from './criterionLabel.js';

// 목록 한 줄: 이름(행사명·시작일 요일…)과 기준값만 보인다. 기준 문장·원문은 항목을 펼쳤을 때 보인다.
// 이름을 모르는 기준은 문장만 한 줄로 보인다. 한 원문 문장에서 나온 여러 기준은 값으로 구분된다.
function CriterionText({ criterion }) {
  const line = criterionLine(criterion);
  return <span className="rv-criteria-item-text">{line.label ? <>
    <span className="rv-criteria-item-label">{line.label}</span>
    <span className="rv-criteria-item-value">{line.expected || ''}</span>
  </> : <span className="rv-criteria-item-value">{criterion.text}</span>}</span>;
}
// PDF 줄바꿈을 한 문단으로 잇는다(원문 글자는 바꾸지 않는다).
const oneLine = text => String(text || '').replace(/\s+/g, ' ').trim();
// 원문은 두 줄까지만 보이고, 실제로 잘릴 때만 '원문 전체 보기'를 단다(화면 폭에 따라 잘리는 길이가 달라서 글자 수로 정하지 않는다).
function ClampedQuote({ text, open, onToggle }) {
  const ref = useRef(null);
  const [overflowing, setOverflowing] = useState(false);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    const measure = () => setOverflowing(open || element.scrollHeight > element.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [text, open]);
  return <>
    <span ref={ref} className={open ? undefined : 'is-clamped'}>{text}</span>
    {overflowing && <button type="button" className="rv-criteria-quote-toggle" aria-expanded={open} onClick={onToggle}>
      {open ? '접기' : '원문 전체 보기'}<Icon name="chevron" className={`icon${open ? ' is-open' : ''}`} /></button>}
  </>;
}
// 판단 기준: 원문에서 뽑은 허용·판단 조건. 없으면 이름·기준값과 다른 기준 문장을 쓴다.
function judgmentRule(criterion) {
  const qualifiers = [...new Set((criterion.details?.qualifiers || []).map(item => oneLine(item.quote)).filter(Boolean))];
  if (qualifiers.length) return qualifiers.join(' ');
  const line = criterionLine(criterion);
  return line.label ? oneLine(line.rule) : '';
}

// 검수 항목 목록. 고른 항목의 상세(판단 기준 · 원문 · 보완 회신)는 그 항목 바로 아래에 펼친다.
// 자료 입력 한 화면의 검수 항목 상자(승인 전 초안), 승인한 기준을 다시 보는 화면, 개발용 화면 둘러보기가 함께 쓴다.
// 데이터와 동작은 부르는 쪽이 넘기고, 이 부품은 서버를 부르지 않는다.
//   criteria 기준 목록 · excluded 체크를 푼 기준의 번호들 · onToggle(번호, 포함 여부)
//   selected 고른 기준 · detailOpen 그 기준의 상세를 펼쳤는가 · onOpen(번호) 항목을 눌렀을 때(펼칠지 접을지는 부르는 쪽이 정한다)
//   quoteOpen 펼친 상세에서 원문 전체를 보는가 · onQuote() 원문 전체 보기 · 접기
//   paged 쪽 나눔(usePaged의 결과) · onPage(쪽) · editable 체크를 바꿀 수 있는가(승인 전) · busy 처리 중
//   amendingId 보완하고 있는 기준(그 줄은 체크를 바꿀 수 없고, 다른 줄은 누를 수 없다) · blocked 값이 정해지지 않은 기준({ 번호: 사유 })
//   notices 목록 위 안내(읽지 못한 쪽) · detailActions 펼친 상세 아래 동작(기준 보완) · footer 목록 아래(제외한 기준)
export default function CriteriaPanel({ criteria, excluded, onToggle, selected, detailOpen = true, onOpen, quoteOpen = false, onQuote,
  paged, onPage, editable = false, busy = false, amendingId = null, blocked = null, notices = null, detailActions = null, footer = null }) {
  return <>
    <div className="rv-panel-heading"><div><span className="rv-eyebrow">REVIEW CRITERIA</span><h2>검수 항목 <span>{criteria.length}</span></h2></div>
      <span className="rv-criteria-selection-count">{criteria.length - excluded.length}개 포함</span></div>
    {notices}
    <div className="rv-criteria-toolbar"><span>{editable ? '뺄 항목은 체크를 풀어 주세요. 값은 기준 보완으로 고칩니다.' : '원문 기준 · 읽기 전용'}</span><span>제외 {excluded.length}개</span></div>
    <ul className="rv-criteria-list rv-criteria-list--inline" aria-label="검수 항목 목록">{paged.shown.map((c, index) => {
      const active = selected?.criterion_id === c.criterion_id;
      const open = active && detailOpen;
      const rule = open ? judgmentRule(c) : '';
      const quote = open ? oneLine(c.source.quote) : '';
      return <li id={'rv-criterion-' + c.criterion_id} key={c.criterion_id} className={`rv-criteria-item${active ? ' is-active' : ''}${open ? ' is-open' : ''}${excluded.includes(c.criterion_id) ? ' is-excluded' : ''}${blocked?.[c.criterion_id] ? ' is-blocked' : ''}`}>
        <label className="rv-criteria-check"><input type="checkbox" aria-label={`검수에 포함: ${c.text}`} disabled={!editable || busy || amendingId === c.criterion_id}
          checked={!excluded.includes(c.criterion_id)} onChange={event => onToggle(c.criterion_id, event.target.checked)} /></label>
        <button type="button" className="rv-criteria-select" aria-expanded={open} aria-controls={open ? 'rv-active-criterion' : undefined}
          disabled={busy || (Boolean(amendingId) && amendingId !== c.criterion_id)} onClick={() => onOpen(c.criterion_id)}>
          <span className="rv-criteria-item-index">{String(paged.start + index + 1).padStart(2, '0')}</span><CriterionText criterion={c} />
          <span className="rv-criteria-item-type">{c.source.kind === 'direct_input' ? '직접 입력' : !c.expected.trim() ? '기대값 미정' : c.kind === 'visual' ? '시각' : '정보'} · {c.required ? '필수' : '참고'}</span>
          <Icon name="chevron" className={`icon rv-criteria-item-arrow${open ? ' is-open' : ''}`} />
        </button>
        {/* 고른 항목의 상세는 그 항목 바로 아래에 펼친다. 이름표 칸은 목록의 이름 칸과 같은 폭으로 맞춘다. */}
        {open && <div className="rv-criteria-inline-detail" id="rv-active-criterion">
          <dl className="rv-criteria-facts">
            {rule && <><dt>판단 기준</dt><dd>{rule}</dd></>}
            <dt>{c.source.kind === 'direct_input' ? '직접 입력 원문' : `원문 ${c.source.page}쪽`}</dt>
            <dd className="rv-criteria-quote"><ClampedQuote text={quote} open={quoteOpen} onToggle={onQuote} /></dd>
            {c.value_source && (c.source.kind !== 'direct_input' || c.value_source.reference !== '담당자 직접 입력')
              && <><dt>보완 회신</dt><dd>{c.value_source.quote} · 출처: {c.value_source.reference}</dd></>}
          </dl>
          {detailActions && <div className="rv-criteria-inline-actions">{detailActions}</div>}
        </div>}
      </li>; })}</ul>
    <Pager page={paged.page} pages={paged.pages} onChange={onPage} label="검수 항목 쪽" disabled={busy} />
    {!criteria.length && <p className="rv-criteria-empty">검수 기준을 불러오고 있습니다.</p>}
    {footer}
  </>;
}
