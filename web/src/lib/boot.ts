/** Shared page boot: locale catalog, tooltips, the language-cycle button, build-id reload. */
import { api } from './api'
import { installTips } from './tooltip'
import { registerWorker } from './worker'
import { applyCatalog, loadLocale, nextLocale, parseCatalog, pickLocale, storedLocale, storeLocale } from './i18n'

export async function initLocale(doc: Document, onApplied: () => void = () => undefined): Promise<void> {
  const tips = installTips(doc)
  // Server unreachable (unsaved edits open offline, unsaved.ts): the page opens with the labels' keys until a reload.
  const available = await api.locales().catch(() => [])
  let tag = pickLocale(available, storedLocale(localStorage), navigator.languages)
  applyCatalog(doc, tag, await loadLocale(tag).catch(() => parseCatalog({ labels: {}, errors: {} })))
  onApplied()
  doc.querySelector('#ui-locale')?.addEventListener('click', () => {
    void (async () => {
      tag = nextLocale(await api.locales(), tag)
      storeLocale(localStorage, tag)
      applyCatalog(doc, tag, await loadLocale(tag))
      tips.refresh()
      onApplied()
    })()
  })
}

/** Reload when the server serves a new build (R2: pages never run stale code). */
export function watchBuild(doc: Document, beforeReload: () => Promise<void> = () => Promise.resolve()): void {
  const mine = doc.documentElement.dataset.build ?? ''
  registerWorker(navigator.serviceWorker, async () => {
    await beforeReload()
    location.reload()
  })
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
