import { defineConfig, loadEnv } from 'vite';

const apiTarget = process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000';

export default defineConfig(({ mode }) => {
  const profile = process.env.VITE_API_PROFILE || loadEnv(mode, process.cwd()).VITE_API_PROFILE || (mode === 'v1' ? 'v1' : 'legacy');
  if (!['legacy', 'v1'].includes(profile)) throw new Error('VITE_API_PROFILE must be legacy or v1');
  return {
  define: { 'import.meta.env.VITE_API_PROFILE': JSON.stringify(profile) },
  plugins: [{ name: 'cc-frontend-profile', generateBundle() { this.emitFile({ type: 'asset', fileName: 'cc-frontend.json', source: JSON.stringify({ contract: 'cc-web-v1', profile }) }); } }],
  server: { proxy: { '/api': { target: apiTarget, changeOrigin: false, xfwd: false, ws: false } } },
  preview: { proxy: { '/api': { target: apiTarget, changeOrigin: false, xfwd: false, ws: false } } },
  };
});
