import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { Icon } from './Icon.jsx';
import { evidenceBoxStyle } from '../pages/review/workflow.js';
import { imageStageSize, imageStageBounds, evidenceLabelOnRight, evidenceLabelBelow, canShowImageEvidence, imageEvidenceFocus, imageStageScroll } from './imageStageGeometry.js';
import { useBoxFit } from './useBoxFit.js';
import '../styles/image-stage.css';

// Evidence and image share one sized surface at every zoom level.
// viewKey(선택): 같은 이미지에서 호출한 화면이 다른 항목을 고르면 확대를 '화면에 맞춤'으로 되돌린다.
// heading=false: 위쪽 제목 상자(아이콘·제목·부제)를 그리지 않는다. 이때 headingActions는 아래 도구 줄 오른쪽에 두고,
// toolbarNote(선택)는 확대 버튼 옆에 짧은 정보로 보여 준다.
// bare=true: 이미지를 감싸는 틀(테두리·회색 배경)을 없애고 미리보기만 보여 준다(image-stage--bare, 화면별 CSS가 처리).
// fitEvidence(선택): 근거 상자를 그림에 맞춰 다듬어 그린다. 'model'(검사 모델이 알려 준 상자) | 'ocr'(글자 인식이 읽은 상자).
//   화면에 그리는 자리만 달라지고 overlays의 좌표는 그대로다. 다듬은 상자가 있으면 도구 줄에 '상자 맞춤' 단추가 생겨 원래 상자와 견줄 수 있다.
//   onEvidenceFit(선택): 다듬은 상자를 그리고 있는지(true/false)를 알려 준다 — 화면의 안내 문구를 맞추는 데 쓴다.
// emphasis=true: 근거 상자를 어떤 바탕에서도 보이게 그리고(image-stage--emphasis), 고른 상자 밖을 어둡게 한다.
export default function ImageStage({ src, alt = '검수 이미지', title = '검수 이미지', subtitle = '', width, height,
  overlays = [], selectedOverlayId, onOverlaySelect, evidenceScope, empty, viewKey, headingActions, children, showControls = true,
  heading = true, toolbarNote, bare = false, fitEvidence, onEvidenceFit, emphasis = false }) {
  const viewport = useRef(null);
  const drag = useRef(null);
  const [bounds, setBounds] = useState({ width: 600, height: 620 });
  const [natural, setNatural] = useState(null);
  const [view, setView] = useState({ zoom: 'fit', target: null });
  const zoom = view.zoom;
  const zoomStops = [.25, .5, .75, 1, 1.25, 1.5, 2, 3];
  const [showEvidence, setShowEvidence] = useState(true);
  const [failed, setFailed] = useState(false);
  const [panning, setPanning] = useState(false);
  const [fitOn, setFitOn] = useState(true);
  const boxFit = useBoxFit(fitEvidence, src);
  const dimId = `image-stage-dim-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const ready = Boolean(src && natural?.src === src);
  const size = imageStageSize(ready ? natural.width : width, ready ? natural.height : height, bounds.width, bounds.height, zoom);
  const safeEvidence = ready && canShowImageEvidence(natural.width, natural.height, width, height);
  useEffect(() => { setView({ zoom: 'fit', target: null }); drag.current = null; setPanning(false); }, [src, viewKey]);
  useEffect(() => { setFailed(false); }, [src]);
  useEffect(() => {
    const element = viewport.current;
    // contentRect is fractional and excludes scrollbars; the first callback arrives before paint.
    const observer = new ResizeObserver(([entry]) => setBounds(imageStageBounds(entry.contentRect.width, entry.contentRect.height)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => {
    const element = viewport.current;
    if (element && size) {
      const scroll = imageStageScroll(size, bounds.width, bounds.height, view.target);
      element.scrollLeft = scroll.left;
      element.scrollTop = scroll.top;
    }
  }, [view, src, size?.width, size?.height, bounds.width, bounds.height]);
  function setZoom(value) {
    const element = viewport.current;
    const target = size && element ? {
      x: (element.scrollLeft + bounds.width / 2 - (Math.max(bounds.width, size.width + 64) - size.width) / 2) / size.scale,
      y: (element.scrollTop + bounds.height / 2 - (Math.max(bounds.height, size.height + 64) - size.height) / 2) / size.scale,
    } : null;
    setView({ zoom: value, target: value === 'fit' ? null : target });
  }
  function changeZoom(direction) {
    const current = zoom === 'fit' ? size?.scale || 1 : Number(zoom);
    setZoom(direction > 0 ? zoomStops.find(value => value > current + .01) || 3 : [...zoomStops].reverse().find(value => value < current - .01) || .25);
  }
  // 그릴 상자: 다듬은 자리가 있고 '상자 맞춤'이 켜져 있으면 그 자리, 아니면 받은 자리
  const drawn = safeEvidence ? overlays.map(overlay => {
    const fitted = fitEvidence ? boxFit.fit(overlay.bbox_xyxy) : null;
    return { overlay, fitted: Boolean(fitted?.changed), box: fitted?.changed && fitOn ? fitted.box : overlay.bbox_xyxy };
  }) : [];
  const anyFitted = drawn.some(item => item.fitted);
  const drawnBoxes = drawn.map(item => item.box);
  const showingFit = anyFitted && fitOn && showEvidence;
  useEffect(() => { onEvidenceFit?.(showingFit); }, [showingFit]);
  // 고른 상자와 똑같은 자리에 있는 다른 상자(영상에서 고른 근거의 원래 글자 줄)는 선과 이름표를 겹쳐 그리지 않는다
  const covered = (overlay, box) => Array.isArray(box) && drawn.some(item => item.overlay.id === selectedOverlayId && item.overlay !== overlay
    && Array.isArray(item.box) && item.box.every((value, at) => Math.abs(value - box[at]) < .5));
  const selectedBoxes = drawn.filter(item => item.overlay.id === selectedOverlayId).map(item => item.box);
  const focusView = safeEvidence ? imageEvidenceFocus(selectedBoxes, natural.width, natural.height, bounds.width, bounds.height) : null;
  // 고른 상자 밖을 어둡게 할 구멍(그림 안으로 자른 상자)
  const holes = emphasis && safeEvidence && showEvidence ? selectedBoxes.filter(box => Array.isArray(box) && box.length === 4 && box.every(Number.isFinite)).map(box => {
    const x1 = Math.min(natural.width, Math.max(0, box[0])), y1 = Math.min(natural.height, Math.max(0, box[1]));
    return [x1, y1, Math.min(natural.width, Math.max(x1, box[2])), Math.min(natural.height, Math.max(y1, box[3]))];
  }).filter(box => box[2] > box[0] && box[3] > box[1]) : [];
  function focusEvidence() {
    if (!focusView) return;
    setShowEvidence(true);
    setView({ ...focusView });
  }
  function keyNavigation(event) {
    if (event.target !== viewport.current) return;
    if (event.key === '0') { event.preventDefault(); setZoom('fit'); }
    if (event.key === '+' || event.key === '=') { event.preventDefault(); changeZoom(1); }
    if (event.key === '-') { event.preventDefault(); changeZoom(-1); }
  }
  function startPan(event) {
    if (event.button !== 0 || event.target.closest('button') || event.pointerType === 'touch') return;
    const element = viewport.current;
    if (element.scrollWidth <= element.clientWidth && element.scrollHeight <= element.clientHeight) return;
    drag.current = { x: event.clientX, y: event.clientY, left: element.scrollLeft, top: element.scrollTop };
    element.setPointerCapture(event.pointerId); setPanning(true); event.preventDefault();
  }
  function movePan(event) {
    if (!drag.current) return;
    viewport.current.scrollLeft = drag.current.left - event.clientX + drag.current.x;
    viewport.current.scrollTop = drag.current.top - event.clientY + drag.current.y;
  }
  function endPan() { drag.current = null; setPanning(false); }
  return <section className={`image-stage${bare ? ' image-stage--bare' : ''}${emphasis ? ' image-stage--emphasis' : ''}`} aria-label="이미지 작업 영역">
    {heading && <div className="image-stage-heading">
      <div className="image-stage-file">
        <span className="image-stage-icon"><Icon name="image" /></span>
        <div>
          <strong title={title}>{title}</strong>
          <span>{subtitle || (ready ? `${natural.width.toLocaleString()} × ${natural.height.toLocaleString()} px` : '원본 이미지')}</span>
        </div>
      </div>
      {headingActions || <span className="image-stage-original">원본</span>}
    </div>}
    <div className={`image-stage-viewport ${panning ? 'is-panning' : ''}`} ref={viewport} tabIndex={src ? 0 : -1} aria-label="이미지 확대 영역. 확대 후 방향키로 이동할 수 있습니다." onKeyDown={keyNavigation} onPointerDown={startPan} onPointerMove={movePan} onPointerUp={endPan} onPointerCancel={endPan} onLostPointerCapture={endPan}>
      {!src ? <div className="image-stage-empty">{empty || <><Icon name="image" /><h3>검수할 이미지를 준비하세요.</h3><p>선택한 이미지가 이곳에 크게 표시됩니다.</p></>}</div>
        : failed ? <div className="image-stage-empty" role="alert"><Icon name="warning" /><h3>이미지를 표시하지 못했습니다.</h3><p>연결 상태를 확인하거나 이미지를 다시 선택해 주세요.</p></div>
        : <div className="image-stage-pan-area" style={size ? { width: Math.max(bounds.width, size.width + 64), minHeight: Math.max(bounds.height, size.height + 64) } : undefined}>
          <div className="image-stage-surface" style={size ? { width: size.width, height: size.height } : undefined}>
            <img src={src} alt={alt} draggable={false} onLoad={event => { const element = event.currentTarget; setNatural({ src, width: element.naturalWidth, height: element.naturalHeight }); boxFit.read(element, src); }} onError={() => setFailed(true)} />
            {holes.length > 0 && <svg className="image-stage-dim" viewBox={`0 0 ${natural.width} ${natural.height}`} preserveAspectRatio="none" aria-hidden="true">
              <mask id={dimId}><rect width={natural.width} height={natural.height} fill="#fff" />{holes.map((box, index) => <rect key={index} x={box[0]} y={box[1]} width={box[2] - box[0]} height={box[3] - box[1]} fill="#000" />)}</mask>
              <rect width={natural.width} height={natural.height} mask={`url(#${dimId})`} />
            </svg>}
            {showEvidence && drawn.map(({ overlay, box, fitted }, index) => {
              const style = evidenceBoxStyle(box, natural.width, natural.height);
              const name = overlay.label || String(index + 1).padStart(2, '0');
              const below = emphasis && evidenceLabelBelow(index, drawnBoxes, size?.scale, name);
              return style ? <button type="button" key={`${overlay.id}-${index}`} data-testid="evidence-box" data-fit={fitted && fitOn ? 'fitted' : undefined} className={`image-stage-box ${overlay.tone ? `is-${overlay.tone}` : 'is-plain'} ${selectedOverlayId === overlay.id ? 'is-selected' : ''} ${evidenceLabelOnRight(box, natural.width) ? 'is-right' : ''} ${below ? 'is-below' : ''} ${emphasis && selectedOverlayId !== overlay.id && covered(overlay, box) ? 'is-covered' : ''}`} style={style} aria-label={overlay.ariaLabel || `근거 영역: ${overlay.text || overlay.label || index + 1}`} aria-pressed={selectedOverlayId === overlay.id} onClick={() => onOverlaySelect?.(overlay.id)}><span>{name}</span></button> : null;
            })}
          </div>
        </div>}
    </div>
    {showControls && <div className="image-stage-toolbar"><div className="image-stage-zoom"><button type="button" aria-label="이미지 축소" disabled={!src || failed} onClick={() => changeZoom(-1)}>−</button><select aria-label="이미지 확대 비율" value={zoom} disabled={!src || failed} onChange={event => setZoom(event.target.value === 'fit' ? 'fit' : Number(event.target.value))}><option value="fit">화면에 맞춤</option>{zoom !== 'fit' && !zoomStops.includes(zoom) && <option value={zoom}>{Math.round(zoom * 100)}% · 근거</option>}{zoomStops.map(value => <option key={value} value={value}>{value * 100}%</option>)}</select><button type="button" aria-label="이미지 확대" disabled={!src || failed} onClick={() => changeZoom(1)}>+</button></div>
      {toolbarNote && <span className="image-stage-note" title={toolbarNote}>{toolbarNote}</span>}
      <div className="image-stage-tools">
        {overlays.length > 0 && <button type="button" className="image-stage-focus" title="선택 근거 확대" disabled={!focusView || failed} onClick={focusEvidence}><Icon name="search" />선택 근거 확대</button>}
        {anyFitted && showEvidence && <button type="button" className="image-stage-fit-toggle" aria-pressed={fitOn}
          title={`${fitEvidence === 'ocr' ? '읽은 상자를 화면의 글자에' : '검사 모델이 알려 준 상자를 둘레의 글자에'} 맞춰 보여 줍니다. 끄면 원래 상자를 봅니다.`}
          onClick={() => setFitOn(value => !value)}>상자 맞춤</button>}
        <button type="button" className="image-stage-evidence-toggle" title={evidenceScope ? '근거 표시 모드 전환' : '근거 표시'}
          aria-pressed={evidenceScope ? undefined : showEvidence} disabled={!safeEvidence || !(evidenceScope?.available ?? overlays.length)}
          onClick={evidenceScope?.toggle ?? (() => setShowEvidence(value => !value))}>
          <Icon name={showEvidence ? 'eye' : 'eyeOff'} />{evidenceScope?.label ?? '근거 표시'}
        </button>
        {!heading && headingActions}
      </div>
    </div>}
    {ready && !safeEvidence && overlays.length > 0 && <p className="image-stage-warning" role="status">원본 이미지와 근거의 크기가 달라 위치 표시를 생략했습니다.</p>}
    {children && <div className="image-stage-extra">{children}</div>}
  </section>;
}
