import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  applyCatalog,
  errorText,
  label,
  loadLocale,
  nextLocale,
  parseCatalog,
  pickLocale,
  storedLocale,
  storeLocale,
  tip,
} from './i18n'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('locale catalogs', () => {
  it('picks stored, then browser, then en, then first', () => {
    expect(pickLocale(['en', 'pl'], 'pl', ['en-US'])).toBe('pl')
    expect(pickLocale(['en', 'pl'], 'zz', ['pl-PL', 'en'])).toBe('pl')
    expect(pickLocale(['en', 'pl'], null, ['de'])).toBe('en')
    expect(pickLocale(['pl', 'de'], null, [])).toBe('pl')
    expect(pickLocale([], null, [])).toBe('en')
  })

  it('cycles through available locales', () => {
    expect(nextLocale(['en', 'pl', 'xx'], 'pl')).toBe('xx')
    expect(nextLocale(['en', 'pl'], 'pl')).toBe('en')
    expect(nextLocale(['en'], 'gone')).toBe('en')
    expect(nextLocale([], 'en')).toBe('en')
  })

  it('applies labels, lang and dir; falls back to keys and the server error', () => {
    document.body.innerHTML = '<button data-label="undo"></button><button data-label=""></button>'
    applyCatalog(document, 'xx', parseCatalog({ dir: 'rtl', labels: { undo: 'U' }, errors: { server: 'S' } }))
    expect(document.documentElement.dir).toBe('rtl')
    expect(document.documentElement.lang).toBe('xx')
    expect(document.querySelector('button')?.getAttribute('aria-label')).toBe('U')
    expect(label('missing')).toBe('missing')
    expect(errorText('claude')).toBe('S')
    applyCatalog(document, 'en', parseCatalog({ labels: {}, errors: {} }))
    expect(document.documentElement.dir).toBe('ltr')
    expect(errorText('claude')).toBe('claude')
  })

  it('rejects a broken catalog', () => {
    expect(() => parseCatalog({ dir: 'up' })).toThrow()
  })

  it('loads a catalog over fetch and stores the choice', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{"labels":{},"errors":{}}'))))
    expect((await loadLocale('pl')).dir).toBe('ltr')
    storeLocale(localStorage, 'pl')
    expect(storedLocale(localStorage)).toBe('pl')
  })
})

describe('tooltip text', () => {
  it('reads short tips, falls back to the label, then the key', () => {
    applyCatalog(document, 'en', parseCatalog({ labels: { undo: 'Undo it', redo: 'Redo it' }, errors: {}, tips: { undo: 'Undo' } }))
    expect(tip('undo')).toBe('Undo')
    expect(tip('redo')).toBe('Redo it')
    expect(tip('nope')).toBe('nope')
    expect(parseCatalog({ labels: {}, errors: {} }).tips).toEqual({})
  })
})
