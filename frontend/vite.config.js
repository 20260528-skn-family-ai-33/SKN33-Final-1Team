import { defineConfig, loadEnv } from 'vite';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));

export function resolveProxyTargets(processValues, rootValues) {
  const api = processValues.API_PROXY_TARGET || rootValues.API_PROXY_TARGET || 'http://127.0.0.1:8000';
  return {
    api,
    framework: processValues.FRAMEWORK_API_PROXY_TARGET || rootValues.FRAMEWORK_API_PROXY_TARGET || 'http://127.0.0.1:8012',
    video: processValues.VIDEO_API_PROXY_TARGET || rootValues.VIDEO_API_PROXY_TARGET || api,
  };
}

export default defineConfig(({ mode }) => {
  // Only proxy/profile settings are read from the repository root; no secrets
  // are exposed to Vite's client environment or emitted to browser code.
  const rootEnv = loadEnv(mode, root, ['API_PROXY_TARGET', 'FRAMEWORK_API_PROXY_TARGET', 'VIDEO_API_PROXY_TARGET', 'VITE_API_PROFILE']);
  const { api: apiTarget, framework: frameworkTarget, video: videoTarget } = resolveProxyTargets(process.env, rootEnv);
  const proxy = {
    '/api/framework': { target: frameworkTarget, changeOrigin: false, xfwd: false, ws: false },
    '/api/video-reviews': { target: videoTarget, changeOrigin: false, xfwd: false, ws: false },
    '/api/video-logos': { target: videoTarget, changeOrigin: false, xfwd: false, ws: false },
    '/api': { target: apiTarget, changeOrigin: false, xfwd: false, ws: false },
  };
  const profile = process.env.VITE_API_PROFILE || loadEnv(mode, process.cwd()).VITE_API_PROFILE || rootEnv.VITE_API_PROFILE || (mode === 'rebuild' ? 'rebuild' : mode === 'v1' ? 'v1' : 'legacy');
  if (!['legacy', 'v1', 'rebuild'].includes(profile)) throw new Error('VITE_API_PROFILE must be legacy, v1 or rebuild');
  return {
  define: { 'import.meta.env.VITE_API_PROFILE': JSON.stringify(profile) },
  plugins: [{ name: 'cc-frontend-profile', generateBundle() { this.emitFile({ type: 'asset', fileName: 'cc-frontend.json', source: JSON.stringify({ contract: 'cc-web-v1', profile }) }); } }],
  server: { proxy },
  preview: { proxy },
  };
});
