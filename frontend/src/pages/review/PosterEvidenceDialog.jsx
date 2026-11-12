import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '../../components/Icon.jsx';
import { evidenceGraph } from './evidenceGraph.js';
import { frameworkPrefix } from './frameworkClient.js';
import '../../styles/evidence-dialog.css';

// 검수 항목을 누르면 열리는 판정 근거 창. 왼쪽은 검수 포스터와 읽은 위치 상자, 오른쪽은 근거 관계도다.
// 관계도 자리는 가로 4칸(%)·세로 3줄(px)로 고정해 창 너비가 바뀌어도 선이 노드와 어긋나지 않는다.
const COL = 19.6, GAP_X = 7.2, NODE_H = 66, GAP_Y = 40;
const HEIGHT = NODE_H * 3 + GAP_Y * 2;
const left = column => column * (COL + GAP_X);
const centerX = column => left(column) + COL / 2;
const top = row => row * (NODE_H + GAP_Y);
const centerY = row => top(row) + NODE_H / 2;

// 관계선이 지나는 점([가로 %, 세로 px])과 이름표 자리. 옆 칸·아래 줄은 곧게, 그 밖은 한 번 꺾는다.
function route(edge, from, to) {
  const [c1, r1] = from.slot, [c2, r2] = to.slot;
  if (edge.id === 'judged') {   // 검수 실행 아래에서 줄 사이로 내려와 판정 위로 들어간다
    const x = centerX(c1) + COL * .27, y = top(r1) + NODE_H + GAP_Y / 2;
    return { points: [[x, top(r1) + NODE_H], [x, y], [centerX(c2), y], [centerX(c2), top(r2)]], label: [(x + centerX(c2)) / 2, y] };
  }
  if (r1 === r2) {
    const start = c2 > c1 ? left(c1) + COL : left(c1), end = c2 > c1 ? left(c2) : left(c2) + COL;
    return { points: [[start, centerY(r1)], [end, centerY(r1)]], label: [(start + end) / 2, centerY(r1)] };
  }
  const end = r2 > r1 ? top(r2) : top(r2) + NODE_H;
  if (c1 === c2) {
    const start = r2 > r1 ? top(r1) + NODE_H : top(r1);
    return { points: [[centerX(c1), start], [centerX(c1), end]], label: [centerX(c1), (start + end) / 2], beside: true };
  }
  const start = c2 > c1 ? left(c1) + COL : left(c1);
  return { points: [[start, centerY(r1)], [centerX(c2), centerY(r1)], [centerX(c2), end]], label: [(start + centerX(c2)) / 2, centerY(r1)] };
}

function Edge({ edge, nodes }) {
  const from = nodes[edge.from], to = nodes[edge.to];
  if (!from || !to) return null;
  const { points, label, beside } = route(edge, from, to);
  const [[px, py], [x, y]] = points.slice(-2);
  const heading = py === y ? (x > px ? 'right' : 'left') : (y > py ? 'down' : 'up');
  return <div className={`rv-evg-edge${edge.chain ? ' is-chain' : ''}${edge.dashed ? ' is-dashed' : ''}`} aria-hidden="true">
    {points.slice(1).map(([x2, y2], index) => {
      const [x1, y1] = points[index];
      return y1 === y2
        ? <i key={index} className="is-h" style={{ left: Math.min(x1, x2) + '%', width: Math.abs(x2 - x1) + '%', top: y1 }} />
        : <i key={index} className="is-v" style={{ left: x1 + '%', top: Math.min(y1, y2), height: Math.abs(y2 - y1) }} />;
    })}
    <b className={`is-${heading}`} style={{ left: x + '%', top: y }} />
    <span className={beside ? 'is-beside' : undefined} style={{ left: label[0] + '%', top: label[1] }}>{edge.label}</span>
  </div>;
}

function VerdictChip({ verdict }) {
  return <span className={`rv-evd-chip is-${verdict.code}`}><i aria-hidden="true" />{verdict.label}</span>;
}

