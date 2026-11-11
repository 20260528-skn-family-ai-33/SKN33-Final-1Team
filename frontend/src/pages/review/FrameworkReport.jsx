import React, { useEffect, useMemo, useRef, useState } from 'react';
import ImageStage from '../../components/ImageStage.jsx';
import { buildReportContext, posterMarks } from './reportBrief.js';
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
      overlays={overlays} selectedOverlayId={mark.criterion_id} viewKey={mark.criterion_id}
      empty={<p>포스터 이미지를 불러올 수 없습니다.</p>} />
    <p className="rv-report-viewer-note">상자는 검사 모델이 읽은 위치입니다. 실제 포스터와 맞는지 담당자가 확인해 주세요.</p>
  </dialog>;
}

function downloadJson(context) {
  const blob = new Blob([JSON.stringify(context, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = Object.assign(document.createElement('a'), { href: url, download: `report-${context.run_id || 'run'}.json` });
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export default function FrameworkReport({ run, projectName, image = null, standalone = false }) {
  const context = useMemo(() => buildReportContext(run, { projectName }), [run, projectName]);
  const marks = useMemo(() => Object.fromEntries(posterMarks(run, context.items).marks.map(mark => [mark.criterion_id, mark])), [run, context]);
  const [viewing, setViewing] = useState(null);
  const changeReasons = (run.approvals || []).filter(approval => approval.decision === 'approve_changes' && approval.reason).map(approval => approval.reason);
  const status = run.human_decision === 'final_approved' ? '담당자 최종 승인' : context.synthetic ? '합성 데이터 · 자동 검수 초안' : '자동 검수 초안';
  const { counts } = context;
  return <article className="rv-report-print rv-poster-report" aria-label="검수 보고서">
    <div className="rv-report-actions">
      <button type="button" className="rv-button secondary" onClick={() => downloadJson(context)}>보고서 데이터(JSON)</button>
      <button type="button" className="rv-button secondary" onClick={() => window.print()}>인쇄 / PDF 저장</button>
    </div>
    <div className="rv-report-shell"><div className="rv-report-paper">
      <header className="rv-report-heading">
        <div><strong>Creative Checker</strong><span>{status}</span></div>
        {standalone ? <h1 id="page-title" tabIndex={-1}>검수 보고서</h1> : <h2>검수 보고서</h2>}
        <p>{projectName ? `${projectName} · 포스터 검수` : '포스터 검수'}</p>
        <dl className="rv-report-meta">
          <div><dt>검수 시각</dt><dd>{context.checked_at || '기록 없음'}</dd></div>
          <div><dt>포스터</dt><dd>{context.target.name || context.target.version || '정보 없음'}</dd></div>
          <div><dt>승인 기준</dt><dd>{context.criteria.document ? `${context.criteria.document} · ` : ''}{context.criteria.version || '정보 없음'}</dd></div>
          <div><dt>담당자 결정</dt><dd>{decisions[run.human_decision] || '기록 확인 필요'}</dd></div>
        </dl>
      </header>

      <section aria-labelledby="rv-report-summary">
        <h2 id="rv-report-summary">판정 요약</h2>
        <div className="rv-report-machine"><StatusChip verdict={context.verdict} /><span>기계 판정 · {context.verdict.label}</span></div>
        <div className="rv-report-counts">
          <span><strong>필수</strong> {counts.required}건</span>
          <span className="is-pass"><strong>충족</strong> {counts.pass}건</span>
          <span className="is-review"><strong>확인 필요</strong> {counts.review}건</span>
          <span className="is-fail"><strong>불충족</strong> {counts.fail}건</span>
          {counts.optional > 0 && <span><strong>권장</strong> {counts.optional}건 · 판정 제외</span>}
        </div>
      </section>

      <section aria-labelledby="rv-report-details">
        <h2 id="rv-report-details">요구사항별 결과</h2>
        {context.items.length ? context.items.map((item, index) => <div className="rv-report-result" key={item.criterion_id}>
          <div className="rv-report-result-heading"><h3>{pad(index + 1)}. {item.label}{item.required ? '' : ' · 권장'}</h3>
            <div className="rv-report-result-side">
              {marks[item.criterion_id] ? <button type="button" className="rv-report-locate" aria-haspopup="dialog"
                aria-label={`${pad(index + 1)}. ${item.label} 위치 보기`} onClick={() => setViewing(item.criterion_id)}>위치 보기</button>
                : item.verdict.code !== 'pass' && <span className="rv-report-locate-none">위치 정보 없음</span>}
              <StatusChip verdict={item.verdict} />
            </div></div>
          <p>기준 {item.expected ? `‘${item.expected}’` : '미정'}{item.source ? ` · ${item.source}` : ''}</p>
          <p><b>관측</b> {item.observed ? `‘${item.observed}’` : item.observation}{item.verdict.code !== 'pass' && item.location ? ` · ${item.location}` : ''}</p>
          {item.reason && <p className="rv-report-muted">{item.reason}</p>}
          {item.human && <p className="rv-report-human"><b>담당자 확인 · {item.human.verdict.label}</b> {item.human.note}</p>}
        </div>) : <p className="rv-report-muted">기록된 검사 결과가 없습니다.</p>}
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
    {viewing && marks[viewing] && <LocationViewer key={viewing} mark={marks[viewing]} image={image}
      verdict={context.items.find(item => item.criterion_id === viewing).verdict} onClose={() => setViewing(null)} />}
  </article>;
}
