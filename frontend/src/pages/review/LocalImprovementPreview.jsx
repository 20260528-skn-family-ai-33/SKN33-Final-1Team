import React, { useEffect, useState } from 'react';
import ReviewLayout from '../../layouts/ReviewLayout.jsx';
import VideoResults from '../video/VideoResults.jsx';
import { DocumentAnalysisProgress } from './DocumentInput.jsx';
import FrameworkRunStatus from './FrameworkRunStatus.jsx';
import RequirementInput from './RequirementInput.jsx';
import '../../styles/review-preview.css';
import '../../styles/review-editor-v2.css';
import '../video/videoReview.css';
import './localImprovementsPreview.css';

const demoVideo = {
  id: 'local-preview-video',
  project_name: '행사 홍보영상 예시',
  criteria_version: 1,
  media: { container: 'avi', duration_ms: 0, width: 1920, height: 1080 },
  playback: { available: false, reason: '미리보기에는 재생 영상이 포함되어 있지 않습니다.' },
  coverage: { duration_ms: 0, inspected_ms: [], uninspected: [] },
  review: { results: [{
    criterion_id: 'title', type: 'information', severity: 'info', reviewer: 'auto', status: 'pass',
    criterion: '행사 제목 표기', observed_text: '봄의 기록',
    reason: '검사한 표본 시각에서 행사 제목을 확인했습니다.', mismatch: [], evidence_segments: [],
  }] },
  criteria: [{ id: 'title', source: '요구사항 문서 · 1쪽' }],
  frames: [], tracks: [], segments: [], warnings: [],
};

const demoLogo = { jobs: [{
  id: 'local-preview-logo', title: '행사 홍보영상 예시 · 주최 로고', status: 'running', progress: 72,
  stage_detail: '표본 프레임에서 로고 후보를 찾고 있습니다.',
}] };

const tabs = [
  ['result', '영상 검수 결과'],
  ['slow', '문서 분석 지연'],
  ['denied', '접근 제한'],
];

export default function LocalImprovementPreview() {
  const [tab, setTab] = useState('result');
  const [requirements, setRequirements] = useState('행사 제목: 봄의 기록\n일정: 2026.11.20–11.28\n주최 로고가 표시되어야 합니다.');
  const [now, setNow] = useState(Date.now);
  const [lastCheckedAt, setLastCheckedAt] = useState(Date.now() - 1500);
  const [requestStartedAt] = useState(Date.now() - 334_000);
  const [stageStartedAt] = useState(Date.now() - 77_000);

  useEffect(() => {
    if (tab !== 'slow') return undefined;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [tab]);

  const totalSeconds = Math.max(0, Math.floor((now - requestStartedAt) / 1000));
  const stageSeconds = Math.max(0, Math.floor((now - stageStartedAt) / 1000));
  const checkAge = Math.max(0, Math.floor((now - lastCheckedAt) / 1000));
  const lastChecked = checkAge < 3 ? '방금' : checkAge < 60 ? `${checkAge}초 전` : `${Math.floor(checkAge / 60)}분 전`;
  const shellState = { step: 'results', projectName: '', criteria: [] };

  return <ReviewLayout state={shellState} busy={false} showWorkspaceBar={false} mainClassName="rv-improvements-preview">
    <div className="rv-preview-toolbar" aria-label="개선 화면 미리보기">
      <div><strong>프로젝트 개선 화면 미리보기</strong>
        <p>예시 데이터만 사용합니다. 이 화면에서는 검수·승인 요청을 보내지 않습니다.</p></div>
      <div className="rv-preview-controls" role="tablist" aria-label="미리보기 화면 상태">
        {tabs.map(([key, label]) => <button key={key} type="button" role="tab" id={`improvements-tab-${key}`}
          aria-controls={`improvements-panel-${key}`} aria-selected={tab === key} aria-pressed={tab === key}
          onClick={() => setTab(key)}>{label}</button>)}
      </div>
    </div>

    <section id="improvements-panel-result" role="tabpanel" aria-labelledby="improvements-tab-result" hidden={tab !== 'result'}>
      <div className="rv-improvement-example-label">예시 화면 · 실제 검수 결과가 아닙니다.</div>
      <div className="rv-video">
        <VideoResults job={demoVideo} logo={demoLogo} onNewReview={() => {}} onReuse={() => {}} onRevise={() => {}}
          onOpenRun={() => {}} onDecision={() => {}} onReport={() => {}} />
      </div>
    </section>

    <section id="improvements-panel-slow" className="rv-improvement-delay" role="tabpanel"
      aria-labelledby="improvements-tab-slow" hidden={tab !== 'slow'}>
      <div className="rv-improvement-delay-copy">
        <span className="rv-improvement-status">분석 중</span>
        <h1>요구사항 분석이 계속 진행 중입니다.</h1>
        <p>문서 분석이 끝날 때까지 포스터·로고·요구사항을 준비할 수 있습니다. 분석 완료 후 검수를 시작할 수 있어요.</p>
        <RequirementInput id="improvement-preview-requirements" value={requirements} onChange={setRequirements}
          placeholder="검수할 요구사항을 입력하세요." disabled={false} />
      </div>
      <aside className="rv-improvement-delay-status" aria-label="분석 상태 예시">
        <strong>현재 단계 · 요구사항 분석</strong>
        <DocumentAnalysisProgress documentProgress={{
          stage: 'extracting', percent: 60, startedAt: stageStartedAt,
          message: '문서에서 검수 요구사항과 원문 근거를 추출하고 있습니다.',
        }} totalSeconds={totalSeconds} stageSeconds={stageSeconds} lastChecked={lastChecked} cancelLabel="입력 반영 취소" />
        <p className="rv-improvement-auto-check">상태를 자동으로 확인하고 있습니다.</p>
        <button type="button" className="rv-button secondary rv-compact" onClick={() => setLastCheckedAt(Date.now())}>
          예시 상태 갱신
        </button>
      </aside>
    </section>

    <section id="improvements-panel-denied" className="rv-improvement-denied" role="tabpanel"
      aria-labelledby="improvements-tab-denied" hidden={tab !== 'denied'}>
      <div className="rv-improvement-example-label">예시 화면 · 계정별 작업 접근 제한</div>
      <FrameworkRunStatus state="denied" />
    </section>
  </ReviewLayout>;
}
