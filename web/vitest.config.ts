/// <reference types="vitest/config" />
import { getViteConfig } from 'astro/config'

export default getViteConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      // DOM controllers (pages, view, boot, tooltip) are covered by the headless E2E flow.
      include: ['src/lib/{api,approve,authorship,copyText,diff,editorCard,editorState,feed,held,heldSpans,i18n,icons,images,permCard,signals,status,sync,tipPlace,unsaved,whatsapp,worker}.ts'],
      exclude: ['src/**/*.test.ts', 'src/**/*.d.ts'],
    },
  },
})
