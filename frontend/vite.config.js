import { defineConfig, loadEnv } from 'vite';

const apiTarget = process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000';
const frameworkTarget = process.env.FRAMEWORK_API_PROXY_TARGET || 'http://127.0.0.1:8012';
const proxy = {
  '/api/framework': { target: frameworkTarget, changeOrigin: false, xfwd: false, ws: false },
  '/api': { target: apiTarget, changeOrigin: false, xfwd: false, ws: false },
};

export default defineConfig(({ mode }) => {
  const profile = process.env.VITE_API_PROFILE || loadEnv(mode, process.cwd()).VITE_API_PROFILE || (mode === 'rebuild' ? 'rebuild' : mode === 'v1' ? 'v1' : 'legacy');
  if (!['legacy', 'v1', 'rebuild'].includes(profile)) throw new Error('VITE_API_PROFILE must be legacy, v1 or rebuild');
  return {
  define: { 'import.meta.env.VITE_API_PROFILE': JSON.stringify(profile) },
  plugins: [{ name: 'cc-frontend-profile', generateBundle() { this.emitFile({ type: 'asset', fileName: 'cc-frontend.json', source: JSON.stringify({ contract: 'cc-web-v1', profile }) }); } }],
  server: { proxy },
  preview: { proxy },
  };
});
