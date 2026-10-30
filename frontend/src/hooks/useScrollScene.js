import { useEffect, useRef } from 'react';

// Sticky travel is the track's extra height, not the document's scroll percentage.
export function scrollSceneProgress(top, trackHeight, stageHeight, stickyTop = 24) {
  if (![top, trackHeight, stageHeight, stickyTop].every(Number.isFinite)) return 0;
  const travel = trackHeight - stageHeight;
  if (travel <= 0) return 0;
  return Math.min(1, Math.max(0, (stickyTop - top) / travel));
}

export function scrollSceneFrame(progress) {
  const p = Number.isFinite(progress) ? Math.min(1, Math.max(0, progress)) : 0;
  const eased = p * p * (3 - 2 * p);
  return { scale: 1 + eased * 0.065, windowScale: 1 + eased * 0.12, windowShift: -eased * 12, radius: 32 - eased * 12 };
}

export function useScrollScene() {
  const rootRef = useRef(null);
  const sceneRef = useRef(null);
  const stageRef = useRef(null);

  useEffect(() => {
    const root = rootRef.current;
    const scene = sceneRef.current;
    const stage = stageRef.current;
    if (!root || !scene || !stage) return undefined;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const roomy = window.matchMedia('(min-width: 900px) and (min-height: 760px)');
    const revealNodes = [...root.querySelectorAll('[data-reveal]')];
    let frameId = 0;
    let active = false;
    let disposed = false;
    let observer;

    function paint() {
      frameId = 0;
      if (disposed) return;
      const progress = active ? scrollSceneProgress(scene.getBoundingClientRect().top, scene.offsetHeight, stage.offsetHeight) : 0;
      const frame = scrollSceneFrame(progress);
      scene.style.setProperty('--scene-progress', progress.toFixed(4));
      scene.style.setProperty('--scene-scale', frame.scale.toFixed(4));
      scene.style.setProperty('--window-scale', frame.windowScale.toFixed(4));
      scene.style.setProperty('--window-shift', `${frame.windowShift.toFixed(2)}px`);
      scene.style.setProperty('--scene-radius', `${frame.radius.toFixed(2)}px`);
    }
    function schedule() {
      if (!disposed && !frameId) frameId = window.requestAnimationFrame(paint);
    }
    function configure() {
      active = !reduced.matches && roomy.matches;
      root.dataset.motion = active ? 'on' : 'off';
      observer?.disconnect();
      observer = undefined;
      const animateReveal = active && 'IntersectionObserver' in window;
      root.dataset.reveal = animateReveal ? 'on' : 'off';
      if (animateReveal) {
        observer = new IntersectionObserver(entries => {
          for (const entry of entries) {
            if (!entry.isIntersecting) continue;
            entry.target.dataset.visible = 'true';
            observer?.unobserve(entry.target);
          }
        }, { threshold: 0.04, rootMargin: '0px 0px -24px 0px' });
        for (const node of revealNodes) {
          if (node.getBoundingClientRect().top < window.innerHeight) node.dataset.visible = 'true';
          if (node.dataset.visible !== 'true') observer.observe(node);
        }
      }
      schedule();
    }
    function revealFocused(event) {
      const section = event.target.closest?.('[data-reveal]');
      if (section && root.contains(section)) section.dataset.visible = 'true';
    }

    configure();
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule, { passive: true });
    reduced.addEventListener('change', configure);
    roomy.addEventListener('change', configure);
    root.addEventListener('focusin', revealFocused);
    const resizeObserver = 'ResizeObserver' in window ? new ResizeObserver(schedule) : null;
    resizeObserver?.observe(stage);
    resizeObserver?.observe(scene);
    return () => {
      disposed = true;
      window.cancelAnimationFrame(frameId);
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
      reduced.removeEventListener('change', configure);
      roomy.removeEventListener('change', configure);
      root.removeEventListener('focusin', revealFocused);
      observer?.disconnect();
      resizeObserver?.disconnect();
      delete root.dataset.motion;
      delete root.dataset.reveal;
      for (const node of revealNodes) delete node.dataset.visible;
      for (const property of ['--scene-progress', '--scene-scale', '--window-scale', '--window-shift', '--scene-radius']) scene.style.removeProperty(property);
    };
  }, []);
  return { rootRef, sceneRef, stageRef };
}
