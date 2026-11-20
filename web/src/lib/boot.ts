/** Shared page boot: locale catalog, the language-cycle button, build-id reload. */
import { api } from './api'
import { applyCatalog, loadLocale, nextLocale, pickLocale, storedLocale, storeLocale } from './i18n'

export async function initLocale(doc: Document, onApplied: () => void = () => undefined): Promise<void> {
  const available = await api.locales()
  let tag = pickLocale(available, storedLocale(localStorage), navigator.languages)
  applyCatalog(doc, tag, await loadLocale(tag))
  onApplied()
  doc.querySelector('#ui-locale')?.addEventListener('click', () => {
    void (async () => {
      tag = nextLocale(await api.locales(), tag)
      storeLocale(localStorage, tag)
      applyCatalog(doc, tag, await loadLocale(tag))
      onApplied()
    })()
  })
}

/** Reload when the server serves a new build (R2: pages never run stale code). */
export function watchBuild(doc: Document, beforeReload: () => Promise<void> = () => Promise.resolve()): void {
  const mine = doc.documentElement.dataset.build ?? ''
  setInterval(() => {
    void api
      .version()
      .then(async (build) => {
        if (build !== mine) {
          await beforeReload()
          location.reload()
        }
      })
      .catch(() => undefined)
  }, 5000)
}
