import React from 'react';
import { stageTitle, STATUS_LABELS } from './modeltest.js';
import { elapsedSeconds, formatDuration, modelTimings } from './modeltestResults.js';
import ModeltestHelp from './ModeltestHelp.jsx';

export function ModelTimingTable({ stages, now }) {
  const rows = modelTimings(stages, now);
  return rows.length ? <div className="mt-timing-scroll"><table className="mt-timing-table" aria-label="모델별 누적 소요 시간">
    <caption><span>모델별 누적 소요 시간</span> <ModeltestHelp label="모델별 누적 소요 시간"><p>로딩에는 모델 다운로드·전환이, 처리에는 입력 전처리·추론·결과 변환이 포함됩니다. 같은 모델·리비전은 합산하며, 호출 수에는 실패하거나 진행 중인 호출도 포함됩니다.</p></ModeltestHelp></caption>
    <thead><tr><th scope="col">모델</th><th scope="col">로딩</th><th scope="col">처리</th><th scope="col">합계</th><th scope="col">호출 수</th></tr></thead>
    <tbody>{rows.map(row => <tr key={row.key}>
      <th scope="row">{row.model_id}{row.revision && <small>@ {row.revision}</small>}{(row.running || row.estimated) && <small>{row.running ? '실행 중 · 추정 경과' : '중단 시점 추정'}</small>}</th>
      <td>{formatDuration(row.load_seconds)}</td><td>{formatDuration(row.inference_seconds)}</td>
      <td>{formatDuration(row.load_seconds + row.inference_seconds)}</td><td>{row.calls}회</td>
    </tr>)}</tbody>
  </table></div> : <p className="mt-hint">모델별 시간은 계측 기능이 적용된 새 실행부터 표시됩니다. 아직 모델이 시작되지 않았거나 서버가 시간을 제공하지 않으면 표시되지 않습니다.</p>;
}

export default function ModeltestTimings({ job, now }) {
  return <div className="mt-timings">
    <div className="mt-timing-scroll"><table className="mt-timing-table" aria-label="단계별 소요 시간">
      <caption><span>단계별 소요 시간</span> <ModeltestHelp label="단계별 소요 시간"><p>단계 시간에는 파일 처리와 결과 저장·전송도 포함되므로 모델 시간의 합계와 다를 수 있습니다. 병렬 실행에서는 누적 시간과 전체 경과 시간이 다릅니다. 실행 중 시간은 추정 경과이며, 완료 후 서버 측정값으로 확정됩니다.</p></ModeltestHelp></caption>
      <thead><tr><th scope="col">단계</th><th scope="col">상태</th><th scope="col">소요 시간</th></tr></thead>
      <tbody>{job.stages.map(stage => <tr key={stage.id}><th scope="row">{stageTitle(stage)}{stage.agent_id && <small>{job.agents?.find(agent => agent.id === stage.agent_id)?.name}</small>}</th>
        <td>{STATUS_LABELS[stage.status]}</td><td>{formatDuration(elapsedSeconds(stage, now))}{(stage.status === 'running' || stage.timing_estimated) && <small>추정</small>}</td>
      </tr>)}</tbody>
    </table></div>
    <ModelTimingTable stages={job.stages} now={now} />
  </div>;
}
