import { defineConfig } from 'vite';

const target = process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000';
const proxy = { '/api': { target, changeOrigin: false, xfwd: false, ws: false } };
export default defineConfig({
  server: { host: '127.0.0.1', proxy },
  preview: { host: '127.0.0.1', proxy },
});
