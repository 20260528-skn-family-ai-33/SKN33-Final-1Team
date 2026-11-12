import React, { useCallback, useEffect, useRef, useState } from 'react';
import { frameworkJson } from './frameworkClient.js';
import { naverMapUrl, renderPosterMapQr } from './posterMapQr.js';
import '../../styles/poster-text-editor.css';

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const minimumSide = 0.015;
const defaultQrRegion = { x: 0.65, y: 0.65, width: 0.3, height: 0.3 };

function pointOnPoster(event) {
  const { left, top, width, height } = event.currentTarget.getBoundingClientRect();
  return { x: clamp((event.clientX - left) / width, 0, 1), y: clamp((event.clientY - top) / height, 0, 1) };
}

function boxBetween(start, end) {
  return {
    x: Math.min(start.x, end.x), y: Math.min(start.y, end.y),
    width: Math.abs(start.x - end.x), height: Math.abs(start.y - end.y),
  };
}

function boxStyle(box) {
  return {
    left: box.x * 100 + '%', top: box.y * 100 + '%',
    width: box.width * 100 + '%', height: box.height * 100 + '%',
  };
}

function pngBlob(encoded) {
  if (typeof encoded !== 'string' || !encoded.startsWith('iVBORw0KGgo')) {
    throw new Error('수정본 이미지를 불러오지 못했어요.');
  }
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: 'image/png' });
}

