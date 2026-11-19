import React, { useEffect, useMemo, useRef, useState } from 'react';
import VideoEvidenceDialog from './VideoEvidenceDialog.jsx';
import { REPORT_COMMENT_MAX, readReportComments, writeReportComments } from '../review/reportComments.js';
import { buildConfirmationForm } from '../review/confirmationForm.js';
import ConfirmationForm from '../review/ConfirmationForm.jsx';
import { coverageNote, formatTime, judgmentItems, statusLabels } from './videoFormat.js';
import '../../styles/framework-report.css';

// 동영상 검수 보고서(2026-11-17). 포스터 보고서(review/FrameworkReport.jsx)와 같은 짜임·같은 모양으로 맞췄다:
// 머리(상태 · 검수 시각 · 담당자 결정) → 판정 요약(기계 판정 칩 · 개수) → 요구사항별 결과(위치 보기 · 상태 칩 ·
// 확인 필요 항목의 담당자 코멘트) → 고칠 것·확인할 것 → 안내. 판정은 서버 결과를 바꾸지 않는다.
// 포스터의 '위치 보기'(포스터 위 상자) 자리는 결과 화면과 같은 판정 근거 창(영상 프레임 · 시각)을 연다.
// 2026-11-18: 포스터와 같이 검수 확인서 인쇄, 충족 항목 접기·펼치기, 충족 항목의 인쇄 포함 선택을 넣었다.
// 포스터의 '보고서 원문 보기'(A07 원문)는 동영상 검수에 보고서 원문이 없어 두지 않는다.
const decisions = { unreviewed: '검토 대기', changes_approved: '수정 요청 승인', final_approved: '최종 승인', exception_approved: '예외 승인' };
// 칩 색은 포스터 보고서와 같은 세 가지다. 대상 아님·생략은 색 없이 이름만 보인다.
const verdictLabels = { pass: '충족', review: '확인 필요', fail: '불충족' };
const verdictOf = status => verdictLabels[status] ? { code: status, label: verdictLabels[status] } : { code: 'other', label: statusLabels[status] || '확인 필요' };
const pad = number => String(number).padStart(2, '0');
const span = item => Number.isFinite(item.start_ms) && Number.isFinite(item.end_ms) ? `${formatTime(item.start_ms)}–${formatTime(item.end_ms)}`
  : Number.isFinite(item.time_ms) ? formatTime(item.time_ms) : null;
// 문서에서 온 기준의 출처('파일 · 3페이지 · …\n원문')는 첫 줄만, 직접 입력은 '직접 입력'으로 보인다.
// 출처가 기준 문장 그대로인 기준도 직접 입력이다.
const sourceLine = (source, text) => !source || source === 'user' || source === text ? '직접 입력' : String(source).split('\n')[0];

function StatusChip({ verdict }) {
  return <span className={`rv-report-chip is-${verdict.code}`}><i aria-hidden="true" />{verdict.label}</span>;
}

