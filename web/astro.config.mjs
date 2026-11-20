import { defineConfig } from 'astro/config'

// Static pages served by draft_studio.server on 127.0.0.1. `/` lists drafts,
// `/d/?id=<id>` is the editor. In dev, `/api` and `/locales` go to the
// Python server (DRAFT_STUDIO_PORT, default 8794).
const port = process.env.DRAFT_STUDIO_PORT ?? '8794'
const target = `http://127.0.0.1:${port}`

export default defineConfig({
  output: 'static',
  build: { format: 'directory' },
  server: { host: '127.0.0.1' },
  vite: {
    server: {
      proxy: {
        '/api': { target, changeOrigin: true },
        '/locales': { target, changeOrigin: true },
        '/version': { target, changeOrigin: true },
      },
    },
  },
})
