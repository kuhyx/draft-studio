import type { EditorView } from '@codemirror/view'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { applyCatalog, parseCatalog } from './i18n'
import { findImageRefs, ImageWidget } from './images'

const view = () => ({ requestMeasure: vi.fn() })

afterEach(() => {
  applyCatalog(document, 'en', parseCatalog({ labels: {}, errors: {} }))
})

/** The marker's aria-label, after checking it is the glyph-only frame and the only child. */
function markerLabel(dom: HTMLElement): string | null | undefined {
  const marker = dom.querySelector('.cm-image-broken')
  expect(dom.querySelector('img')).toBeNull()
  expect(marker?.getAttribute('role')).toBe('img')
  expect(marker?.querySelector('svg')).not.toBeNull()
  expect(dom.textContent).toBe('')
  expect(dom.dataset.broken).toBe('1')
  return marker?.getAttribute('aria-label')
}

describe('missing images', () => {
  it('marks references to names the server can never store as missing', () => {
    const text = '![](assets/../draft.md)\n![a](assets/sub/x.png)\n![](assets/TODO cyberfolks.png)\n![](assets/ok-1.png)'
    expect(findImageRefs(text)).toEqual([
      { alt: '', name: '../draft.md', lineEnd: 23, missing: true },
      { alt: 'a', name: 'sub/x.png', lineEnd: 46, missing: true },
      { alt: '', name: 'TODO cyberfolks.png', lineEnd: 78, missing: true },
      { alt: '', name: 'ok-1.png', lineEnd: 99, missing: false },
    ])
  })

  it('draws the marker at once, without fetching anything, for a name that cannot exist', () => {
    applyCatalog(document, 'en', parseCatalog({ labels: {}, errors: {}, tips: { missingImage: 'Missing' } }))
    const v = view()
    expect(markerLabel(new ImageWidget('/x', 'lost', true).toDOM(v as unknown as EditorView))).toBe('Missing')
  })

  it('keeps the picture hidden until it loads, and swaps a failed load for the marker', () => {
    const v = view()
    const dom = new ImageWidget('/api/drafts/d/assets/a.png', 'a cat').toDOM(v as unknown as EditorView)
    const img = dom.querySelector('img')
    expect(img?.hidden).toBe(true)
    img?.dispatchEvent(new Event('load'))
    expect(img?.hidden).toBe(false)
    const gone = new ImageWidget('/api/drafts/d/assets/gone.png', 'gone').toDOM(v as unknown as EditorView)
    gone.querySelector('img')?.dispatchEvent(new Event('error'))
    expect(markerLabel(gone)).toBe('missingImage')
    expect(v.requestMeasure).toHaveBeenCalledTimes(2)
  })

  it('is not equal to the same picture once it is known missing', () => {
    expect(new ImageWidget('/a', 'x', true).eq(new ImageWidget('/a', 'x'))).toBe(false)
    expect(new ImageWidget('/a', 'x', true).eq(new ImageWidget('/a', 'x', true))).toBe(true)
  })
})