export default function PosterTextEditor({ runPath, imageUrl, initialMode, placeCandidates, onClose }) {
  const dialog = useRef(null);
  const drag = useRef(null);
  const pending = useRef(null);
  const autoPlacePending = useRef(initialMode === 'qr');
  const onlyPlace = placeCandidates.length === 1 ? placeCandidates[0] : '';
  const [selection, setSelection] = useState(null);
  const [drawing, setDrawing] = useState(null);
  const [text, setText] = useState('');
  const [addQr, setAddQr] = useState(initialMode === 'qr');
  const [placeInput, setPlaceInput] = useState(onlyPlace);
  const [qrDestination, setQrDestination] = useState(null);
  const [preview, setPreview] = useState(null);
  const [tab, setTab] = useState('original');
  const [imageState, setImageState] = useState('loading');
  const [dimensions, setDimensions] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const element = dialog.current;
    if (!element.open) element.showModal();
    return () => {
      pending.current?.abort();
      if (element.open) element.close();
    };
  }, []);
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  function resetPreview() {
    setPreview(null);
    setTab('original');
    setError('');
  }

  function updateSelection(box) {
    setSelection(box);
    resetPreview();
  }

  function startSelection(event) {
    if (event.button !== 0 || !event.isPrimary || busy || imageState !== 'ready' || tab !== 'original') return;
    event.currentTarget.focus({ preventScroll: true });
    event.currentTarget.setPointerCapture(event.pointerId);
    const start = pointOnPoster(event);
    drag.current = { start, pointerId: event.pointerId };
    setDrawing(boxBetween(start, start));
  }

  function moveSelection(event) {
    if (drag.current?.pointerId === event.pointerId) {
      setDrawing(boxBetween(drag.current.start, pointOnPoster(event)));
    }
  }

  function finishSelection(event) {
    if (drag.current?.pointerId !== event.pointerId) return;
    const box = boxBetween(drag.current.start, pointOnPoster(event));
    drag.current = null;
    setDrawing(null);
    if (box.width < minimumSide || box.height < minimumSide) {
      setError('포스터 위에서 마우스를 끌어 영역을 지정해 주세요.');
      return;
    }
    updateSelection(box);
  }

  function cancelSelection(event) {
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = null;
    setDrawing(null);
  }

  function selectWithKeyboard(event) {
    if (busy || imageState !== 'ready' || tab !== 'original' || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (!selection) updateSelection(addQr
        ? defaultQrRegion
        : { x: 0.25, y: 0.45, width: 0.5, height: 0.1 });
      return;
    }
    const offsets = { ArrowLeft: [-0.02, 0], ArrowRight: [0.02, 0], ArrowUp: [0, -0.02], ArrowDown: [0, 0.02] };
    if (!selection || !(event.key in offsets)) return;
    event.preventDefault();
    const [x, y] = offsets[event.key];
    const box = { ...selection };
    if (event.shiftKey) {
      box.width = clamp(box.width + x, minimumSide, 1 - box.x);
      box.height = clamp(box.height + y, minimumSide, 1 - box.y);
    } else {
      box.x = clamp(box.x + x, 0, 1 - box.width);
      box.y = clamp(box.y + y, 0, 1 - box.height);
    }
    updateSelection(box);
  }

  const canGenerate = imageState === 'ready' && (addQr ? placeInput.trim() : selection && text.trim());
  const generateEdit = useCallback(async (region, { mapUrl, replacement }) => {
    if (pending.current || drag.current || imageState !== 'ready') return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError('');
    try {
      let blob;
      if (mapUrl) {
        blob = await renderPosterMapQr(imageUrl, region, mapUrl, controller.signal);
      } else {
        const result = await frameworkJson(runPath + '/poster-text-edits', {
          method: 'POST', signal: controller.signal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ region, text: replacement }),
        });
        if (result?.error) throw new Error(result.error.message);
        blob = pngBlob(result?.image_base64);
      }
      if (controller.signal.aborted) return;
      setPreview(URL.createObjectURL(blob));
      setTab('preview');
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message);
    } finally {
      pending.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }, [imageState, imageUrl, runPath]);

  useEffect(() => {
    if (!autoPlacePending.current || !addQr || !onlyPlace || imageState !== 'ready') return;
    autoPlacePending.current = false;
    const mapUrl = naverMapUrl(onlyPlace);
    setPlaceInput(onlyPlace);
    setQrDestination(mapUrl);
    void generateEdit(null, { mapUrl });
  }, [addQr, onlyPlace, imageState, generateEdit]);

  function selectPlace(place) {
    if (pending.current || drag.current) return;
    const mapUrl = naverMapUrl(place);
    if (!mapUrl) return;
    autoPlacePending.current = false;
    setPlaceInput(place.trim());
    setQrDestination(mapUrl);
    resetPreview();
    void generateEdit(selection, { mapUrl });
  }

  function closeEditor() {
    if (pending.current) return;
    dialog.current.close();
    onClose();
  }

  const box = drawing || selection;
  return <dialog ref={dialog} className="rv-poster-edit" aria-labelledby="poster-edit-title" onCancel={event => {
    event.preventDefault();
    closeEditor();
  }}>
    <header className="rv-poster-edit-header">
      <div>
        <h2 id="poster-edit-title">{addQr ? '네이버지도 QR 생성' : '포스터 문구 수정'}</h2>
      </div>
      <button type="button" className="rv-poster-edit-close" aria-label="편집 닫기" disabled={busy} onClick={closeEditor}>×</button>
    </header>
    <div className="rv-poster-edit-body">
      <section className="rv-poster-edit-visual" aria-label="포스터 비교">
        <div className="rv-poster-edit-compare">
          <div role="group" aria-label="포스터 비교 보기">
            <button type="button" aria-pressed={tab === 'original'} onClick={() => setTab('original')}>원본</button>
            <button type="button" aria-pressed={tab === 'preview'} disabled={!preview} onClick={() => setTab('preview')}>수정본</button>
          </div>
          {dimensions && <span>{dimensions.width} × {dimensions.height}</span>}
        </div>
        <div className="rv-poster-edit-mat">
          <div className="rv-poster-edit-stage" role="group" aria-label={addQr ? 'QR 영역 선택' : '문구 영역 선택'}
            aria-describedby="poster-edit-selection-help"
            tabIndex={!busy && tab === 'original' && imageState === 'ready' ? 0 : undefined} data-mode={tab}
            style={dimensions ? { '--poster-aspect': dimensions.width / dimensions.height } : undefined}
            onPointerDown={startSelection} onPointerMove={moveSelection} onPointerUp={finishSelection}
            onPointerCancel={cancelSelection} onLostPointerCapture={cancelSelection} onKeyDown={selectWithKeyboard}>
            <img src={tab === 'preview' ? preview : imageUrl}
              alt={tab === 'preview' ? (addQr ? '네이버지도 QR을 넣은 포스터' : '문구를 수정한 포스터') : '검수한 포스터'}
              draggable={false}
              onLoad={event => {
                if (tab === 'preview') return;
                setDimensions({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight });
                setImageState('ready');
              }} onError={() => {
                if (tab === 'original') setImageState('error');
                else { setPreview(null); setTab('original'); setError('수정본 이미지를 불러오지 못했어요.'); }
              }} />
            {box && tab === 'original' && <div className="rv-poster-edit-selection" style={boxStyle(box)} aria-hidden="true" />}
          </div>
          {imageState === 'loading' && <p role="status">포스터를 불러오고 있어요.</p>}
          {imageState === 'error' && <p role="alert">검수 포스터를 불러오지 못했어요.</p>}
        </div>
      </section>
      <form className="rv-poster-edit-form" onSubmit={event => {
        event.preventDefault();
        if (!canGenerate) return;
        if (addQr) selectPlace(placeInput);
        else void generateEdit(selection, { replacement: text.trim() });
      }}>
        <label className="rv-poster-edit-toggle">
          <input type="checkbox" checked={addQr} disabled={busy} onChange={event => {
            setAddQr(event.target.checked);
            autoPlacePending.current = event.target.checked;
            setPlaceInput(onlyPlace);
            setQrDestination(null);
            updateSelection(null);
          }} />
          네이버지도 QR 추가
        </label>
        {addQr && <section>
          <label htmlFor="poster-qr-place">장소명 또는 도로명 주소</label>
          <input id="poster-qr-place" type="text" value={placeInput} maxLength={120} disabled={busy}
            placeholder="예: 서울특별시 중구 세종대로 110" onChange={event => {
              autoPlacePending.current = false;
              setPlaceInput(event.target.value);
              setQrDestination(null);
              resetPreview();
            }} />
          {placeCandidates.length > 1 && <div className="rv-poster-place-suggestions" role="group" aria-label="검수 장소 후보">
            {placeCandidates.map(place => <button type="button" key={place} className="rv-button secondary"
              aria-pressed={Boolean(qrDestination && placeInput === place)} disabled={busy || imageState !== 'ready' || Boolean(drawing)}
              onClick={() => selectPlace(place)}>{place}</button>)}
          </div>}
          {qrDestination && <div className="rv-poster-edit-map-links">
            <a href={qrDestination} target="_blank" rel="noopener noreferrer">네이버지도에서 위치 확인 ↗</a>
          </div>}
        </section>}
        <section>
          <h3>{addQr ? 'QR을 넣을 영역' : '수정 영역'}</h3>
          <p id="poster-edit-selection-help">{addQr && '기본 위치는 오른쪽 아래예요. 위치를 바꾸려면 원본에서 영역을 선택하세요. '}
            포스터 위에서 드래그하거나, 키보드에서 Enter로 선택한 뒤 방향키로 이동하세요. Shift와 방향키를 함께 누르면 크기를 조절할 수 있어요.</p>
          <div className="rv-poster-edit-region">
            <span role="status">{selection ? '영역 선택 완료' : addQr ? '오른쪽 아래에 자동 배치' : '영역을 선택해 주세요'}</span>
            {selection && <button type="button" className="text-btn" disabled={busy} onClick={() => updateSelection(null)}>선택 지우기</button>}
          </div>
        </section>
        {!addQr && <section>
          <label htmlFor="poster-edit-text">바꿀 문구</label>
          <input id="poster-edit-text" type="text" placeholder="예: 11.20–11.28" value={text} maxLength={80} disabled={busy}
            onChange={event => { setText(event.target.value); resetPreview(); }} />
          <p>포스터에 표시할 문구를 한 줄로 입력하세요.</p>
        </section>}
        {error && <p className="rv-poster-edit-error" role="alert">{error}</p>}
        <div className="rv-poster-edit-controls">
          {(!addQr || !preview) && <button type="submit"
            className={'rv-button ' + (preview ? 'secondary' : 'primary')} aria-busy={busy}
            disabled={busy || Boolean(drawing) || !canGenerate}>
            {busy ? (addQr ? '포스터에 QR을 넣고 있어요' : '포스터를 수정하고 있어요')
              : addQr ? '포스터에 QR 넣기' : preview ? '수정본 다시 만들기' : '수정본 만들기'}
          </button>}
          {busy && !addQr && <p role="status">이미지 수정이 끝나면 여기에서 확인할 수 있어요.</p>}
          {preview && !busy && <>
            <a className="rv-button primary" href={preview} download={addQr ? '네이버지도_QR_포스터.png' : '수정_포스터.png'}>수정본 다운로드</a>
            <p>{addQr ? '배포 전에 QR을 스캔해 장소가 맞는지 확인해 주세요.' : '다운로드하기 전에 문구가 정확히 바뀌었는지 확인해 주세요.'}</p>
          </>}
        </div>
      </form>
    </div>
  </dialog>;
}
