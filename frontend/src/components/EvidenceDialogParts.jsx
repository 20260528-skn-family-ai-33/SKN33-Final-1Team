import React, { useEffect, useRef, useState } from 'react';
import '../styles/evidence-dialog.css';

// 판정 근거 창(포스터·동영상 공통)의 틀, 근거 이미지 칸, 근거 관계도.
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

export function VerdictChip({ verdict }) {
  return <span className={`rv-evd-chip is-${verdict.code}`}><i aria-hidden="true" />{verdict.label}</span>;
}

// 상자가 근거 칸의 80%를 채우도록 키우는 배율. 1.25배가 안 되면 확대 단추를 내지 않는다.
export function boxZoom(box) {
  const scale = box ? Math.min(6, .8 / (box[2] - box[0]), .8 / (box[3] - box[1])) : 1;
  return { scale, canZoom: scale >= 1.25 };
}

// 근거 이미지와 읽은 위치 상자. 상자는 0~1 비율([왼쪽, 위, 오른쪽, 아래])이라 이미지 크기를 몰라도 그릴 수 있다.
// ratio(가로÷세로)를 알면 이미지를 불러오기 전에도 자리를 잡는다.
export function EvidenceFrame({ src, alt, box, tone, ratio: known, zoomed, empty }) {
  const [natural, setNatural] = useState(null);
  const [failedSrc, setFailedSrc] = useState(null);
  const ratio = natural?.src === src ? natural.width / natural.height : known > 0 ? known : null;
  const { scale, canZoom } = boxZoom(box);
  const zoom = zoomed && canZoom ? scale : 1;
  const middle = box ? [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2] : [.5, .5];
  return <div className="rv-evd-frame">
    {!src || failedSrc === src ? <p className="rv-evd-frame-empty">{empty}</p>
      : <div className={`rv-evd-surface${ratio ? ' is-sized' : ''}`}
        style={{ '--zoom': zoom, ...(ratio ? { width: `min(100cqw, calc(100cqh * ${ratio}))`, aspectRatio: String(ratio) } : {}),
          transformOrigin: `${middle[0] * 100}% ${middle[1] * 100}%`,
          transform: zoom > 1 ? `translate(${(.5 - middle[0]) * 100}%, ${(.5 - middle[1]) * 100}%) scale(${zoom})` : undefined }}>
        <img src={src} alt={alt} draggable={false}
          onLoad={event => setNatural({ src, width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
          onError={() => setFailedSrc(src)} />
        {box && <span className={`rv-evd-box is-${tone}`} data-testid="evidence-read-box"
          style={{ left: box[0] * 100 + '%', top: box[1] * 100 + '%', width: (box[2] - box[0]) * 100 + '%', height: (box[3] - box[1]) * 100 + '%' }} />}
      </div>}
  </div>;
}

// 근거 관계도 + 선택한 노드 + 근거 경로. graph는 { label, nodes, edges, path }이고 노드 자리(slot)는 [열, 행]이다.
// store(선택): 선택한 노드가 어디에 저장된 기록인지 한 줄로 그리는 함수.
export function EvidenceRelations({ graph, selectedId, onSelect, store, foot }) {
  const [mode, setMode] = useState('chain');
  const nodes = Object.fromEntries(graph.nodes.map(node => [node.id, node]));
  const selected = nodes[selectedId] || nodes.verdict || graph.nodes[0];
  const names = id => `${nodes[id].kind} ${nodes[id].title}`;
  const stored = store ? store(selected) : selected.record;
  return <>
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
          style={{ left: left(node.slot[0]) + '%', top: top(node.slot[1]), width: COL + '%', height: NODE_H }} onClick={() => onSelect(node)}>
          <span className="rv-evg-kind">{node.kind}</span>
          <strong className="rv-evg-title">{node.tone && <i aria-hidden="true" />}{node.title}</strong>
          <span className="rv-evg-sub">{node.sub}</span>
        </button>)}
      </div>
    </div>
    <ul className="sr-only" aria-label="관계 목록">{graph.edges.filter(edge => nodes[edge.from] && nodes[edge.to])
      .map(edge => <li key={edge.id}>{names(edge.from)} → {edge.label} → {names(edge.to)}</li>)}</ul>

    <div className="rv-evd-panels">
      <section className="rv-evd-detail" aria-label="선택한 노드" aria-live="polite">
        <h4>선택한 노드 <span>{selected.kind}</span></h4>
        <p className="rv-evd-detail-title">{selected.title}</p>
        <dl>{selected.facts.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>
        {stored && <p className="rv-evd-store">{stored}</p>}
      </section>
      <section className="rv-evd-path" aria-label="근거 경로">
        <h4>근거 경로</h4>
        <ol>{graph.path.map(step => <li key={step.id}><button type="button" aria-pressed={selected.id === step.id}
          onClick={() => onSelect(nodes[step.id])}>{step.text}</button></li>)}</ol>
      </section>
    </div>
    {foot && <p className="rv-evd-foot">{foot}</p>}
  </>;
}

// 창 틀: 머리글(번호·항목 이름·판정·앞뒤 항목·닫기)과 본문. 바깥(어두운 곳)을 누르거나 Esc로 닫는다.
export function EvidenceDialogShell({ number, title, verdict, tag, position, onOpen, onClose, onPrev, onNext, children }) {
  const dialog = useRef(null);
  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
    onOpen?.();
  }, []);
  const close = () => dialog.current?.close();
  return <dialog ref={dialog} className="rv-evd" aria-labelledby="rv-evd-title" onClose={onClose}
    onClick={event => { if (event.target === event.currentTarget) close(); }}>
    <header className="rv-evd-head">
      <h2 id="rv-evd-title">{number && <><span>{number}</span>{' '}</>}{title}{' '}<small>판정 근거</small></h2>
      {verdict && <VerdictChip verdict={verdict} />}
      {tag && <span className="rv-evd-required">{tag}</span>}
      <div className="rv-evd-nav">
        {position && <span>{position.index} / {position.total}</span>}
        <button type="button" aria-label="이전 항목" disabled={!onPrev} onClick={onPrev}>‹</button>
        <button type="button" aria-label="다음 항목" disabled={!onNext} onClick={onNext}>›</button>
      </div>
      <button type="button" className="rv-evd-close" aria-label="판정 근거 닫기" onClick={close}>×</button>
    </header>
    {children}
  </dialog>;
}
