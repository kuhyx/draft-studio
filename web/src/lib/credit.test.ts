import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'

const SRC = resolve(fileURLToPath(import.meta.url), '../..')

describe('kuhy-credit in source Astro files', () => {
  it('pages/index.astro contains kuhy-credit element', () => {
    const content = readFileSync(resolve(SRC, 'pages/index.astro'), 'utf-8')
    expect(content).toMatch(/<kuhy-credit[^>]*><\/kuhy-credit>\s*<\/header>/)
  })

  it('pages/d/index.astro contains kuhy-credit element', () => {
    const content = readFileSync(resolve(SRC, 'pages/d/index.astro'), 'utf-8')
    expect(content).toMatch(/<kuhy-credit[^>]*><\/kuhy-credit>\s*<\/header>/)
  })

  it('layouts/Base.astro imports @kuhyx/kuhy-credit', () => {
    const content = readFileSync(resolve(SRC, 'layouts/Base.astro'), 'utf-8')
    expect(content).toContain("import '@kuhyx/kuhy-credit'")
  })
})