const imagePath = assetVersionId => assetVersionId ? frameworkPrefix + '/images/' + encodeURIComponent(assetVersionId) + '/content' : null;

// 검수 포스터와 읽은 위치 상자. 상자는 0~1 비율이라 이미지 크기를 몰라도 그릴 수 있다.
function PosterPane({ graph, image, shownId, onShow }) {
  const [zoomed, setZoomed] = useState(false);
  const [natural, setNatural] = useState(null);
  const [failedSrc, setFailedSrc] = useState(null);
  const current = { runId: null, name: graph.versionName, current: true, verdict: graph.verdict, value: graph.observed,
    statusLabel: graph.observation, box: graph.box, assetVersionId: graph.assetVersionId };
  const shown = graph.trail.find(point => !point.current && point.runId === shownId) || current;
  const src = shown.current ? image?.url || imagePath(graph.assetVersionId) : imagePath(shown.assetVersionId);
  const box = shown.box;
  const ratio = natural?.src === src ? natural.width / natural.height
    : shown.current && image?.width > 0 && image?.height > 0 ? image.width / image.height : null;
  const scale = box ? Math.min(6, .8 / (box[2] - box[0]), .8 / (box[3] - box[1])) : 1;
  const canZoom = scale >= 1.25;
  const zoom = zoomed && canZoom ? scale : 1;
  const middle = box ? [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2] : [.5, .5];
  useEffect(() => { setZoomed(false); }, [src]);
  const title = shown.name ? `${shown.name} 포스터` : '검수 포스터';
  return <section className="rv-evd-poster" aria-label="검수 포스터">
    <header className="rv-evd-poster-head">
      <Icon name="image" /><strong>{title}</strong>
      {graph.trail.length > 0 && <span>{shown.current ? '지금 검수본' : '이전 검수본'}</span>}
    </header>
    <div className="rv-evd-frame">
      {!src || failedSrc === src ? <p className="rv-evd-frame-empty">포스터 이미지를 표시할 수 없습니다.</p>
        : <div className={`rv-evd-surface${ratio ? ' is-sized' : ''}`}
          style={{ '--zoom': zoom, ...(ratio ? { width: `min(100cqw, calc(100cqh * ${ratio}))`, aspectRatio: String(ratio) } : {}),
            transformOrigin: `${middle[0] * 100}% ${middle[1] * 100}%`,
            transform: zoom > 1 ? `translate(${(.5 - middle[0]) * 100}%, ${(.5 - middle[1]) * 100}%) scale(${zoom})` : undefined }}>
          <img src={src} alt={`${title}${box ? ', 읽은 위치를 상자로 표시' : ''}`} draggable={false}
            onLoad={event => setNatural({ src, width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
            onError={() => setFailedSrc(src)} />
          {box && <span className={`rv-evd-box is-${shown.verdict.code}`} data-testid="evidence-read-box"
            style={{ left: box[0] * 100 + '%', top: box[1] * 100 + '%', width: (box[2] - box[0]) * 100 + '%', height: (box[3] - box[1]) * 100 + '%' }} />}
        </div>}
    </div>
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
    <p className="rv-evd-poster-note">상자는 검사 모델이 읽은 위치입니다. 실제 포스터와 맞는지 확인해 주세요.</p>
  </section>;
}

function EvidenceBody({ graph, image }) {
  const [mode, setMode] = useState('chain');
  const [selectedId, setSelectedId] = useState('verdict');
  const [shownId, setShownId] = useState(null);
  const nodes = Object.fromEntries(graph.nodes.map(node => [node.id, node]));
  const selected = nodes[selectedId] || nodes.verdict;
  const names = id => `${nodes[id].kind} ${nodes[id].title}`;
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

      <div className="rv-evd-graph-head">
        <h3>근거 관계도</h3><span>노드 {graph.nodes.length} · 관계 {graph.edges.length}</span>
        <div className="rv-evd-modes" role="group" aria-label="관계도 보기 방식">
          <button type="button" aria-pressed={mode === 'chain'} onClick={() => setMode('chain')}>근거 사슬 강조</button>
          <button type="button" aria-pressed={mode === 'all'} onClick={() => setMode('all')}>전체 관계</button>
        </div>
      </div>
      <div className="rv-evg-scroll">
        <div className={`rv-evg is-${mode}`} role="group" aria-label={`${graph.label} 근거 관계도`} style={{ height: HEIGHT }}>
          {graph.edges.map(edge => <Edge key={edge.id} edge={edge} nodes={nodes} />)}
          {graph.nodes.map(node => <button type="button" key={node.id} aria-pressed={selected.id === node.id}
            className={`rv-evg-node${node.chain ? ' is-chain' : ''}${node.dashed ? ' is-dashed' : ''}${node.tone ? ` is-${node.tone}` : ''}`}
            style={{ left: left(node.slot[0]) + '%', top: top(node.slot[1]), width: COL + '%', height: NODE_H }} onClick={() => select(node)}>
            <span className="rv-evg-kind">{node.kind}</span>
            <strong className="rv-evg-title">{node.tone && <i aria-hidden="true" />}{node.title}</strong>
            <span className="rv-evg-sub">{node.sub}</span>
          </button>)}
        </div>
      </div>
      <ul className="sr-only" aria-label="관계 목록">{graph.edges.map(edge => <li key={edge.id}>{names(edge.from)} → {edge.label} → {names(edge.to)}</li>)}</ul>

      <div className="rv-evd-panels">
        <section className="rv-evd-detail" aria-label="선택한 노드" aria-live="polite">
          <h4>선택한 노드 <span>{selected.kind}</span></h4>
          <p className="rv-evd-detail-title">{selected.title}</p>
          <dl>{selected.facts.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>
          <p className="rv-evd-store">{selected.graph ? <>그래프 노드 <code title={selected.graph.key}>{selected.graph.kind}</code></> : selected.record}
            {selected.runId && <a href={'?framework=' + encodeURIComponent(selected.runId)}>이 검수 결과 열기</a>}</p>
        </section>
        <section className="rv-evd-path" aria-label="근거 경로">
          <h4>근거 경로</h4>
          <ol>{graph.path.map(step => <li key={step.id}><button type="button" aria-pressed={selected.id === step.id}
            onClick={() => select(nodes[step.id])}>{step.text}</button></li>)}</ol>
        </section>
      </div>
      <p className="rv-evd-foot">{source}</p>
    </section>
  </div>;
}

export default function PosterEvidenceDialog({ run, criterionId, number, position, image, history, runs, retrieval, onOpen, onClose, onPrev, onNext }) {
  const dialog = useRef(null);
  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
    onOpen?.();
  }, []);
  const graph = useMemo(() => evidenceGraph(run, criterionId, { image, history, runs, retrieval }),
    [run, criterionId, image, history, runs, retrieval]);
  const close = () => dialog.current?.close();
  return <dialog ref={dialog} className="rv-evd" aria-labelledby="rv-evd-title" onClose={onClose}
    onClick={event => { if (event.target === event.currentTarget) close(); }}>
    <header className="rv-evd-head">
      <h2 id="rv-evd-title">{number && <><span>{number}</span>{' '}</>}{graph ? graph.label : '검수 항목'}{' '}<small>판정 근거</small></h2>
      {graph && <><VerdictChip verdict={graph.verdict} /><span className="rv-evd-required">{graph.required ? '필수 조건' : '참고 조건'}</span></>}
      <div className="rv-evd-nav">
        {position && <span>{position.index} / {position.total}</span>}
        <button type="button" aria-label="이전 항목" disabled={!onPrev} onClick={onPrev}>‹</button>
        <button type="button" aria-label="다음 항목" disabled={!onNext} onClick={onNext}>›</button>
      </div>
      <button type="button" className="rv-evd-close" aria-label="판정 근거 닫기" onClick={close}>×</button>
    </header>
    {graph ? <EvidenceBody key={criterionId} graph={graph} image={image} />
      : <p className="rv-evd-missing">이 항목의 판정 기록을 찾지 못했습니다.</p>}
  </dialog>;
}
