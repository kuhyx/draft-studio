/**
 * The service worker (web/public/sw.js) keeps the editor's pages and static files for a start with
 * the server down. A new build is a new worker file: it takes over at once, and a page that was
 * already controlled saves what it holds and reloads, so a deploy is never served stale.
 */
export function registerWorker(
  container: ServiceWorkerContainer | undefined,
  reload: () => Promise<void>,
  onError: (err: unknown) => void = (err) => {
    console.error('service worker not registered', err)
  },
): void {
  if (!container) return
  const controlled = container.controller !== null
  container.addEventListener('controllerchange', () => {
    if (controlled) void reload()
  })
  container.register('/sw.js').catch(onError)
}
