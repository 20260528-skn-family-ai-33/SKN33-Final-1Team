import test from 'node:test';
import assert from 'node:assert/strict';
import { scrollSceneProgress, scrollSceneFrame } from './useScrollScene.js';

test('sticky scene clamps progress before and after its own travel', () => {
  assert.equal(scrollSceneProgress(130, 1160, 820), 0);
  assert.equal(scrollSceneProgress(24, 1160, 820), 0);
  assert.equal(scrollSceneProgress(-146, 1160, 820), 0.5);
  assert.equal(scrollSceneProgress(-316, 1160, 820), 1);
  assert.equal(scrollSceneProgress(-1000, 1160, 820), 1);
});
test('collapsed and invalid geometry leaves the scene stable', () => {
  assert.equal(scrollSceneProgress(-100, 820, 820), 0);
  assert.equal(scrollSceneProgress(-100, 0, 820), 0);
  assert.equal(scrollSceneProgress(Number.NaN, 1160, 820), 0);
  assert.deepEqual(scrollSceneFrame(Number.NaN), scrollSceneFrame(0));
});
test('zoom stays bounded and moves forward monotonically', () => {
  let previous = scrollSceneFrame(0);
  for (let step = 1; step <= 100; step++) {
    const frame = scrollSceneFrame(step / 100);
    assert.ok(frame.scale >= previous.scale && frame.scale <= 1.065);
    assert.ok(frame.windowScale >= previous.windowScale && frame.windowScale <= 1.12);
    assert.ok(frame.radius <= previous.radius && frame.radius >= 20);
    previous = frame;
  }
  assert.deepEqual(scrollSceneFrame(-1), scrollSceneFrame(0));
  assert.deepEqual(scrollSceneFrame(2), scrollSceneFrame(1));
});
