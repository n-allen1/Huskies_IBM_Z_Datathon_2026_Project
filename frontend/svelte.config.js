import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

export default {
  preprocess: vitePreprocess(),
  kit: {
    // Static SPA: no server-side JavaScript needed for a private ops dashboard.
    adapter: adapter({ fallback: 'index.html' })
  }
};
