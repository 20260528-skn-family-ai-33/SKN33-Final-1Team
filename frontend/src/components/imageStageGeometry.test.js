import test from 'node:test';
import assert from 'node:assert/strict';
import { imageStageSize, canShowImageEvidence, imageEvidenceFocus, imageStageScroll } from './imageStageGeometry.js';
import { evidenceBoxStyle } from '../workflow.js';
test('fit preserves aspect ratio and keeps portrait inside canvas', () => {
  const size = imageStageSize(600, 848, 700, 650);
  assert.ok(size.width <= 636 && size.height <= 586);
  assert.ok(Math.abs(size.width / size.height - 600 / 848) < 1e-10);
});
test('evidence stays attached to original pixels through zoom', () => {
  const box = evidenceBoxStyle([60, 84.8, 300, 424], 600, 848);
  for (const zoom of [.5, 1, 2, 3]) {
    const size = imageStageSize(600, 848, 700, 650, zoom);
    assert.equal(parseFloat(box.left) / 100 * size.width, 60 * zoom);
    assert.equal(parseFloat(box.width) / 100 * size.width, 240 * zoom);
  }
});
test('dimension mismatch or partial dimension never paints evidence', () => {
  assert.equal(canShowImageEvidence(600, 848, 600, 848), true);
  assert.equal(canShowImageEvidence(600, 848, 848, 600), false);
  assert.equal(canShowImageEvidence(600, 848, 600, undefined), false);
  assert.equal(canShowImageEvidence(600, 848), true);
  assert.equal(canShowImageEvidence(0, 0), false);
});
test('unloaded image has no geometry and zoom stays bounded', () => {
  assert.equal(imageStageSize(0, 0, 500, 500), null);
  assert.equal(imageStageSize(600, 848, 0, 500), null);
  assert.equal(imageStageSize(600, 848, 500, 500, 9).scale, 3);
  assert.equal(imageStageSize(600, 848, 500, 500, -1).scale, .25);
});
test('evidence focus keeps the original region centered after zoom', () => {
  const focus = imageEvidenceFocus([[120, 200, 450, 240]], 600, 848, 720, 480);
  const size = imageStageSize(600, 848, 720, 480, focus.zoom);
  const scroll = imageStageScroll(size, 720, 480, focus.target);
  const surfaceLeft = (Math.max(720, size.width + 64) - size.width) / 2;
  assert.equal(surfaceLeft + 285 * size.scale - scroll.left, 360);
  assert.equal(32 + 220 * size.scale - scroll.top, 240);
  assert.ok(size.scale > 1 && size.scale <= 3);
});
test('focus combines selected evidence, rejects foreign coordinates, and clamps scrolling at image edges', () => {
  assert.equal(imageEvidenceFocus([[600, 1, 700, 3]], 600, 848, 700, 500), null);
  assert.equal(imageEvidenceFocus([[-1, 1, 20, 3]], 600, 848, 700, 500), null);
  const focus = imageEvidenceFocus([[20, 20, 100, 60], [200, 300, 400, 400]], 600, 848, 700, 500);
  assert.deepEqual(focus.target, { x: 210, y: 210 });
  assert.deepEqual(imageStageScroll(imageStageSize(600, 848, 700, 500, 2), 700, 500, { x: 0, y: 0 }), { left: 0, top: 0 });
  assert.deepEqual(imageStageScroll(imageStageSize(600, 848, 700, 500, 2), 700, 500, { x: 600, y: 848 }), { left: 564, top: 1260 });
});
