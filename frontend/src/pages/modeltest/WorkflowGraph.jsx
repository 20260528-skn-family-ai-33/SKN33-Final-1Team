import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { boardPoint, boardView, DEFAULT_VIEW, fitBoard, nodePosition, zoomAt } from './modeltestBoard.js';

export default function WorkflowGraph({ boardId, label, nodes, edges, positions, onChange, disabled, onConfigure, error = '', nodeActions = () => [], addActions = [], onUndo, canUndo, onRedo, canRedo }) {
  const arrowId = useId().replace(/:/g, ''); const root = useRef(null); const viewport = useRef(null); const menuRef = useRef(null); const focusBeforeMenu = useRef(null);
  const storageKey = `creative-checker-board-${boardId}`;
  const [view, setView] = useState(() => { try { return boardView(JSON.parse(localStorage.getItem(storageKey))); } catch { return { ...DEFAULT_VIEW }; } });
  const viewRef = useRef(view); viewRef.current = view;
  const [drag, setDrag] = useState(null); const gesture = useRef(null); const [space, setSpace] = useState(false);
  const [source, setSource] = useState(''); const [point, setPoint] = useState(null); const [notice, setNotice] = useState('');
  const [menu, setMenu] = useState(null); const [fullscreen, setFullscreen] = useState(false);
  const [hoveredEdge, setHoveredEdge] = useState(null); const [focusedEdge, setFocusedEdge] = useState(null);
  const placed = new Map(nodes.map((node, index) => [node.id, drag?.id === node.id ? drag : nodePosition(positions, node.id, index)]));
  const title = id => nodes.find(node => node.id === id)?.title || id;
  const localPoint = event => { const rect = viewport.current.getBoundingClientRect(); return { x: event.clientX - rect.left, y: event.clientY - rect.top }; };
  const worldPoint = event => boardPoint(view, localPoint(event));
  function closeMenu() { setMenu(null); if (focusBeforeMenu.current?.isConnected) focusBeforeMenu.current.focus({ preventScroll: true }); }
  function fit() { setView(current => fitBoard(current, [...placed.values()], viewport.current.clientWidth, viewport.current.clientHeight)); }
  function zoom(value) { const element = viewport.current; setView(current => zoomAt(current, value, { x: element.clientWidth / 2, y: element.clientHeight / 2 })); }
  async function configure(id) {
    if (document.fullscreenElement === root.current) await document.exitFullscreen();
    onConfigure(id);
  }
  async function toggleFullscreen() {
    try { if (document.fullscreenElement === root.current) await document.exitFullscreen(); else await root.current.requestFullscreen(); }
    catch { setNotice('전체화면을 열지 못했습니다. 보드 높이 조절을 사용해 주세요.'); }
  }
  useEffect(() => {
    const timer = setTimeout(() => { try { localStorage.setItem(storageKey, JSON.stringify(view)); } catch { setNotice('보드 보기 설정을 저장하지 못했습니다. 새로고침하면 초기화됩니다.'); } }, 150);
    return () => clearTimeout(timer);
  }, [view, storageKey]);
  useEffect(() => {
    const save = () => { try { localStorage.setItem(storageKey, JSON.stringify(viewRef.current)); } catch { /* Storage errors are shown by the mounted view. */ } };
    window.addEventListener('pagehide', save);
    return () => { window.removeEventListener('pagehide', save); save(); };
  }, [storageKey]);
  useEffect(() => {
    const element = viewport.current;
    const wheel = event => {
      if (gesture.current || event.target.closest('input,textarea,select')) return;
      event.preventDefault(); setMenu(null);
      const rect = element.getBoundingClientRect(); const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element.clientHeight : 1);
      setView(current => zoomAt(current, current.zoom * Math.exp(-Math.max(-400, Math.min(400, delta)) * .002), { x: event.clientX - rect.left, y: event.clientY - rect.top }));
    };
    element.addEventListener('wheel', wheel, { passive: false });
    return () => element.removeEventListener('wheel', wheel);
  }, []);
  useEffect(() => {
    const release = () => { setSpace(false); gesture.current = null; setDrag(null); setSource(''); setPoint(null); setMenu(null); };
    const keyup = event => { if (event.code === 'Space') setSpace(false); };
    const changed = () => { setFullscreen(document.fullscreenElement === root.current); release(); };
    window.addEventListener('blur', release); window.addEventListener('keyup', keyup); document.addEventListener('fullscreenchange', changed);
    return () => { window.removeEventListener('blur', release); window.removeEventListener('keyup', keyup); document.removeEventListener('fullscreenchange', changed); };
  }, []);
  useEffect(() => { if (disabled) { gesture.current = null; setDrag(null); setSource(''); setPoint(null); setMenu(null); } }, [disabled]);
  useEffect(() => { if (source && !nodes.some(node => node.id === source)) { setSource(''); setPoint(null); } }, [nodes, source]);
  useEffect(() => { setHoveredEdge(null); setFocusedEdge(null); }, [edges]);
  useLayoutEffect(() => {
    if (!menu || !menuRef.current) return;
    const element = menuRef.current; const bounds = root.current.getBoundingClientRect();
    element.style.left = `${Math.max(4, Math.min(menu.x, bounds.width - element.offsetWidth - 4))}px`;
    element.style.top = `${Math.max(4, Math.min(menu.y, bounds.height - element.offsetHeight - 4))}px`;
    element.querySelector('button:not(:disabled)')?.focus();
  }, [menu]);
  useEffect(() => {
    if (!menu) return;
    const outside = event => { if (!menuRef.current?.contains(event.target)) setMenu(null); };
    document.addEventListener('pointerdown', outside); return () => document.removeEventListener('pointerdown', outside);
  }, [menu]);
  function openMenu(event, target) {
    event.preventDefault(); event.stopPropagation(); gesture.current = null; setDrag(null); setSource(''); setPoint(null);
    focusBeforeMenu.current = event.target.closest('button,[tabindex]') || viewport.current;
    const bounds = root.current.getBoundingClientRect(); const rect = event.currentTarget.getBoundingClientRect();
    const client = event.clientX || event.clientY ? event : { clientX: rect.left + 16, clientY: rect.top + 16 };
    setMenu({ ...target, x: client.clientX - bounds.left, y: client.clientY - bounds.top, point: worldPoint(client) });
  }
  function connect(target) {
    if (!source || disabled) return;
    if (source === target || edges.some(edge => edge.source === source && edge.target === target)) setNotice('자기 연결 또는 중복 연결은 추가할 수 없습니다.');
    else { onChange({ edges: [...edges, { source, target }] }); setNotice(''); }
    setSource(''); setPoint(null);
  }
  function disconnect(edge) { if (!disabled) onChange({ edges: edges.filter(item => item.source !== edge.source || item.target !== edge.target) }); }
  function begin(event, type, id) {
    event.preventDefault(); event.stopPropagation(); setMenu(null); viewport.current.focus({ preventScroll: true });
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = { type, id, pointerId: event.pointerId, x: event.clientX, y: event.clientY, view, pos: placed.get(id), point: worldPoint(event) };
  }
  function pointerDown(event) {
    if (event.button === 1 || (event.button === 0 && (space || !event.target.closest('button,.mt-workflow-node,.mt-wire-hit')))) {
      begin(event, 'pan'); setSource(''); setPoint(null);
    }
  }
  function move(event) {
    const active = gesture.current;
    if (active && active.pointerId === event.pointerId) {
      const dx = event.clientX - active.x; const dy = event.clientY - active.y;
      if (active.type === 'pan') setView({ ...active.view, x: active.view.x + dx, y: active.view.y + dy });
      else if (active.type === 'resize') setView(boardView({ ...active.view, height: active.view.height + dy }));
      else if (!disabled) setDrag({ id: active.id, x: active.pos.x + dx / active.view.zoom, y: active.pos.y + dy / active.view.zoom });
    } else if (source) setPoint(worldPoint(event));
  }
  function end(event) {
    const active = gesture.current;
    if (!active || active.pointerId !== event.pointerId) return;
    if (active.type === 'node' && !disabled && (event.clientX !== active.x || event.clientY !== active.y)) onChange({ positions: { ...positions, [active.id]: { x: active.pos.x + (event.clientX - active.x) / active.view.zoom, y: active.pos.y + (event.clientY - active.y) / active.view.zoom } } });
    gesture.current = null; setDrag(null);
  }
  function keyDown(event) {
    if (event.target.closest('input,textarea,select,[contenteditable=true]')) return;
    if (event.code === 'Space' && !menu && viewport.current.contains(event.target)) { event.preventDefault(); setSpace(true); }
    if ((event.ctrlKey || event.metaKey) && ['z', 'y'].includes(event.key.toLowerCase())) { setMenu(null); gesture.current = null; setDrag(null); setSource(''); setPoint(null); }
    if (event.key === 'Escape') { if (menu) { event.preventDefault(); event.stopPropagation(); closeMenu(); } setSource(''); setPoint(null); gesture.current = null; setDrag(null); }
  }
  function keyMove(event, id, pos) {
    const steps = { ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] };
    if (!steps[event.key] || disabled || menu) return;
    event.preventDefault(); const [x, y] = steps[event.key]; onChange({ positions: { ...positions, [id]: { x: pos.x + x, y: pos.y + y } } });
  }
  const curve = (from, to) => `M ${from.x} ${from.y} C ${from.x + 70} ${from.y}, ${to.x - 70} ${to.y}, ${to.x} ${to.y}`;
  let actions = [];
  if (menu?.type === 'node') actions = [{ label: '설정 열기', view: true, run: () => configure(menu.id) }, ...nodeActions(menu.id)];
  if (menu?.type === 'edge') actions = [{ label: '연결 해제', danger: true, run: () => disconnect(menu.edge) }, { label: '출발 노드 설정', view: true, run: () => configure(menu.edge.source) }, { label: '도착 노드 설정', view: true, run: () => configure(menu.edge.target) }];
  if (menu?.type === 'background') actions = [...addActions.map(action => ({ ...action, run: () => action.run(menu.point) })), { label: '전체 보기', view: true, run: fit }, { label: '100%로 보기', view: true, run: () => zoom(1) }, { label: '배치 초기화', run: () => onChange({ positions: {} }) }];
  return <div className="mt-board" ref={root} data-board={boardId} aria-label={label} onKeyDown={keyDown} onPointerMove={move} onPointerUp={end} onPointerCancel={() => { gesture.current = null; setDrag(null); setSource(''); setPoint(null); }}>
    <p className="mt-workflow-notice" role="status">{notice || (source ? `${title(source)}의 출력을 연결할 입력 점을 선택하세요.` : error || '실행 가능한 연결입니다.')}{source && <button type="button" onClick={() => { setSource(''); setPoint(null); }}>연결 취소</button>}</p>
    <div ref={viewport} className={`mt-workflow-scroll ${space || gesture.current?.type === 'pan' ? 'is-panning' : ''}`} tabIndex={0} aria-label={`${label} 작업 영역`}
      style={{ height: view.height, backgroundSize: `${20 * view.zoom}px ${20 * view.zoom}px`, backgroundPosition: `${view.x}px ${view.y}px` }}
      onPointerDownCapture={pointerDown} onContextMenu={event => openMenu(event, { type: 'background' })}>
      <div className="mt-workflow-canvas" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})` }}>
        <svg className="mt-workflow-wires"><defs><marker id={arrowId} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="currentColor" /></marker></defs>
          {edges.map(edge => { const a = placed.get(edge.source); const b = placed.get(edge.target); const d = a && b && curve({ x: a.x + 250, y: a.y + 48 }, { x: b.x, y: b.y + 48 }); return d && <g key={`${edge.source}-${edge.target}`}>
            <path d={d} markerEnd={`url(#${arrowId})`} aria-hidden="true" />
            <path className="mt-wire-hit" d={d} tabIndex={0} role="button" aria-label={`${title(edge.source)}에서 ${title(edge.target)} 연결 메뉴`} onContextMenu={event => openMenu(event, { type: 'edge', edge })}
              onPointerEnter={() => setHoveredEdge(edge)} onPointerLeave={() => setHoveredEdge(null)} onFocus={() => setFocusedEdge(edge)} onBlur={() => setFocusedEdge(null)}
              onKeyDown={event => { if (event.key === 'Enter' || (event.shiftKey && event.key === 'F10')) openMenu(event, { type: 'edge', edge }); }} />
          </g>; })}
          {source && point && placed.has(source) && <path className="is-connecting" aria-hidden="true" d={curve({ x: placed.get(source).x + 250, y: placed.get(source).y + 48 }, point)} />}
        </svg>
        {nodes.map(node => { const pos = placed.get(node.id); return <div className="mt-workflow-node" key={node.id} style={{ left: pos.x, top: pos.y }} data-node={node.id} onContextMenu={event => openMenu(event, { type: 'node', id: node.id })}>
          <button type="button" className="mt-node-port mt-node-input" disabled={disabled} aria-label={`${node.title} 입력 연결`} onPointerUp={() => connect(node.id)} onClick={event => { if (event.detail === 0) connect(node.id); }} />
          <button type="button" className="mt-node-drag" aria-disabled={disabled} aria-label={`${node.title} 노드 이동`} onKeyDown={event => keyMove(event, node.id, pos)} onPointerDown={event => { if (event.button === 0 && !disabled) begin(event, 'node', node.id); }}>{node.title}</button>
          <small>{node.id === 'frames' ? '영상 프레임 샘플링' : node.id === 'candidate_filter' ? '최소 유사도 · 상위 후보 수' : node.description || '단계를 설정하세요'}</small>
          <button type="button" className="text-btn" onClick={() => configure(node.id)}>설정</button>
          <button type="button" className={`mt-node-port mt-node-output ${source === node.id ? 'is-selected' : ''}`} disabled={disabled} aria-label={`${node.title} 출력 연결`}
            onPointerDown={event => { if (event.button === 0) { event.preventDefault(); setSource(node.id); setNotice(''); setPoint(null); } }} onClick={event => { if (event.detail === 0) { setSource(node.id); setNotice(''); } }} />
        </div>; })}
      </div>
    </div>
    {!fullscreen && <div className="mt-board-resize" tabIndex={0} role="separator" aria-label={`${label} 높이 조절`} aria-orientation="horizontal" aria-valuenow={Math.round(view.height)} aria-valuemin={280} aria-valuemax={1000}
      onPointerDown={event => { if (event.button === 0) begin(event, 'resize'); }} onKeyDown={event => { if (['ArrowUp', 'ArrowDown'].includes(event.key)) { event.preventDefault(); setView(current => boardView({ ...current, height: current.height + (event.key === 'ArrowUp' ? -20 : 20) })); } }} />}
    <div className="mt-board-controls"><span>휠 확대 · 배경 드래그 · Space 이동 · 우클릭 편집</span><button type="button" disabled={disabled || !canUndo} onClick={onUndo}>되돌리기 <kbd>Ctrl+Z</kbd></button>
      <button type="button" disabled={disabled || !canRedo} onClick={onRedo} title="Ctrl+Shift+Z / Ctrl+Y">다시 실행 <kbd>Ctrl+Shift+Z</kbd></button>
      <button type="button" aria-label="보드 축소" onClick={() => zoom(view.zoom / 1.2)}>−</button><button type="button" aria-label="보드 100%로 보기" onClick={() => zoom(1)}>{Math.round(view.zoom * 100)}%</button><button type="button" aria-label="보드 확대" onClick={() => zoom(view.zoom * 1.2)}>+</button>
      <button type="button" onClick={fit}>전체 보기</button><button type="button" onClick={toggleFullscreen}>{fullscreen ? '전체화면 종료' : '전체화면'}</button></div>
    <ul className="mt-workflow-edges" aria-label="워크플로우 연결 목록">{edges.map(edge => <li key={`${edge.source}-${edge.target}`} className={hoveredEdge === edge || focusedEdge === edge ? 'is-highlighted' : undefined}><span>{title(edge.source)} → {title(edge.target)}</span><button type="button" disabled={disabled} aria-label={`${title(edge.source)}에서 ${title(edge.target)} 연결 삭제`} onClick={() => disconnect(edge)}>삭제</button></li>)}</ul>
    {!edges.length && <p className="mt-hint">연결이 없는 단계는 동시에 실행 가능한 시작 단계가 됩니다. 필수 입력 연결은 실행 전에 검사합니다.</p>}
    {menu && <div ref={menuRef} className="mt-board-menu" role="menu" aria-label="보드 편집 메뉴" onKeyDown={event => {
      if (event.key === 'Tab') { closeMenu(); return; }
      const buttons = [...menuRef.current.querySelectorAll('button:not(:disabled)')]; const index = buttons.indexOf(document.activeElement);
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) { event.preventDefault(); buttons[event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowUp' ? -1 : 1) + buttons.length) % buttons.length]?.focus(); }
    }}>{actions.map((action, index) => <button type="button" role="menuitem" key={index} className={action.danger ? 'mt-error' : ''} disabled={action.disabled || (disabled && !action.view)} onClick={() => { closeMenu(); action.run(); }}>{action.label}</button>)}</div>}
  </div>;
}
