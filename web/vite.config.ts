import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    strictPort: true,
    proxy: { '/api': { target: process.env.ITIME_API_ORIGIN ?? 'http://127.0.0.1:8000' } },
  },
  preview: {
    strictPort: true,
    proxy: { '/api': { target: process.env.ITIME_API_ORIGIN ?? 'http://127.0.0.1:8000' } },
  },
})
