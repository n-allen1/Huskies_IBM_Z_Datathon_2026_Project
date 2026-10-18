import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  // Default to the Prism mock; point at the FastAPI backend with VITE_API_TARGET=http://localhost:8000
  const target = env.VITE_API_TARGET || 'http://localhost:4010';
  return {
    plugins: [sveltekit()],
    server: { proxy: { '/api': { target, changeOrigin: true } } },
    test: { include: ['src/**/*.test.ts', 'mock/**/*.test.mjs'] }
  };
});
