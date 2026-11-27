/// <reference types="vitest/config" />
import { getViteConfig } from 'astro/config'

export default getViteConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      // DOM controllers (pages, view, boot, tooltip) are covered by the headless E2E flow.
      include: ['src/lib/{api,approve,authorship,diff,editorState,i18n,icons,images,status,tipPlace}.ts'],
      exclude: ['src/**/*.test.ts', 'src/**/*.d.ts'],
      thresholds: { statements: 100, branches: 100, functions: 100, lines: 100 },
    },
  },
})
