import React, { useEffect, useMemo, useState } from 'react';
import { EvidenceDialogShell, EvidenceFrame, EvidenceRelations, boxZoom } from '../../components/EvidenceDialogParts.jsx';
import { Icon } from '../../components/Icon.jsx';
import { videoEvidence } from './videoEvidence.js';
import { diffChars, formatTime, mismatchLabels } from './videoFormat.js';

// 동영상 검수 항목을 누르면 열리는 판정 근거 창. 포스터 판정 근거 창과 같은 틀을 쓴다.
// 왼쪽은 근거 프레임과 읽은 위치 상자(근거가 여러 곳이면 골라 본다), 오른쪽은 기준·판정 이유와 근거 관계도다.
const framePath = (jobId, frameId) => `/api/video-reviews/${encodeURIComponent(jobId)}/frames/${encodeURIComponent(frameId)}`;

// 기준 문구와 화면 문구를 나란히 두고 다른 글자만 강조한다.
function Mismatch({ item }) {
  const variants = (item.variants || []).map(value => (typeof value === 'string' ? value : value?.text)).filter(Boolean);
  return <li>
    <span>{mismatchLabels[item.kind] || '불일치'}</span>
    {item.expected ? <dl><div><dt>기준</dt><dd>{item.expected}</dd></div>
      <div><dt>화면</dt><dd>{diffChars(item.expected, item.observed).map(([ch, different], i) => different ? <mark key={i}>{ch}</mark> : <React.Fragment key={i}>{ch}</React.Fragment>)}</dd></div></dl>
      : <dl><div><dt>대표</dt><dd>{item.observed}</dd></div><div><dt>다르게 읽힘</dt><dd>{variants.join(' / ') || '–'}</dd></div></dl>}
  </li>;
}

function FramePane({ jobId, graph, onShow }) {
  const [zoomed, setZoomed] = useState(false);
  const shown = graph.shown;
  const view = shown || graph.still;
  const src = view ? framePath(jobId, view.frameId) : null;
  const box = shown?.box || null;
  const { canZoom } = boxZoom(box);
  useEffect(() => { setZoomed(false); }, [src, shown?.id]);
  const time = view ? formatTime(view.timeMs) : null;
  return <section className="rv-evd-poster" aria-label="근거 프레임">
    <header className="rv-evd-poster-head">
      <Icon name="film" /><strong>{shown ? '근거 프레임' : '영상 장면'}</strong>
      {time && <span>{time}</span>}
    </header>
    <EvidenceFrame src={src} alt={time ? `${time} 프레임${box ? ', 읽은 위치를 상자로 표시' : ''}` : '근거 프레임'} box={box} tone={graph.tone}
      ratio={view?.ratio} zoomed={zoomed} empty="표시할 프레임이 없습니다." />
    <div className="rv-evd-poster-caption">
      <p>{shown ? box ? <>읽은 위치 <b>{time} · 화면 {shown.hint}</b></> : <><b>{time}</b> 프레임 · 위치 상자가 없어 프레임 전체를 보여 줍니다.</>
        : view ? '이 항목은 위치 근거가 없어 영상의 한 장면만 보여 줍니다. 영상 전체에서 확인해 주세요.' : '이 항목에는 표시할 프레임이 없습니다.'}</p>
      {box && canZoom && <button type="button" className="rv-evd-zoom" aria-pressed={zoomed} onClick={() => setZoomed(value => !value)}>
        <Icon name="search" />{zoomed ? '전체 보기' : '상자 확대'}</button>}
    </div>
    {graph.evidence.length > 1 && <div className="rv-evd-trail" role="group" aria-label="근거 위치">
      <span>근거 위치 {graph.evidence.length}곳</span>
      <ol>{graph.evidence.map((entry, index) => <li key={entry.id}>
        <button type="button" className={`rv-evd-version is-${graph.tone}`} aria-pressed={entry.id === shown?.id} onClick={() => onShow(index)}>
          <span>{entry.range ? `${formatTime(entry.range[0])}~${formatTime(entry.range[1])}` : formatTime(entry.timeMs)}</span>
          <b><i aria-hidden="true" /><span>{entry.text || '위치만 기록'}</span></b>
        </button></li>)}</ol>
    </div>}
    <p className="rv-evd-poster-note">상자는 OCR이 글자를 읽은 위치입니다. 원본 영상과 맞는지 확인해 주세요.</p>
  </section>;
}

