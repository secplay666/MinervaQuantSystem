import { defineConfig } from '@vben/vite-config';

// 开发时：vite (5666) -> 本机后端 quant-app serve --no-tls --port 8000
export default defineConfig(async () => {
  return {
    application: {},
    vite: {
      server: {
        proxy: {
          '/api': {
            changeOrigin: true,
            target: process.env.MINERVA_API ?? 'http://127.0.0.1:8000',
            ws: true,
          },
        },
      },
    },
  };
});
