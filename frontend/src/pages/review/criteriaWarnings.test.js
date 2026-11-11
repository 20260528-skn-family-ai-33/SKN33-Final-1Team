import assert from 'node:assert/strict';
import test from 'node:test';
import { unreadImagePages } from './criteriaWarnings.js';

test('초안 경고에서 이미지를 읽지 않은 쪽 번호만 꺼낸다', () => {
  assert.deepEqual(unreadImagePages(['unapproved_draft', 'page_2_embedded_images_not_read',
    'page_11_embedded_images_not_read', 'text_layer_only_visual_layout_not_verified']), [2, 11]);
});

test('경고가 없거나 형식이 다르면 빈 목록이다', () => {
  assert.deepEqual(unreadImagePages(undefined), []);
  assert.deepEqual(unreadImagePages(['page_x_embedded_images_not_read', 'xpage_1_embedded_images_not_read']), []);
});
