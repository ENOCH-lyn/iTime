import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      dts: 'src/auto-imports.d.ts',
      resolvers: [ElementPlusResolver()],
    }),
    Components({
      dts: 'src/components.d.ts',
      resolvers: [ElementPlusResolver()],
    }),
  ],
  server: {
    strictPort: true,
    proxy: { '/api': { target: process.env.ITIME_API_ORIGIN ?? 'http://127.0.0.1:8000' } },
  },
  preview: {
    strictPort: true,
    proxy: { '/api': { target: process.env.ITIME_API_ORIGIN ?? 'http://127.0.0.1:8000' } },
  },
})
