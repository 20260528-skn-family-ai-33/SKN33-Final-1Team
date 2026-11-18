import React from 'react';
import { DECISION_OPTIONS } from './confirmationForm.js';
import '../../styles/confirmation-form.css';

// 공공기관 보고서 형식의 검수 확인서(2쪽). 화면에는 보이지 않고, 보고서의 '검수 확인서 인쇄'를 누를 때만 인쇄된다.
// 내용은 confirmationForm.js가 보고서 context로 만든다. 확인 결과·결정 칸은 기록이 없으면 빈 □로 둔다.
const box = checked => checked ? '■' : '□';

function Page({ number, title, sub, children }) {
  return <section className="rv-confirm-page">
    <h1 className="rv-confirm-title">{title}</h1>
    <p className="rv-confirm-note">{sub}</p>
    {children}
    <footer className="rv-confirm-footer"><div>Creative Checker · 검수 확인서</div>- {number} -</footer>
  </section>;
}

const Heading = ({ mark, children }) => <h2 className="rv-confirm-heading"><b>{mark}</b>{children}</h2>;

export default function ConfirmationForm({ form }) {
  const { counts } = form;
  const sub = [form.target, form.criteriaVersion && `기준안 ${form.criteriaVersion}`, form.status].filter(Boolean).join('  |  ');
  return <div className="rv-confirm-form" aria-hidden="true">
    <Page number={1} title={form.title} sub={`Creative Checker  |  ${form.status}  |  검수 시각: ${form.checkedAt}`}>
      <Heading mark="Ⅰ">검수 개요</Heading>
      <p className="rv-confirm-lead">□ 검수 대상 및 적용 기준</p>
      <p className="rv-confirm-body">○ 검수 대상: {form.target}</p>
      {form.targetVersion && <p className="rv-confirm-note">   - 포스터 버전 {form.targetVersion}</p>}
      <p className="rv-confirm-body">○ 기준 문서: {form.criteriaDocument || '실행 기록에 문서 이름 없음'}</p>
      <p className="rv-confirm-body">○ 기준안: {form.criteriaVersion || '기록 없음'}</p>
      <p className="rv-confirm-note">   - 필수 {counts.required}항목{counts.optional ? ` · 권장 ${counts.optional}항목(판정 제외)` : ''}</p>
      {form.runId && <p className="rv-confirm-body">○ 실행 번호: {form.runId}</p>}

      <Heading mark="Ⅱ">검수 결과</Heading>
      <p className="rv-confirm-lead">□ 기계 판정: {form.verdict}{counts.fail ? `(불충족 ${counts.fail}건)` : ''}</p>
      <table className="rv-confirm-table">
        <thead><tr><th>필수 기준</th><th>충족</th><th>확인 필요</th><th>불충족</th></tr></thead>
        <tbody><tr><td>{counts.required}건</td><td>{counts.pass}건</td><td>{counts.review}건</td><td>{counts.fail}건</td></tr></tbody>
      </table>
      <p className="rv-confirm-note">※ 항목별 기준·관측·근거는 검수 보고서의 ‘요구사항별 결과’에 있으며, 아래 번호는 보고서 항목 번호와 같습니다.</p>

      <Heading mark="Ⅲ">수정 요청 사항</Heading>
      {form.fixes.length ? <table className="rv-confirm-table is-fix">
        <thead><tr><th>번호 / 항목</th><th>관측 결과</th><th>기준 및 수정 요청</th></tr></thead>
        <tbody>{form.fixes.map(fix => <tr key={fix.number}><td>{fix.number} / {fix.label}</td><td>{fix.observed}</td><td>{fix.request}</td></tr>)}</tbody>
      </table> : <p className="rv-confirm-body">○ 불충족 필수 항목이 없습니다.</p>}
      {form.notes.map(text => <p className="rv-confirm-note" key={text}>※ {text}</p>)}
    </Page>

    <Page number={2} title="검수 확인 및 담당자 결정" sub={sub}>
      <Heading mark="Ⅳ">담당자 확인 사항</Heading>
      <p className="rv-confirm-lead">□ 확인 필요 필수 항목 {form.checks.length}건</p>
      {form.checks.length ? <table className="rv-confirm-table is-check">
        <thead><tr><th>번호</th><th>항목</th><th>확인할 내용</th><th>확인 결과</th></tr></thead>
        <tbody>{form.checks.map(check => <tr key={check.number}>
          <td>{check.number}</td><td>{check.label}</td><td>{check.content}</td>
          <td>{check.human ? `담당자 확인 · ${check.human}` : '□ 충족  □ 불충족'}
            {check.comment && <span className="rv-confirm-comment">{check.comment}</span>}</td>
        </tr>)}</tbody>
      </table> : <p className="rv-confirm-body">○ 확인 필요 필수 항목이 없습니다.</p>}
      {form.optional.length > 0 && <p className="rv-confirm-note">※ 권장 항목(판정 제외): {form.optional.join(', ')}</p>}

      <Heading mark="Ⅴ">최종 결정 및 서명</Heading>
      <p className="rv-confirm-body">○ 위 대상·기준안과 항목별 결과를 검토하고 아래와 같이 결정합니다. 파일 또는 요구사항이 바뀌면 다시 검수합니다.</p>
      <p className="rv-confirm-note">   - 적용 범위: 해당 포스터 및 기준안 {form.criteriaVersion || '(기록 없음)'}</p>
      <table className="rv-confirm-table is-sign">
        <tbody>
          <tr><th>담당자 결정</th><td>{DECISION_OPTIONS.map(option => `${box(form.decision === option.code)} ${option.label}`).join('   ')}</td></tr>
          <tr><th>확인 결과 및 사유</th><td>{'　'}</td></tr>
          <tr><th>검수 담당자</th><td>성명:                                   (서명)</td></tr>
          <tr><th>확인 일자</th><td>          년          월          일</td></tr>
        </tbody>
      </table>
      <p className="rv-confirm-note">※ 자동 검수는 담당자 판단을 돕는 자료이며, 최종 사용·게시 여부는 담당자가 결정합니다.</p>
    </Page>
  </div>;
}
