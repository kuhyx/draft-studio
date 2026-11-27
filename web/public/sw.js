// Service worker: the editor opens with the server down (offline-first; unsaved edits wait in
// IndexedDB, web/src/lib/unsaved.ts). The server stamps the build id into this file (files.py), so a
// new build is a new worker: it takes over at once and the pages reload (web/src/lib/worker.ts).
// Pages and static files go network first, so an online tab always runs the newest build; the cache
// answers only when the network does not. Nothing under /api/ is ever cached.
const BUILD = '__BUILD__'
const CACHE = `ds-${BUILD}`
const PAGES = ['/', '/d/']

const keyOf = (url) => {
  const u = new URL(url, self.location.origin)
  return u.origin + u.pathname
}
const cacheable = (u) =>
  u.origin === self.location.origin && !u.pathname.startsWith('/api/') && u.pathname !== '/version' && u.pathname !== '/sw.js'

/** Cache `url`, then what it names: files an HTML page or a script imports (`/_astro/x.js`, `./y.js`). */
async function walk(cache, url, seen) {
  const key = keyOf(url)
  if (seen.has(key)) return
  seen.add(key)
  const resp = await fetch(key, { cache: 'reload' })
  if (!resp.ok) throw new Error(`${resp.status} ${key}`)
  await cache.put(key, resp.clone())
  if (!/html|javascript/.test(resp.headers.get('content-type') ?? '')) return
  const refs = [...(await resp.text()).matchAll(/["'(]((?:\/_astro\/|\.\/)[\w.-]+\.(?:js|css))["')]/g)]
  await Promise.all(refs.map((m) => walk(cache, new URL(m[1], key).href, seen)))
}

async function precache() {
  const cache = await caches.open(CACHE)
  const seen = new Set()
  await Promise.all(PAGES.map((p) => walk(cache, p, seen)))
  const { locales } = await (await fetch('/api/locales')).json()
  await Promise.all(locales.map((tag) => walk(cache, `/locales/${tag}.json`, seen)))
}

self.addEventListener('install', (event) => {
  event.waitUntil(precache().then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) => Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.method !== 'GET' || !cacheable(new URL(req.url))) return
  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE)
      try {
        const resp = await fetch(req)
        if (resp.ok) event.waitUntil(cache.put(keyOf(req.url), resp.clone()))
        return resp
      } catch (err) {
        const hit = await cache.match(keyOf(req.url))
        if (hit) return hit
        throw err
      }
    })(),
  )
})
