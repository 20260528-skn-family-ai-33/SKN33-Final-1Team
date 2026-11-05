import test from 'node:test';
import assert from 'node:assert/strict';

test('root environment proxy settings are used, with process overrides taking priority', async () => {
  const { resolveProxyTargets } = await import('../../../vite.config.js');
  const file = { API_PROXY_TARGET: 'http://127.0.0.1:8012', VIDEO_API_PROXY_TARGET: 'http://127.0.0.1:8013', DB_APP_PASSWORD: 'not-a-proxy' };
  assert.deepEqual(resolveProxyTargets({}, file), {
    api: 'http://127.0.0.1:8012', framework: 'http://127.0.0.1:8012', video: 'http://127.0.0.1:8013',
  });
  assert.equal(resolveProxyTargets({ VIDEO_API_PROXY_TARGET: 'http://127.0.0.1:9013' }, file).video, 'http://127.0.0.1:9013');
});

test('video endpoints use their own target without moving authentication or framework routes', async () => {
  process.env.API_PROXY_TARGET = 'http://127.0.0.1:8012';
  process.env.FRAMEWORK_API_PROXY_TARGET = 'http://127.0.0.1:8012';
  process.env.VIDEO_API_PROXY_TARGET = 'http://127.0.0.1:8013';
  const { default: config } = await import('../../../vite.config.js');
  const proxy = config({ mode: 'development' }).server.proxy;
  assert.equal(proxy['/api/framework'].target, 'http://127.0.0.1:8012');
  assert.equal(proxy['/api/video-reviews'].target, 'http://127.0.0.1:8013');
  assert.equal(proxy['/api/video-logos'].target, 'http://127.0.0.1:8013');
  assert.equal(proxy['/api'].target, 'http://127.0.0.1:8012');
  assert.equal(proxy['/api/video-reviews'].changeOrigin, false);
  assert.equal(proxy['/api/video-logos'].xfwd, false);
});
