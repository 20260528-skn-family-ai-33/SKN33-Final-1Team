import React, { useEffect, useState } from 'react';
import { modeltestRequest } from './modeltest.js';
import { boxStyle, decisionLabel, frameLabel, previewURL } from './modeltestResults.js';

function Preview({ context, jobId, name, cache, alt, children }) {
  const [src, setSrc] = useState(''); const [error, setError] = useState(''); const [retry, setRetry] = useState(0);
  useEffect(() => {
    setSrc(''); setError('');
    if (!name || !context) return;
    if (!/^[\w.-]+$/.test(name)) { setError('미리보기 이름을 확인하지 못했습니다.'); return; }
    if (cache.has(name)) { setSrc(cache.get(name)); return; }
    const controller = new AbortController();
    modeltestRequest(context, `/runs/${jobId}/previews/${name}`, { signal: controller.signal, timeout: 90000 })
      .then(value => {
        if (controller.signal.aborted) return;
        const url = previewURL(value);
        if (cache.size >= 32) cache.delete(cache.keys().next().value);
        cache.set(name, url); setSrc(url);
      }).catch(cause => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [context, jobId, name, cache, retry]);
  return <div className="mt-evidence-image">
    {src ? <div className="mt-image-coordinate-plane"><img src={src} alt={alt} onError={() => { setSrc(''); setError('이미지를 표시하지 못했습니다.'); }} />{children}</div>
      : <div className="mt-image-placeholder" role="status">{!name ? '이 결과에는 미리보기 정보가 없습니다. 변경 적용 후 새로 실행해 주세요.' : error || 'Pod에서 미리보기를 불러오는 중…'}
        {error && <button type="button" className="btn secondary small" onClick={() => setRetry(value => value + 1)}>다시 불러오기</button>}</div>}
  </div>;
}

function Scores({ rows }) {
  return <div className="mt-label-scores">{rows.map((item, index) => <div key={index}>
    <span>{item.label}</span><meter min={0} max={1} value={Number.isFinite(item.score) ? item.score : 0} aria-label={`${item.label} 유사도`} />
    <strong>{Number.isFinite(item.score) ? item.score.toFixed(3) : '미제공'}</strong>
  </div>)}</div>;
}

function sourceStage(job, stage, kind, visited = new Set()) {
  if (!stage || visited.has(stage.id)) return null;
  visited.add(stage.id);
  if ((stage.kind || stage.id) === kind) return stage;
  if (!stage.kind) return job.stages.find(item => item.id === kind);
  if (stage.inputs?.[kind]) return job.stages.find(item => item.id === stage.inputs[kind]);
  for (const id of Object.values(stage.inputs || {})) {
    const found = sourceStage(job, job.stages.find(item => item.id === id), kind, visited);
    if (found) return found;
  }
  return null;
}

function LogoEvidence({ job, stage, manifest, frame, context, cache, selectedLogo, setSelectedLogo, referenceIndex, setReferenceIndex }) {
  const detector = sourceStage(job, stage, 'logo_extract');
  const extracted = detector?.output?.items?.find(item => item.index === frame.index);
  const logos = extracted?.logos || [];
  const logo = logos[selectedLogo];
  const kind = stage.kind || stage.id;
  const comparison = ['logo_compare', 'logo_similarity', 'candidate_filter'].includes(kind) ? stage : sourceStage(job, stage, 'logo_compare');
  const compared = comparison?.output?.items?.find(item => item.index === frame.index);
  const matches = (compared?.matches || []).filter(match => match.logo_index === (logo?.logo_index ?? selectedLogo) || (match.logo_index === undefined && logos.length === 1));
  const reference = job.references?.find(item => item.index === referenceIndex);
  const comparedMatch = matches.find(item => item.reference_index === referenceIndex);
  const similarities = job.stages.filter(item => (item.kind || item.id) === 'logo_similarity' && sourceStage(job, item, 'logo_extract')?.id === detector?.id);
  const similarity = similarities.length === 1 ? similarities[0].output?.items?.find(item => item.index === frame.index)?.matches?.find(item => item.logo_index === (logo?.logo_index ?? selectedLogo) && item.reference_index === referenceIndex) : null;
  const match = comparedMatch ? { ...similarity, ...comparedMatch } : null;
  if (!logos.length) return <p className="mt-empty">{extracted ? '이 프레임에서 탐지한 로고가 없습니다.' : job.stages.some(stage => stage.id === 'logo_extract') ? '로고 추출 결과를 기다리고 있습니다.' : '로고 추출 단계를 선택하지 않았습니다.'}</p>;
  return <section className="mt-logo-evidence" aria-label="로고 대조 결과">
    <div className="mt-logo-picker">{logos.map((item, index) => <button type="button" key={index} aria-pressed={selectedLogo === index} onClick={() => setSelectedLogo(index)}>로고 {index + 1}</button>)}</div>
    <p className="mt-hint">위치 (x1, y1) → (x2, y2): {logo?.bbox?.join(', ')} px · 분석 프레임 {frame.width || extracted?.width} × {frame.height || extracted?.height} px
      {Number.isFinite(logo?.score) && ` · 탐지 점수 ${logo.score.toFixed(3)}`}</p>
    <div className="mt-logo-pair">
      <figure><figcaption>추출 로고 {selectedLogo + 1}</figcaption><Preview context={context} jobId={job.id} name={logo?.preview} cache={cache} alt={`추출 로고 ${selectedLogo + 1}`} /></figure>
      <span className="mt-pair-arrow" aria-hidden="true">↔</span>
      <figure><figcaption><label htmlFor="mt-result-reference">원본 로고</label>
        <select id="mt-result-reference" value={referenceIndex} onChange={event => setReferenceIndex(Number(event.target.value))} disabled={!manifest.files.references.length}>
          {!manifest.files.references.length && <option value={0}>첨부하지 않음</option>}
          {manifest.files.references.map((file, index) => <option key={index} value={index}>{file.name}</option>)}
        </select></figcaption>
        {manifest.files.references.length ? <Preview context={context} jobId={job.id} name={reference?.preview} cache={cache} alt={`원본 로고 ${manifest.files.references[referenceIndex]?.name || ''}`} /> : <p className="mt-empty">원본 로고를 첨부하지 않았습니다.</p>}
      </figure>
    </div>
    {match ? <div className="mt-match-result">
      <h4>{kind === 'candidate_filter' ? '후보 필터 결과' : kind === 'logo_similarity' ? '로고 이미지 유사도' : Number.isFinite(match.matches) ? 'XFeat 대조 결과' : '로고 대조 결과'}</h4>
      <strong className={`mt-decision mt-decision-${['match', 'candidate_match', 'pass'].includes(match.decision) ? 'candidate' : 'uncertain'}`}>{kind === 'candidate_filter' ? '필터 통과 후보' : kind === 'logo_similarity' ? '유사도 측정' : decisionLabel(match.decision)}</strong>
      <dl className="mt-match-metrics">
        <div><dt>이미지 코사인 유사도 · SigLIP2</dt><dd>{Number.isFinite(match.image_cosine_similarity) ? match.image_cosine_similarity.toFixed(3) : '미계산'}</dd></div>
        {Number.isFinite(match.descriptor_similarity) && <div><dt>특징점 유사도 (코사인)</dt><dd>{match.descriptor_similarity.toFixed(3)}</dd></div>}
        {Number.isFinite(match.inlier_ratio) && <div><dt>기하 검증 통과율</dt><dd>{(match.inlier_ratio * 100).toFixed(1)}%</dd></div>}
        {Number.isFinite(match.matches) && <div><dt>매칭 / 검증 통과</dt><dd>{match.matches} / {match.inliers ?? 0}개</dd></div>}
        {Array.isArray(match.keypoints) && <div><dt>특징점 · 크롭 / 원본</dt><dd>{match.keypoints.join(' / ')}개</dd></div>}
      </dl>
      {!Number.isFinite(match.image_cosine_similarity) && <p className="mt-hint">이미지 코사인 값도 보려면 XFeat 로고 대조에서 SigLIP2 추가 계산을 선택하세요.</p>}
      {typeof match.text === 'string' && <p className="mt-readable-output">{match.text}</p>}
      {typeof match.note === 'string' && <p className="mt-hint">{match.note}</p>}
      {Number.isFinite(match.descriptor_similarity) && <p className="mt-hint">특징점 유사도는 매칭된 점들의 평균입니다. 이미지 코사인 및 특징점 지표는 일치 확률이 아닙니다.</p>}
    </div> : <p className="mt-hint">{compared ? Number.isFinite(compared.selected_count) ? '이 후보는 필터에서 제외되어 대조하지 않았습니다.' : '선택한 로고와 레퍼런스의 대조 결과가 없습니다.' : '결과를 확인할 로고 대조 단계를 선택해 주세요.'}</p>}
    {Number.isFinite(compared?.selected_count) && <p className="mt-hint">이 프레임의 전체 후보 {compared.candidate_count}개 중 {compared.selected_count}개 선택</p>}
  </section>;
}

export default function ModeltestResults({ job, stage, manifest, context, onSeek, cache, selection, onSelect }) {
  const kind = stage?.kind || stage?.id;
  const selectedFrame = selection.frame ?? 0; const selectedLogo = selection.logo ?? 0;
  const setSelectedLogo = logo => onSelect({ logo });
  const rows = stage?.output?.frames || stage?.output?.items;
  if (!Array.isArray(rows) || !rows.length) return <p className="mt-empty">이 단계의 결과가 아직 도착하지 않았습니다.</p>;
  if (kind === 'text_generate') return <div>{rows.map((row, index) => <p key={index} className="mt-readable-output">{row.text || '모델 응답이 없습니다.'}</p>)}</div>;
  if (kind === 'conditions') return <div className="mt-document-results">{rows.map((row, index) => <article key={index}>
    <h3>{manifest.files.documents[row.document_index]?.name || `문서 ${(row.document_index ?? index) + 1}`} · {row.page_or_chunk} 페이지/구간</h3>
    <p className="mt-readable-output">{row.text || '추출한 조건이 없습니다.'}</p>
  </article>)}</div>;
  const frame = rows[selectedFrame] || rows[0];
  const frameInfo = job.stages.find(item => item.id === 'frames')?.output?.frames?.find(item => item.index === frame.index) || job.media || {};
  const sample = { ...frameInfo, ...frame };
  const logos = sourceStage(job, stage, 'logo_extract')?.output?.items?.find(item => item.index === frame.index)?.logos || [];
  return <div className="mt-sample-results">
    {manifest.flow === 'video' && <nav className="mt-frame-picker" aria-label="분석 프레임 선택"><p>{rows.length}개 프레임 · 첫 프레임은 0번</p>
      {rows.map((row, index) => <button type="button" key={index} aria-pressed={selectedFrame === index} onClick={() => onSelect({ frame: index, logo: 0, reference: 0 })}>{frameLabel(row)}</button>)}
    </nav>}
    <div className="mt-sample-detail">
      <div className="mt-sample-title"><h3>{manifest.flow === 'video' ? frameLabel(sample) : '검수 이미지'}</h3>
        {onSeek && Number.isFinite(sample.timestamp_seconds) && <button type="button" className="btn secondary small" onClick={() => onSeek(sample.timestamp_seconds)}>원본 영상에서 보기</button>}
      </div>
      <Preview context={context} jobId={job.id} name={sample.preview} cache={cache} alt={manifest.flow === 'video' ? `${frameLabel(sample)} 분석 이미지` : '검수 이미지'}>
        {logos.map((logo, index) => {
          const style = boxStyle(logo.bbox, sample.width, sample.height);
          return style && <button type="button" key={index} className={`mt-logo-box ${selectedLogo === index ? 'is-selected' : ''}`} style={style} aria-label={`로고 ${index + 1} 위치 보기`} aria-pressed={selectedLogo === index} onClick={() => setSelectedLogo(index)}><span>{index + 1}</span></button>;
        })}
      </Preview>
      {['logo_extract', 'logo_similarity', 'candidate_filter', 'logo_compare', 'frames'].includes(kind) && <LogoEvidence key={sample.index} job={job} stage={stage} manifest={manifest} frame={sample} context={context} cache={cache} selectedLogo={selectedLogo} setSelectedLogo={setSelectedLogo}
        referenceIndex={selection.reference ?? 0} setReferenceIndex={reference => onSelect({ reference })} />}
      {typeof frame.text === 'string' && <p className="mt-readable-output">{frame.text}</p>}
      {Array.isArray(frame.label_scores) && <Scores rows={frame.label_scores} />}
      {typeof frame.note === 'string' && <p className="mt-hint">{frame.note}</p>}
    </div>
  </div>;
}
