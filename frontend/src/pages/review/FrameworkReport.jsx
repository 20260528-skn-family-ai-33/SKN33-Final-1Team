import React, { useEffect, useMemo, useRef, useState } from 'react';
import ImageStage from '../../components/ImageStage.jsx';
import { buildReportContext, posterMarks } from './reportBrief.js';
import { REPORT_COMMENT_MAX, readReportComments, writeReportComments } from './reportComments.js';
import { buildConfirmationForm } from './confirmationForm.js';
import ConfirmationForm from './ConfirmationForm.jsx';
import '../../styles/framework-report.css';

// 포스터 4단계 보고서. 디자인은 목업(회색 바탕 위 종이, 점 달린 상태 칩, 개수 상자, 안내 상자)을 따르고,
// 내용은 팀 A07 보고서 템플릿(reportBrief.js) 그대로다. 판정은 A06 결과를 바꾸지 않는다.
// '결과 확인으로 돌아가기'는 목업처럼 프로젝트 바에 있다(FrameworkReviewPage).
const decisions = { unreviewed: '검토 대기', changes_approved: '수정 요청 승인', final_approved: '최종 승인', exception_approved: '예외 승인' };
const pad = number => String(number).padStart(2, '0');

function StatusChip({ verdict }) {
  return <span className={`rv-report-chip is-${verdict.code}`}><i aria-hidden="true" />{verdict.label}</span>;
}

// '위치 보기' 창: 화면 가운데 가로·세로 절반 크기. 결과 확인 화면과 같은 ImageStage(확대·이동·선택 근거 확대)로
// 이 항목의 위치 상자 하나만 포스터 위에 표시한다. Esc·닫기·바깥 클릭으로 닫는다.
function LocationViewer({ mark, verdict, image, onClose }) {
  const dialog = useRef(null);
  useEffect(() => { const element = dialog.current; if (element && !element.open) element.showModal(); }, []);
  const [fitted, setFitted] = useState(false);
  const sized = image?.width > 0 && image?.height > 0;
  const overlays = sized ? [{ id: mark.criterion_id, tone: mark.code, label: `${mark.number}. ${mark.label}`,
    bbox_xyxy: mark.bbox.map((value, index) => value * (index % 2 ? image.height : image.width)) }] : [];
  const close = () => dialog.current?.close();
  return <dialog ref={dialog} className="rv-report-viewer" aria-labelledby="rv-report-viewer-title" onClose={onClose}
    onClick={event => { if (event.target === event.currentTarget) close(); }}>
    <header className="rv-report-viewer-heading">
      <h2 id="rv-report-viewer-title">{mark.number}. {mark.label}</h2><StatusChip verdict={verdict} />
      <button type="button" className="rv-report-viewer-close" aria-label="위치 보기 닫기" onClick={close}>×</button>
    </header>
    <ImageStage src={image?.url || null} alt={`${mark.number}. ${mark.label} 위치를 표시한 검수 포스터`} title={`${mark.number}. ${mark.label}`}
      width={sized ? image.width : undefined} height={sized ? image.height : undefined}
      overlays={overlays} selectedOverlayId={mark.criterion_id} viewKey={mark.criterion_id} fitEvidence="model" onEvidenceFit={setFitted} emphasis
      empty={<p>포스터 이미지를 불러올 수 없습니다.</p>} />
    <p className="rv-report-viewer-note">{fitted ? '상자는 검사 모델이 알려 준 위치를 둘레의 글자에 맞춰 다듬은 것입니다.' : '상자는 검사 모델이 읽은 위치입니다.'} 실제 포스터와 맞는지 담당자가 확인해 주세요.</p>
  </dialog>;
}