function EvidenceBody({ job, item, onFrame }) {
  const [rule, setRule] = useState(null);
  const [shown, setShown] = useState(0);
  const [selectedId, setSelectedId] = useState('verdict');
  const graph = useMemo(() => videoEvidence(job, item, { rule, shown }), [job, item, rule, shown]);
  // 근거 위치를 바꾸면 창 뒤 결과 화면의 프레임도 같은 곳으로 옮겨, 창을 닫았을 때 같은 장면이 보이게 한다.
  function show(index) { setShown(index); if (graph.evidence[index]) onFrame?.(graph.evidence[index].raw); }
  function chooseRule(key) { setRule(key); setShown(0); setSelectedId('verdict'); }
  return <div className="rv-evd-body">
    <FramePane jobId={job.id} graph={graph} onShow={show} />
    <section className="rv-evd-main" aria-label="판정 근거">
      <div className={`rv-evd-criterion is-${graph.verdict.code}`}>
        <p className="rv-evd-criterion-text">{graph.text}</p>
        {graph.compare.length > 0 && <dl className="rv-evd-compare">{graph.compare.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>}
        <p className="rv-evd-reason">{graph.reason || '제공된 판정 이유가 없습니다. 원본 영상과 기준을 직접 확인해 주세요.'}</p>
        {graph.notes.map(note => <p className="rv-evd-note" key={note}>{note}</p>)}
        {graph.quote && <details className="rv-evd-quote"><summary>기준 출처 보기</summary><p>{graph.quote}</p></details>}
      </div>
      {graph.mismatch.length > 0 && <section className="rv-evd-mismatch" aria-label="불일치">
        <h3>불일치 <span>{graph.mismatch.length}</span></h3>
        <ul>{graph.mismatch.map((entry, index) => <Mismatch key={index} item={entry} />)}</ul>
        <p>OCR이 잘못 읽었을 수도 있으니 원본을 확인해 주세요.</p>
      </section>}
      {graph.rules.length > 0 && <section className="rv-evd-rules" aria-label="규정별 확인">
        <h3>규정별 확인 <span>{graph.rules.length}</span></h3>
        <ul>{graph.rules.map(entry => <li key={entry.key}>
          <button type="button" className={`rv-evd-rule is-${entry.verdict.code}`} aria-pressed={entry.key === graph.rule} title={entry.note || undefined}
            onClick={() => chooseRule(entry.key)}><span>{entry.label}</span><b><i aria-hidden="true" />{entry.verdict.label}</b></button></li>)}</ul>
      </section>}
      <EvidenceRelations graph={graph} selectedId={selectedId} onSelect={node => setSelectedId(node.id)}
        foot="영상·읽은 문구·판정을 이 검수의 결과 기록에서 읽어 연결했습니다. 판정은 검사한 표본 시각을 기준으로 합니다." />
    </section>
  </div>;
}

export default function VideoEvidenceDialog({ job, item, number, position, onFrame, onClose, onPrev, onNext }) {
  const graph = useMemo(() => videoEvidence(job, item), [job, item]);
  return <EvidenceDialogShell number={number} title={graph ? graph.label : '검수 항목'} verdict={graph?.verdict} tag={graph?.tag}
    position={position} onClose={onClose} onPrev={onPrev} onNext={onNext}>
    {graph ? <EvidenceBody key={item.key} job={job} item={item} onFrame={onFrame} />
      : <p className="rv-evd-missing">이 항목의 판정 기록을 찾지 못했습니다.</p>}
  </EvidenceDialogShell>;
}
