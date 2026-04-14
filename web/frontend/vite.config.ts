import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    // iframe 임베딩 허용
    headers: {
      'X-Frame-Options': 'ALLOWALL',
      'Content-Security-Policy': '',
    },
    proxy: {
      '/api': 'http://localhost:8088',
      '/ws': {
        target: 'ws://localhost:8088',
        ws: true,
      },
    },
  },
})