export default function FrameworkReport({ run, projectName, image = null, standalone = false }) {
  const context = useMemo(() => buildReportContext(run, { projectName }), [run, projectName]);
  const marks = useMemo(() => Object.fromEntries(posterMarks(run, context.items).marks.map(mark => [mark.criterion_id, mark])), [run, context]);
  const [viewing, setViewing] = useState(null);
  // 확인 필요 항목의 담당자 코멘트. 실행별로 이 브라우저에 저장한다(reportComments.js).
  const runId = run.context?.run_id;
  const [comments, setComments] = useState(() => readReportComments(runId));
  const [commentSaved, setCommentSaved] = useState(true);
  const [commentRun, setCommentRun] = useState(runId);
  if (commentRun !== runId) { setCommentRun(runId); setComments(readReportComments(runId)); setCommentSaved(true); }
  const updateComment = (id, text) => {
    const next = { ...comments, [id]: text };
    setComments(next);
    setCommentSaved(writeReportComments(runId, next));
  };
  // 충족 항목은 처음에 접어 두고(2026-11-17 요청: 확인 필요는 적고 충족이 너무 많음) 단추로 펼친다.
  // 인쇄·PDF에는 충족 항목을 빼고, 담당자가 고른 항목만 싣는다. 고른 항목은 이 화면에서만 기억한다.
  const passRef = useRef(null);
  const scrollOnOpen = useRef(false);
  const [passOpen, setPassOpen] = useState(false);
  const [printPass, setPrintPass] = useState(() => new Set());
  const [passRun, setPassRun] = useState(runId);
  if (passRun !== runId) { setPassRun(runId); setPassOpen(false); setPrintPass(new Set()); }
  const openPass = () => { scrollOnOpen.current = true; setPassOpen(true); };
  useEffect(() => {
    if (!passOpen || !scrollOnOpen.current) return;
    scrollOnOpen.current = false;
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    passRef.current?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
  }, [passOpen]);
  const togglePrint = id => setPrintPass(current => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  // '검수 확인서 인쇄'(2026-11-18 요청): 보고서 대신 공공기관 보고서 형식 확인서(ConfirmationForm)만 인쇄한다.
  // 상태를 바꿔 확인서가 그려진 뒤 인쇄 창을 열고, 창이 닫히면 보고서 인쇄로 되돌린다.
  const [printingForm, setPrintingForm] = useState(false);
  const form = useMemo(() => buildConfirmationForm(context, { comments, decision: run.human_decision }), [context, comments, run.human_decision]);
  useEffect(() => {
    if (!printingForm) return;
    window.print();
    setPrintingForm(false);
  }, [printingForm]);
  const hasReviewItems = context.items.some(item => item.verdict.code === 'review');
  const changeReasons = (run.approvals || []).filter(approval => approval.decision === 'approve_changes' && approval.reason).map(approval => approval.reason);
  const status = run.human_decision === 'final_approved' ? '담당자 최종 승인' : context.synthetic ? '합성 데이터 · 자동 검수 초안' : '자동 검수 초안';
  const { counts } = context;
  // 번호는 전체 순서(위치 보기 번호와 같음)를 그대로 쓴다. 항목은 불충족·확인 필요·충족 순으로 정렬돼 있다(reportBrief.js).
  const numbered = context.items.map((item, index) => ({ item, number: pad(index + 1) }));
  const passItems = numbered.filter(({ item }) => item.verdict.code === 'pass');
  const otherItems = numbered.filter(({ item }) => item.verdict.code !== 'pass');
  const printedPass = passItems.filter(({ item }) => printPass.has(item.criterion_id)).length;
  const renderItem = ({ item, number }) => {
    const passed = item.verdict.code === 'pass';
    const printed = !passed || printPass.has(item.criterion_id);
    const commentable = item.verdict.code === 'review';
    const comment = comments[item.criterion_id] || '';
    return <div className={`rv-report-result${commentable ? ' has-comment' : ''}${printed ? '' : ' is-print-excluded'}`} key={item.criterion_id}>
      <div className="rv-report-result-heading"><h3>{number}. {item.label}{item.required ? '' : ' · 권장'}</h3>
        <div className="rv-report-result-side">
          {passed && <label className="rv-report-print-pick"><input type="checkbox" checked={printed}
            onChange={() => togglePrint(item.criterion_id)} aria-label={`${number}. ${item.label} 인쇄에 포함`} />인쇄에 포함</label>}
          {marks[item.criterion_id] ? <button type="button" className="rv-report-locate" aria-haspopup="dialog"
            aria-label={`${number}. ${item.label} 위치 보기`} onClick={() => setViewing(item.criterion_id)}>위치 보기</button>
            : !passed && <span className="rv-report-locate-none">위치 정보 없음</span>}
          <StatusChip verdict={item.verdict} />
        </div></div>
      <div className="rv-report-result-body"><div>
        <p>기준 {item.expected ? `‘${item.expected}’` : '미정'}{item.source ? ` · ${item.source}` : ''}</p>
        <p><b>관측</b> {item.observed ? `‘${item.observed}’` : item.observation}{!passed && item.location ? ` · ${item.location}` : ''}</p>
        {item.reason && <p className="rv-report-muted">{item.reason}</p>}
        {item.human && <p className="rv-report-human"><b>담당자 확인 · {item.human.verdict.label}</b> {item.human.note}</p>}
        {commentable && comment.trim() && <p className="rv-report-comment-print"><b>담당자 코멘트</b> {comment.trim()}</p>}
      </div>
      {/* 위치 보기·확인 필요 칩 바로 아래(오른쪽 칸)에 코멘트 한 칸만 둔다. 인쇄에서는 칸 대신 위의 글로 나온다. */}
      {commentable && <textarea className="rv-report-comment" rows={2} maxLength={REPORT_COMMENT_MAX}
        placeholder="담당자 코멘트" aria-label={`${number}. ${item.label} 담당자 코멘트`}
        value={comment} onChange={event => updateComment(item.criterion_id, event.target.value)} />}
      </div>
    </div>;
  };
  return <article className={`rv-report-print rv-poster-report${printingForm ? ' is-printing-form' : ''}`} aria-label="검수 보고서">
    <div className="rv-report-actions">
      <button type="button" className="rv-button secondary" onClick={() => setPrintingForm(true)}>검수 확인서 인쇄</button>
      <button type="button" className="rv-button secondary" onClick={() => window.print()}>인쇄 / PDF 저장</button>
    </div>
    <div className="rv-report-shell"><div className="rv-report-paper">
      <header className="rv-report-heading">
        <div><strong>Creative Checker</strong><span>{status}</span></div>
        {standalone ? <h1 id="page-title" tabIndex={-1}>검수 보고서</h1> : <h2>검수 보고서</h2>}
        <p>{projectName ? `${projectName} · 포스터 검수` : '포스터 검수'}</p>
        <dl className="rv-report-meta">
          <div><dt>검수 시각</dt><dd>{context.checked_at || '기록 없음'}</dd></div>
          {/* 포스터·승인 기준 식별값(내부 ID)은 보고서 머리에 보이지 않는다. */}
          <div><dt>담당자 결정</dt><dd>{decisions[run.human_decision] || '기록 확인 필요'}</dd></div>
        </dl>
      </header>

      <section aria-labelledby="rv-report-summary">
        <h2 id="rv-report-summary">판정 요약</h2>
        <div className="rv-report-machine"><StatusChip verdict={context.verdict} /><span>기계 판정 · {context.verdict.label}</span></div>
        <div className="rv-report-counts">
          <span><strong>필수</strong> {counts.required}건</span>
          {passItems.length > 0 ? <button type="button" className="rv-report-count-jump is-pass" aria-controls="rv-report-pass-list"
            onClick={openPass}><strong>충족</strong> {counts.pass}건 <span className="rv-report-count-arrow" aria-hidden="true">↓</span></button>
            : <span className="is-pass"><strong>충족</strong> {counts.pass}건</span>}
          <span className="is-review"><strong>확인 필요</strong> {counts.review}건</span>
          <span className="is-fail"><strong>불충족</strong> {counts.fail}건</span>
          {counts.optional > 0 && <span><strong>권장</strong> {counts.optional}건 · 판정 제외</span>}
        </div>
      </section>

      <section aria-labelledby="rv-report-details">
        <h2 id="rv-report-details">요구사항별 결과</h2>
        {hasReviewItems && <p className="rv-report-comment-hint">
          {commentSaved ? '확인 필요 항목의 담당자 코멘트는 이 브라우저에 저장되고, 인쇄·PDF에 함께 나옵니다.'
            : '이 브라우저에 저장할 수 없어요. 새로고침하면 코멘트가 사라집니다.'}</p>}
        {context.items.length ? <>
          {otherItems.map(renderItem)}
          {passItems.length > 0 && <div className="rv-report-pass" ref={passRef}>
            <div className="rv-report-pass-bar">
              <button type="button" className="rv-report-pass-toggle" aria-expanded={passOpen} aria-controls="rv-report-pass-list"
                onClick={() => passOpen ? setPassOpen(false) : openPass()}>
                충족 항목 {passItems.length}건 {passOpen ? '접기' : '펼쳐 보기'} <span aria-hidden="true">{passOpen ? '▴' : '▾'}</span></button>
              <span className="rv-report-pass-count">인쇄 포함 {printedPass}/{passItems.length}</span>
              {passOpen && <button type="button" className="rv-report-pass-all"
                onClick={() => setPrintPass(printedPass === passItems.length ? new Set() : new Set(passItems.map(({ item }) => item.criterion_id)))}>
                {printedPass === passItems.length ? '인쇄에서 모두 빼기' : '모두 인쇄에 넣기'}</button>}
            </div>
            <div id="rv-report-pass-list" className={`rv-report-pass-list${passOpen ? ' is-open' : ''}`} inert={!passOpen}>
              <div>{passItems.map(renderItem)}</div>
            </div>
            {printedPass < passItems.length && <p className="rv-report-pass-print">{printedPass === 0
              ? `충족 ${passItems.length}건은 인쇄에서 뺐습니다.` : `충족 ${passItems.length}건 중 담당자가 고른 ${printedPass}건만 실었습니다.`}</p>}
          </div>}
        </> : <p className="rv-report-muted">기록된 검사 결과가 없습니다.</p>}
      </section>

      {context.actions.length > 0 && <section aria-labelledby="rv-report-todo">
        <h2 id="rv-report-todo">고칠 것·확인할 것</h2>
        <ol className="rv-report-todo">{context.actions.map((action, index) => <li key={index}>
          <span className={`rv-report-chip is-${action.kind === '수정' ? 'fail' : 'review'}`}>{action.kind}</span><span>{action.text}</span>
        </li>)}</ol>
      </section>}

      <section aria-label="보고서 안내" className="rv-report-notes">
        {changeReasons.map((text, index) => <p key={'change' + index}>수정 요청 사유 · {text}</p>)}
        <ul>{context.notes.map(text => <li key={text}>{text}</li>)}</ul>
      </section>

      {run.report && <details className="rv-report-raw"><summary>보고서 원문 보기</summary>
        <p>{run.report.summary}</p>
        {run.report.change_requests?.length > 0 && <ul>{run.report.change_requests.map((text, index) => <li key={index}>{text}</li>)}</ul>}
      </details>}
    </div></div>
    <ConfirmationForm form={form} />
    {viewing && marks[viewing] && <LocationViewer key={viewing} mark={marks[viewing]} image={image}
      verdict={context.items.find(item => item.criterion_id === viewing).verdict} onClose={() => setViewing(null)} />}
  </article>;
}
