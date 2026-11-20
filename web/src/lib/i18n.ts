/**
 * Locale catalogs: screen-reader labels and error messages only (the visible
 * chrome has no text). One JSON file per language in /locales/; adding a
 * language is dropping in one file. A catalog may set "dir": "rtl".
 */
import { z } from 'zod'

const catalogSchema = z.object({
  dir: z.enum(['ltr', 'rtl']).default('ltr'),
  labels: z.partialRecord(z.string(), z.string()),
  errors: z.partialRecord(z.string(), z.string()),
})
export type Catalog = z.infer<typeof catalogSchema>

const STORAGE_KEY = 'draft-studio.ui-locale'
let current: Catalog = { dir: 'ltr', labels: {}, errors: {} }

export function pickLocale(available: readonly string[], stored: string | null, nav: readonly string[]): string {
  if (stored && available.includes(stored)) return stored
  for (const tag of nav) {
    const base = tag.toLowerCase().replace(/-.*/, '')
    if (available.includes(base)) return base
  }
  return available.includes('en') ? 'en' : (available[0] ?? 'en')
}

export function nextLocale(available: readonly string[], now: string): string {
  const i = available.indexOf(now)
  return available[(i + 1) % available.length] ?? now
}

export function parseCatalog(data: unknown): Catalog {
  return catalogSchema.parse(data)
}

export function label(key: string): string {
  return current.labels[key] ?? key
}

export function errorText(code: string): string {
  return current.errors[code] ?? current.errors.server ?? code
}

/** Apply a catalog: aria-labels on [data-label], lang + dir on <html>. */
export function applyCatalog(root: Document, tag: string, catalog: Catalog): void {
  current = catalog
  root.documentElement.lang = tag
  root.documentElement.dir = catalog.dir
  for (const el of root.querySelectorAll<HTMLElement>('[data-label]')) {
    el.setAttribute('aria-label', label(String(el.dataset.label)))
  }
}

export async function loadLocale(tag: string): Promise<Catalog> {
  const resp = await fetch(`/locales/${encodeURIComponent(tag)}.json`)
  return parseCatalog(await resp.json())
}

export function storedLocale(storage: Storage): string | null {
  return storage.getItem(STORAGE_KEY)
}

export function storeLocale(storage: Storage, tag: string): void {
  storage.setItem(STORAGE_KEY, tag)
}