function checkedAt(job) {
  const date = new Date(job.updated_at || job.created_at || '');
  return Number.isNaN(date.getTime()) ? null : date.toLocaleString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export default function VideoReport({ job }) {
  const entries = useMemo(() => judgmentItems(job), [job]);
  const items = useMemo(() => entries.map((entry, index) => {
    const result = entry.result || {};
    const criterion = job.criteria?.find(item => item.id === result.criterion_id);
    const times = (result.evidence_segments || []).map(span).filter(Boolean);
    const label = result.criterion || criterion?.text || '검수 항목';
    return { entry, number: pad(index + 1), id: result.criterion_id || entry.key, label,
      verdict: verdictOf(entry.status), observed: result.observed_text, reason: result.reason, source: sourceLine(criterion?.source, criterion?.text || label),
      times, located: (result.evidence_segments || []).length > 0 };
  }), [entries, job.criteria]);
  const [viewing, setViewing] = useState(null);
  // 확인 필요 항목의 담당자 코멘트. 포스터 보고서와 같은 저장 방식(이 브라우저, 검수별)이고 열쇠 앞에 video:를 붙여 섞이지 않게 한다.
  const commentKey = job.id ? 'video:' + job.id : null;
  const [comments, setComments] = useState(() => readReportComments(commentKey));
  const [commentSaved, setCommentSaved] = useState(true);
  const [commentJob, setCommentJob] = useState(commentKey);
  if (commentJob !== commentKey) { setCommentJob(commentKey); setComments(readReportComments(commentKey)); setCommentSaved(true); }
  const updateComment = (id, text) => {
    const next = { ...comments, [id]: text };
    setComments(next);
    setCommentSaved(writeReportComments(commentKey, next));
  };
  // 충족 항목은 처음에 접어 두고 단추로 펼친다. 인쇄·PDF에는 충족 항목을 빼고, 담당자가 고른 항목만 싣는다(포스터와 같다).
  const passRef = useRef(null);
  const scrollOnOpen = useRef(false);
  const [passOpen, setPassOpen] = useState(false);
  const [printPass, setPrintPass] = useState(() => new Set());
  const [passJob, setPassJob] = useState(commentKey);
  if (passJob !== commentKey) { setPassJob(commentKey); setPassOpen(false); setPrintPass(new Set()); }
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

  const counts = items.reduce((total, item) => ({ ...total, [item.verdict.code]: (total[item.verdict.code] || 0) + 1 }), {});
  const machine = !items.length ? verdictOf('review') : counts.fail ? verdictOf('fail') : counts.review || counts.other ? verdictOf('review') : verdictOf('pass');
  const decision = job.workflow?.human_decision || 'unreviewed';
  const status = decision === 'final_approved' ? '담당자 최종 승인' : '자동 검수 초안';
  const hasReviewItems = items.some(item => item.verdict.code === 'review');
  const actions = items.filter(item => item.verdict.code === 'fail' || item.verdict.code === 'review').map(item => ({
    kind: item.verdict.code === 'fail' ? '수정' : '확인',
    text: `${item.number}. ${item.label}${item.observed ? ` — 관측 ‘${item.observed}’` : ''}${item.times.length ? ` · ${item.times.slice(0, 3).join(', ')}` : ''}`,
  }));
  const reasons = (job.workflow?.decisions || []).filter(record => record.reason).map(record => record.reason);
  const media = job.media;
  const notes = [
    media && `검수 대상 · 영상 ${formatTime(media.duration_ms)} · ${media.width}×${media.height}`,
    coverageNote(job.coverage),
    '판정은 검사한 표본 시각을 기준으로 합니다. 확인 필요 항목은 원본 영상에서 확인해 주세요.',
  ].filter(Boolean);
  const viewed = viewing ? items.find(item => item.id === viewing) : null;

  // '검수 확인서 인쇄': 포스터와 같은 공공기관 보고서 형식 확인서(ConfirmationForm)만 인쇄한다.
  // 대상 아님·생략(기타)은 판정 제외 항목으로 싣는다. 동영상 기준은 문장 하나라 기대값 대신 근거 시각을 적는다.
  const [printingForm, setPrintingForm] = useState(false);
  const form = useMemo(() => {
    const context = {
      items: items.map(item => ({ criterion_id: item.id, label: item.label, required: item.verdict.code !== 'other', verdict: item.verdict,
        observed: item.observed, observation: '읽은 문구 없음', reason: item.reason,
        basis: item.times.length ? `근거 시각 ${item.times.slice(0, 3).join(', ')}` : null })),
      counts: { required: items.length - (counts.other || 0), pass: counts.pass || 0, review: counts.review || 0, fail: counts.fail || 0, optional: counts.other || 0 },
      project: { name: job.project_name || null }, checked_at: checkedAt(job), run_id: job.id || null, verdict: machine, notes,
    };
    return buildConfirmationForm(context, { comments, decision, subject: '동영상', optionalLabel: '기타' });
  }, [items, counts.other, counts.pass, counts.review, counts.fail, job, machine, notes, comments, decision]);
  useEffect(() => {
    if (!printingForm) return;
    window.print();
    setPrintingForm(false);
  }, [printingForm]);

  const passItems = items.filter(item => item.verdict.code === 'pass');
  const otherItems = items.filter(item => item.verdict.code !== 'pass');
  const printedPass = passItems.filter(item => printPass.has(item.id)).length;
  const renderItem = item => {
    const passed = item.verdict.code === 'pass';
    const printed = !passed || printPass.has(item.id);
    const commentable = item.verdict.code === 'review';
    const comment = comments[item.id] || '';
    return <div className={`rv-report-result${commentable ? ' has-comment' : ''}${printed ? '' : ' is-print-excluded'}`} key={item.id}>
      <div className="rv-report-result-heading"><h3>{item.number}. {item.label}</h3>
        <div className="rv-report-result-side">
          {passed && <label className="rv-report-print-pick"><input type="checkbox" checked={printed}
            onChange={() => togglePrint(item.id)} aria-label={`${item.number}. ${item.label} 인쇄에 포함`} />인쇄에 포함</label>}
          {item.located ? <button type="button" className="rv-report-locate" aria-haspopup="dialog"
            aria-label={`${item.number}. ${item.label} 위치 보기`} onClick={() => setViewing(item.id)}>위치 보기</button>
            : !passed && <span className="rv-report-locate-none">위치 정보 없음</span>}
          <StatusChip verdict={item.verdict} />
        </div></div>
      <div className="rv-report-result-body"><div>
        <p>기준 출처 · {item.source}</p>
        <p><b>관측</b> {item.observed ? `‘${item.observed}’` : '읽은 문구 없음'}{item.times.length ? ` · ${item.times.slice(0, 3).join(', ')}` : ''}</p>
        {item.reason && <p className="rv-report-muted">{item.reason}</p>}
        {commentable && comment.trim() && <p className="rv-report-comment-print"><b>담당자 코멘트</b> {comment.trim()}</p>}
      </div>
      {commentable && <textarea className="rv-report-comment" rows={2} maxLength={REPORT_COMMENT_MAX}
        placeholder="담당자 코멘트" aria-label={`${item.number}. ${item.label} 담당자 코멘트`}
        value={comment} onChange={event => updateComment(item.id, event.target.value)} />}
      </div>
    </div>;
  };

  return <article className={`rv-report-print rv-poster-report rv-video-report${printingForm ? ' is-printing-form' : ''}`} aria-label="검수 보고서">
    <div className="rv-report-actions">
      <button type="button" className="rv-button secondary" onClick={() => setPrintingForm(true)}>검수 확인서 인쇄</button>
      <button type="button" className="rv-button secondary" onClick={() => window.print()}>인쇄 / PDF 저장</button>
    </div>
    <div className="rv-report-shell"><div className="rv-report-paper">
      <header className="rv-report-heading">
        <div><strong>Creative Checker</strong><span>{status}</span></div>
        <h1 id="page-title" tabIndex={-1}>검수 보고서</h1>
        <p>{`${job.project_name || '숏폼 검수'} · 동영상 검수`}</p>
        <dl className="rv-report-meta">
          <div><dt>검수 시각</dt><dd>{checkedAt(job) || '기록 없음'}</dd></div>
          <div><dt>담당자 결정</dt><dd>{decisions[decision] || '기록 확인 필요'}</dd></div>
        </dl>
      </header>

      <section aria-labelledby="rv-report-summary">
        <h2 id="rv-report-summary">판정 요약</h2>
        <div className="rv-report-machine"><StatusChip verdict={machine} /><span>기계 판정 · {machine.label}</span></div>
        <div className="rv-report-counts">
          <span><strong>전체</strong> {items.length}건</span>
          {passItems.length > 0 ? <button type="button" className="rv-report-count-jump is-pass" aria-controls="rv-report-pass-list"
            onClick={openPass}><strong>충족</strong> {counts.pass}건 <span className="rv-report-count-arrow" aria-hidden="true">↓</span></button>
            : <span className="is-pass"><strong>충족</strong> 0건</span>}
          <span className="is-review"><strong>확인 필요</strong> {counts.review || 0}건</span>
          <span className="is-fail"><strong>불충족</strong> {counts.fail || 0}건</span>
          {counts.other > 0 && <span><strong>기타</strong> {counts.other}건 · 판정 제외</span>}
        </div>
      </section>

      <section aria-labelledby="rv-report-details">
        <h2 id="rv-report-details">요구사항별 결과</h2>
        {hasReviewItems && <p className="rv-report-comment-hint">
          {commentSaved ? '확인 필요 항목의 담당자 코멘트는 이 브라우저에 저장되고, 인쇄·PDF에 함께 나옵니다.'
            : '이 브라우저에 저장할 수 없어요. 새로고침하면 코멘트가 사라집니다.'}</p>}
        {items.length ? <>
          {otherItems.map(renderItem)}
          {passItems.length > 0 && <div className="rv-report-pass" ref={passRef}>
            <div className="rv-report-pass-bar">
              <button type="button" className="rv-report-pass-toggle" aria-expanded={passOpen} aria-controls="rv-report-pass-list"
                onClick={() => passOpen ? setPassOpen(false) : openPass()}>
                충족 항목 {passItems.length}건 {passOpen ? '접기' : '펼쳐 보기'} <span aria-hidden="true">{passOpen ? '▴' : '▾'}</span></button>
              <span className="rv-report-pass-count">인쇄 포함 {printedPass}/{passItems.length}</span>
              {passOpen && <button type="button" className="rv-report-pass-all"
                onClick={() => setPrintPass(printedPass === passItems.length ? new Set() : new Set(passItems.map(item => item.id)))}>
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

      {actions.length > 0 && <section aria-labelledby="rv-report-todo">
        <h2 id="rv-report-todo">고칠 것·확인할 것</h2>
        <ol className="rv-report-todo">{actions.map((action, index) => <li key={index}>
          <span className={`rv-report-chip is-${action.kind === '수정' ? 'fail' : 'review'}`}>{action.kind}</span><span>{action.text}</span>
        </li>)}</ol>
      </section>}

      <section aria-label="보고서 안내" className="rv-report-notes">
        {reasons.map((text, index) => <p key={'reason' + index}>담당자 결정 사유 · {text}</p>)}
        <ul>{notes.map(text => <li key={text}>{text}</li>)}</ul>
      </section>
    </div></div>
    <ConfirmationForm form={form} />
    {viewed && <VideoEvidenceDialog job={job} item={viewed.entry} number={viewed.number} onClose={() => setViewing(null)} />}
  </article>;
}
