import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vite';

// base './': the Android app loads the build from the APK (Capacitor), and
// the same build can be served as a mobile web page.
export default defineConfig({
  base: './',
  plugins: [vue()],
  server: {
    port: 5677,
    proxy: { '/api': { changeOrigin: true, target: process.env.MINERVA_API ?? 'http://127.0.0.1:8000' } },
  },
  build: { chunkSizeWarningLimit: 1200 },
});
