import React, { useEffect, useRef, useState } from 'react';
import { Icon } from '../../components/Icon.jsx';
import { frameworkJson } from './frameworkClient.js';
import '../../styles/poster-text-editor.css';

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const minimumSide = 0.015;

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

export default function PosterTextEditor({ runPath, imageUrl, onClose }) {
  const dialog = useRef(null);
  const drag = useRef(null);
  const pending = useRef(null);
  const [selection, setSelection] = useState(null);
  const [drawing, setDrawing] = useState(null);
  const [text, setText] = useState('');
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

  function updateSelection(box) {
    setSelection(box);
    setPreview(null);
    setTab('original');
    setError('');
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
      setError('포스터 위에서 마우스를 끌어 문구 영역을 지정해 주세요.');
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
      if (!selection) updateSelection({ x: 0.25, y: 0.45, width: 0.5, height: 0.1 });
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

  async function generateEdit(event) {
    event.preventDefault();
    const replacement = text.trim();
    if (pending.current || drag.current || !selection || !replacement || imageState !== 'ready') return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError('');
    try {
      const result = await frameworkJson(runPath + '/poster-text-edits', {
        method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ region: selection, text: replacement }),
      });
      if (controller.signal.aborted) return;
      if (result?.error) throw new Error(result.error.message);
      setPreview(URL.createObjectURL(pngBlob(result?.image_base64)));
      setTab('preview');
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message);
    } finally {
      pending.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
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
        <h2 id="poster-edit-title">포스터 문구 수정</h2>
        <p>바꿀 부분을 선택하고 새 문구를 입력하세요.</p>
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
          <div className="rv-poster-edit-stage" role="group" aria-label="문구 영역 선택" aria-describedby="poster-edit-selection-help"
            tabIndex={!busy && tab === 'original' && imageState === 'ready' ? 0 : undefined} data-mode={tab}
            style={dimensions ? { '--poster-aspect': dimensions.width / dimensions.height } : undefined}
            onPointerDown={startSelection} onPointerMove={moveSelection} onPointerUp={finishSelection}
            onPointerCancel={cancelSelection} onLostPointerCapture={cancelSelection} onKeyDown={selectWithKeyboard}>
            <img src={tab === 'preview' ? preview : imageUrl}
              alt={tab === 'preview' ? '문구를 수정한 포스터' : '검수한 포스터'} draggable={false}
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
      <form className="rv-poster-edit-form" onSubmit={generateEdit}>
        <section>
          <h3>수정 영역</h3>
          <p id="poster-edit-selection-help">포스터 위에서 드래그하세요. 키보드는 Enter로 선택한 뒤 방향키로 이동하고, Shift와 함께 누르면 크기를 조절할 수 있어요.</p>
          <div className="rv-poster-edit-region">
            <span role="status">{selection ? '영역 선택 완료' : '영역을 선택해 주세요'}</span>
            {selection && <button type="button" className="text-btn" disabled={busy} onClick={() => updateSelection(null)}>선택 지우기</button>}
          </div>
        </section>
        <section>
          <label htmlFor="poster-edit-text">바꿀 문구</label>
          <input id="poster-edit-text" type="text" placeholder="예: 11.20–11.28" value={text} maxLength={80} disabled={busy}
            onChange={event => { setText(event.target.value); setPreview(null); setTab('original'); setError(''); }} />
          <p>포스터에 표시할 문구를 한 줄로 입력하세요.</p>
        </section>
        {error && <p className="rv-poster-edit-error" role="alert">{error}</p>}
        <div className="rv-poster-edit-controls">
          <button type="submit" className={'rv-button ' + (preview ? 'secondary' : 'primary')} aria-busy={busy}
            disabled={busy || Boolean(drawing) || !selection || !text.trim() || imageState !== 'ready'}>
            {busy ? '포스터를 수정하고 있어요' : preview ? '수정본 다시 만들기' : '수정본 만들기'}
          </button>
          {busy && <p role="status">이미지 수정이 끝나면 여기에서 확인할 수 있어요.</p>}
          {preview && !busy && <>
            <a className="rv-button primary" href={preview} download="수정_포스터.png">수정본 다운로드</a>
            <p>다운로드하기 전에 문구가 정확히 바뀌었는지 확인해 주세요.</p>
          </>}
        </div>
      </form>
    </div>
  </dialog>;
}
