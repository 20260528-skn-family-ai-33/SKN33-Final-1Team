import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Icon } from './Icon.jsx';
import { evidenceBoxStyle } from '../workflow.js';
import { imageStageSize, canShowImageEvidence, imageEvidenceFocus, imageStageScroll } from './imageStageGeometry.js';
import '../styles/image-stage.css';

// Evidence and image share one sized surface at every zoom level.
export default function ImageStage({ src, alt = '검수 이미지', title = '검수 이미지', subtitle = '', width, height,
  overlays = [], selectedOverlayId, onOverlaySelect, empty, children }) {
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
  const ready = Boolean(src && natural?.src === src);
  const size = imageStageSize(ready ? natural.width : width, ready ? natural.height : height, bounds.width, bounds.height, zoom);
  const safeEvidence = ready && canShowImageEvidence(natural.width, natural.height, width, height);
  useEffect(() => { setView({ zoom: 'fit', target: null }); setFailed(false); drag.current = null; setPanning(false); }, [src]);
  useEffect(() => {
    const element = viewport.current;
    const measure = () => setBounds({ width: element.clientWidth, height: element.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
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
  const focusView = safeEvidence ? imageEvidenceFocus(overlays.filter(item => item.id === selectedOverlayId).map(item => item.bbox_xyxy), natural.width, natural.height, bounds.width, bounds.height) : null;
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
  return <section className="image-stage" aria-label="이미지 작업 영역">
    <div className="image-stage-heading"><div className="image-stage-file"><span className="image-stage-icon"><Icon name="image" /></span><div><strong title={title}>{title}</strong><span>{subtitle || (ready ? `${natural.width.toLocaleString()} × ${natural.height.toLocaleString()} px` : '원본 이미지')}</span></div></div><span className="image-stage-original">ORIGINAL</span></div>
    <div className={`image-stage-viewport ${panning ? 'is-panning' : ''}`} ref={viewport} tabIndex={src ? 0 : -1} aria-label="이미지 확대 영역. 확대 후 방향키로 이동할 수 있습니다." onKeyDown={keyNavigation} onPointerDown={startPan} onPointerMove={movePan} onPointerUp={endPan} onPointerCancel={endPan} onLostPointerCapture={endPan}>
      {!src ? <div className="image-stage-empty">{empty || <><Icon name="image" /><h3>검수할 이미지를 준비하세요.</h3><p>선택한 이미지가 이곳에 크게 표시됩니다.</p></>}</div>
        : failed ? <div className="image-stage-empty" role="alert"><Icon name="warning" /><h3>이미지를 표시하지 못했습니다.</h3><p>연결 상태를 확인하거나 이미지를 다시 선택해 주세요.</p></div>
        : <div className="image-stage-pan-area" style={size ? { width: Math.max(bounds.width, size.width + 64), minHeight: Math.max(bounds.height, size.height + 64) } : undefined}>
          <div className="image-stage-surface" style={size ? { width: size.width, height: size.height } : undefined}>
            <img src={src} alt={alt} draggable={false} onLoad={event => setNatural({ src, width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })} onError={() => setFailed(true)} />
            {safeEvidence && showEvidence && overlays.map((overlay, index) => {
              const style = evidenceBoxStyle(overlay.bbox_xyxy, natural.width, natural.height);
              return style ? <button type="button" key={`${overlay.id}-${index}`} data-testid="evidence-box" className={`image-stage-box ${selectedOverlayId === overlay.id ? 'is-selected' : ''}`} style={style} aria-label={`근거 영역: ${overlay.text || overlay.label || index + 1}`} aria-pressed={selectedOverlayId === overlay.id} onClick={() => onOverlaySelect?.(overlay.id)}><span>{overlay.label || String(index + 1).padStart(2, '0')}</span></button> : null;
            })}
          </div>
        </div>}
    </div>
    <div className="image-stage-toolbar"><div className="image-stage-zoom"><button type="button" aria-label="이미지 축소" disabled={!src || failed} onClick={() => changeZoom(-1)}>−</button><select aria-label="이미지 확대 비율" value={zoom} disabled={!src || failed} onChange={event => setZoom(event.target.value === 'fit' ? 'fit' : Number(event.target.value))}><option value="fit">화면에 맞춤</option>{zoom !== 'fit' && !zoomStops.includes(zoom) && <option value={zoom}>{Math.round(zoom * 100)}% · 근거</option>}{zoomStops.map(value => <option key={value} value={value}>{value * 100}%</option>)}</select><button type="button" aria-label="이미지 확대" disabled={!src || failed} onClick={() => changeZoom(1)}>+</button></div><div className="image-stage-tools">{overlays.length > 0 && <button type="button" className="image-stage-focus" disabled={!focusView || failed} onClick={focusEvidence}><Icon name="search" />선택 근거 확대</button>}<button type="button" className="image-stage-evidence-toggle" aria-pressed={showEvidence} disabled={!safeEvidence || !overlays.length} onClick={() => setShowEvidence(value => !value)}><Icon name={showEvidence ? 'eye' : 'eyeOff'} />근거 표시</button></div></div>
    {ready && !safeEvidence && overlays.length > 0 && <p className="image-stage-warning" role="status">원본 이미지와 근거의 크기가 달라 위치 표시를 생략했습니다.</p>}
    {children && <div className="image-stage-extra">{children}</div>}
  </section>;
}
