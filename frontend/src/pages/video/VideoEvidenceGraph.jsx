import React, { useEffect, useState } from 'react';
import { fetchJson } from '../../api/client.js';
import { ErrorMessage } from '../../components/ErrorMessage.jsx';
import { statusLabels } from './videoFormat.js';

export default function VideoEvidenceGraph({ jobId, onFrame }) {
  const [open, setOpen] = useState(false);
  const [graph, setGraph] = useState(null);
  const [error, setError] = useState('');
  const [rule, setRule] = useState('');
  useEffect(() => {
    if (!open || graph) return undefined;
    const controller = new AbortController();
    setError('');
    fetchJson('/api/video-reviews/' + encodeURIComponent(jobId) + '/evidence-graph', { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setGraph(data); })
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [jobId, open, graph]);
  const nodes = new Map((graph?.nodes || []).map(node => [node.id, node]));
  const verdicts = graph?.verdicts || [];
  return <details className="rv-video-coverage" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>광고·협찬 근거 관계</summary>
    {open && <>
      <ErrorMessage message={error} />
      {!error && !graph && <p role="status">저장된 근거를 불러오고 있어요.</p>}
      {graph && <>
        <label>판정 규칙 <select value={rule} onChange={event => setRule(event.target.value)}>
          <option value="">모든 규칙</option>
          {verdicts.map(item => <option key={item.rule} value={item.rule}>{item.rule} {item.title}</option>)}
        </select></label>
        {verdicts.filter(item => !rule || item.rule === rule).map(item => <section key={item.id}>
          <h3>{item.title} · {statusLabels[item.status] || item.status}</h3>
          {item.note && <p>{item.note}</p>}
          <ol>{item.chain.map(id => nodes.get(id)).filter(Boolean).map(node => <li key={node.id}>
            {node.kind === 'frame' && onFrame ? <button type="button" className="text-btn"
              onClick={() => onFrame(node)}>{node.label}</button> : node.label}
          </li>)}</ol>
        </section>)}
        {graph.truncated && <p>관측 문구 일부만 표시합니다. 나머지 근거는 검수 결과에서 확인해 주세요.</p>}
      </>}
    </>}
  </details>;
}
