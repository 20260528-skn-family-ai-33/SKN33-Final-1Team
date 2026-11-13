import React, { useEffect, useMemo, useState } from 'react';
import { EvidenceDialogShell, EvidenceFrame, EvidenceRelations, boxZoom } from '../../components/EvidenceDialogParts.jsx';
import { Icon } from '../../components/Icon.jsx';
import { evidenceGraph } from './evidenceGraph.js';
import { frameworkPrefix } from './frameworkClient.js';

// 검수 항목을 누르면 열리는 판정 근거 창. 왼쪽은 검수 포스터와 읽은 위치 상자, 오른쪽은 근거 관계도다.
// 창 틀·근거 이미지 칸·관계도는 동영상 판정 근거 창과 함께 쓴다(components/EvidenceDialogParts.jsx).
const imagePath = assetVersionId => assetVersionId ? frameworkPrefix + '/images/' + encodeURIComponent(assetVersionId) + '/content' : null;

// 검수 포스터와 읽은 위치 상자. 상자는 0~1 비율이라 이미지 크기를 몰라도 그릴 수 있다.
function PosterPane({ graph, image, shownId, onShow }) {
  const [zoomed, setZoomed] = useState(false);
  const [fitted, setFitted] = useState(false);
  const current = { runId: null, name: graph.versionName, current: true, verdict: graph.verdict, value: graph.observed,
    statusLabel: graph.observation, box: graph.box, assetVersionId: graph.assetVersionId };
  const shown = graph.trail.find(point => !point.current && point.runId === shownId) || current;
  const src = shown.current ? image?.url || imagePath(graph.assetVersionId) : imagePath(shown.assetVersionId);
  const box = shown.box;
  const { canZoom } = boxZoom(box);
  useEffect(() => { setZoomed(false); }, [src]);
  const title = shown.name ? `${shown.name} 포스터` : '검수 포스터';
  return <section className="rv-evd-poster" aria-label="검수 포스터">
    <header className="rv-evd-poster-head">
      <Icon name="image" /><strong>{title}</strong>
      {graph.trail.length > 0 && <span>{shown.current ? '지금 검수본' : '이전 검수본'}</span>}
    </header>
    <EvidenceFrame src={src} alt={`${title}${box ? ', 읽은 위치를 상자로 표시' : ''}`} box={box} tone={shown.verdict.code} zoomed={zoomed} fit="model" onFit={setFitted}
      ratio={shown.current && image?.width > 0 && image?.height > 0 ? image.width / image.height : null}
      empty="포스터 이미지를 표시할 수 없습니다." />
    <div className="rv-evd-poster-caption">
      <p>{shown.current
        ? box ? <>읽은 위치 <b>{graph.hint === '전체' ? '포스터 전체' : `포스터 ${graph.hint}`}</b></> : '이 항목은 위치 상자가 없어 포스터 전체를 보여 줍니다.'
        : <>{shown.name}에서 읽은 값 <b>{shown.value ? `‘${shown.value}’` : shown.statusLabel}</b> · {shown.verdict.label}</>}</p>
      {box && canZoom && <button type="button" className="rv-evd-zoom" aria-pressed={zoomed} onClick={() => setZoomed(value => !value)}>
        <Icon name="search" />{zoomed ? '전체 보기' : '상자 확대'}</button>}
    </div>
    {graph.trail.length > 0 && <div className="rv-evd-trail" role="group" aria-label="버전별 판정">
      <span>버전별 판정</span>
      <ol>{graph.trail.map(point => <li key={point.runId}>
        <button type="button" className={`rv-evd-version is-${point.verdict.code}`} aria-pressed={shown.current ? point.current : point.runId === shown.runId}
          onClick={() => onShow(point.current ? null : point.runId)}>
          <span>{point.name}</span><b><i aria-hidden="true" />{point.verdict.label}</b>{point.current && <small>지금</small>}
        </button></li>)}</ol>
    </div>}
    <p className="rv-evd-poster-note">{fitted ? '상자는 검사 모델이 알려 준 위치를 둘레의 글자에 맞춰 다듬은 것입니다.' : '상자는 검사 모델이 읽은 위치입니다.'} 실제 포스터와 맞는지 확인해 주세요.</p>
  </section>;
}

function EvidenceBody({ graph, image }) {
  const [selectedId, setSelectedId] = useState('verdict');
  const [shownId, setShownId] = useState(null);
  function select(node) {
    setSelectedId(node.id);
    if (node.id === 'previous') setShownId(node.runId);
    else if (['poster', 'region', 'observation'].includes(node.id)) setShownId(null);
  }
  const source = graph.relationSource === 'neo4j' ? '기준과 원문의 관계는 Neo4j 그래프에서 조회했고, 읽은 값·위치·판정은 이 검수의 실행 기록에서 읽어 연결했습니다.'
    : graph.relationSource === 'postgresql' ? '기준과 원문의 관계는 PostgreSQL에서 조회했고, 읽은 값·위치·판정은 이 검수의 실행 기록에서 읽어 연결했습니다.'
      : '기준·원문·읽은 값·판정을 이 검수의 실행 기록에서 읽어 연결했습니다.';
  return <div className="rv-evd-body">
    <PosterPane graph={graph} image={image} shownId={shownId} onShow={setShownId} />
    <section className="rv-evd-main" aria-label="판정 근거">
      <div className={`rv-evd-criterion is-${graph.verdict.code}`}>
        <p className="rv-evd-criterion-text">{graph.text}</p>
        <dl className="rv-evd-compare">
          <div><dt>기대값</dt><dd>{graph.expected ? `‘${graph.expected}’` : '미정'}</dd></div>
          <div><dt>읽은 값</dt><dd>{graph.observed ? `‘${graph.observed}’` : graph.observation}</dd></div>
          {graph.operator && <div><dt>비교</dt><dd>{graph.operator}</dd></div>}
        </dl>
        {graph.reasons.length > 0 && <ul className="rv-evd-reasons" aria-label="판정 사유">{graph.reasons.map(text => <li key={text}>{text}</li>)}</ul>}
      </div>
      <EvidenceRelations graph={graph} selectedId={selectedId} onSelect={select} foot={source}
        store={selected => <>{selected.graph ? <>그래프 노드 <code title={selected.graph.key}>{selected.graph.kind}</code></> : selected.record}
          {selected.runId && <a href={'?framework=' + encodeURIComponent(selected.runId)}>이 검수 결과 열기</a>}</>} />
    </section>
  </div>;
}

export default function PosterEvidenceDialog({ run, criterionId, number, position, image, history, runs, retrieval, onOpen, onClose, onPrev, onNext }) {
  const graph = useMemo(() => evidenceGraph(run, criterionId, { image, history, runs, retrieval }),
    [run, criterionId, image, history, runs, retrieval]);
  return <EvidenceDialogShell number={number} title={graph ? graph.label : '검수 항목'} verdict={graph?.verdict}
    tag={graph && (graph.required ? '필수 조건' : '참고 조건')} position={position} onOpen={onOpen} onClose={onClose} onPrev={onPrev} onNext={onNext}>
    {graph ? <EvidenceBody key={criterionId} graph={graph} image={image} />
      : <p className="rv-evd-missing">이 항목의 판정 기록을 찾지 못했습니다.</p>}
  </EvidenceDialogShell>;
}
