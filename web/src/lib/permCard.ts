/**
 * The capture permission card's DOM: one card per held answer (held.ts), with
 * a row per refused request (shot, then one decision row per blocked METHOD url
 * with deny-rest/allow-rest/send, grantRows.ts; a click refusal: reason glyph,
 * the clicked text, deny and allow), a login row per origin and a busy row. Chrome is
 * icon-only; only data shows as text. Buttons carry `data-act`; the controller
 * (editorCard.ts) handles the clicks and the in-flight state given here.
 */
import type { CaptureRequest, Held } from './api'
import { button, detailButton, el, glyph, reasonOf } from './cardDom'
import { grantRows, type Pick } from './grantPick'
import { grantBody } from './grantRows'
import { icon } from './icons'

export { detailNodes, REASONS } from './cardDom'

/** What is in flight: request ids, `retry <hid>` and `<hid> <origin>` login keys; details kuhy expanded. */
export interface CardState {
  readonly working: ReadonlySet<string>
  readonly waiting: ReadonlySet<string>
  readonly opened: ReadonlySet<string>
  /** Per-request grant: each request's row decisions (grantPick.ts). */
  readonly picks?: ReadonlyMap<string, readonly Pick[]>
}

/** A card and its span share a colour; the first hold gets the accent. */
export const HUES = ['var(--accent)', 'var(--cat-2)', 'var(--cat-4)', 'var(--cat-5)', 'var(--cat-1)', 'var(--cat-6)']

export function nextHue(used: readonly number[]): number {
  let i = 0
  while (used.includes(i)) i += 1
  return i
}

export const loginKey = (hid: string, origin: string): string => `${hid} ${origin}`
export const retryKey = (hid: string): string => `retry ${hid}`

/** What a hold waits on: changes when a login, a retry or a grant moved it. */
export const signature = (h: Held): string =>
  JSON.stringify([h.requests.map((r) => r.id), h.login, h.busy, h.tries, h.failed])

export function host(origin: string): string {
  try {
    return new URL(origin).host
  } catch {
    return origin
  }
}

function decisions(rid: string, busy: boolean, cls: string): HTMLElement {
  const bar = el('div', cls)
  bar.dataset.rid = rid
  bar.append(
    button('x', 'deny', 'deny', 'deny', busy),
    button(busy ? 'spinner' : 'check', 'allow', 'grant', 'grant', busy),
  )
  return bar
}

function requestRow(r: CaptureRequest, s: CardState): HTMLElement {
  const busy = s.working.has(r.id)
  const rows = grantRows(r)
  // Sent rows lock (send spins); a click refusal's single allow spins as before.
  const row = el('article', busy ? `req ${rows.length > 0 ? 'locked' : 'working'}` : 'req')
  row.dataset.rid = r.id
  const shot = button('expand', 'enlarge', 'shot', 'zoom')
  const img = document.createElement('img')
  img.src = r.shot
  img.alt = ''
  shot.replaceChildren(img, Object.assign(el('span', 'shot-zoom'), { innerHTML: icon('expand') }))
  const body = el('div', 'req-body')
  if (rows.length > 0) {
    body.append(...grantBody(r, rows, { picks: s.picks?.get(r.id) ?? [], sent: busy, opened: s.opened }))
  } else {
    const [name, key] = reasonOf(r.reason)
    const info = el('div', 'req-info')
    info.append(glyph(name, key), detailButton(r.detail, s.opened.has(r.id)))
    body.append(info, decisions(r.id, busy, 'req-actions'))
  }
  row.append(shot, body)
  return row
}

function loginRow(hid: string, origin: string, s: CardState): HTMLElement {
  const wait = s.waiting.has(loginKey(hid, origin))
  const row = el('div', wait ? 'login waiting' : 'login')
  row.dataset.key = loginKey(hid, origin)
  const b = button(wait ? 'spinner' : 'login', 'login', '', 'login', wait)
  b.dataset.origin = origin
  const where = el('span', 'origin')
  where.dir = 'ltr'
  where.textContent = host(origin)
  // Retry with no login window (already logged in, the cookies came late): the hold's retry route.
  const again = s.working.has(retryKey(hid))
  row.append(b, where, button(again ? 'spinner' : 'retry', 'retry', '', 'retry', again))
  return row
}

function busyRow(hid: string, s: CardState): HTMLElement {
  const busy = s.working.has(retryKey(hid))
  const row = el('div', busy ? 'busy working' : 'busy')
  row.append(glyph('hourglass', 'heldBusy'), button(busy ? 'spinner' : 'retry', 'retry', '', 'retry', busy))
  return row
}

/** A proposal none of whose screenshots was taken: one row per shot (key, reason), then retry and dismiss. */
function failedRows(h: Held, s: CardState): HTMLElement[] {
  const busy = s.working.has(retryKey(h.id))
  const rows = h.failed.map((f) => {
    const row = el('div', 'failed')
    row.dataset.key = f.key
    const what = el('span', 'origin')
    what.dir = 'ltr'
    what.textContent = `${f.key} ${f.reason}`
    row.append(glyph('imageOff', 'missingImage'), what)
    return row
  })
  const bar = el('div', busy ? 'busy failed-bar working' : 'busy failed-bar')
  bar.append(button('x', 'close', '', 'dismiss'), button(busy ? 'spinner' : 'retry', 'retry', '', 'retry', busy))
  return [...rows, bar]
}

export function heldCard(h: Held, hue: string, s: CardState): HTMLElement {
  const card = el('section', 'held')
  card.dataset.held = h.id
  card.style.setProperty('--hc', hue)
  card.append(...h.requests.map((r) => requestRow(r, s)), ...h.login.map((o) => loginRow(h.id, o, s)))
  if (h.busy) card.append(busyRow(h.id, s))
  if (h.failed.length > 0) card.append(...failedRows(h, s))
  return card
}

/** The large view of one shot, with its own deny and allow for a click refusal (no rows). */
export function shotView(r: CaptureRequest, s: CardState): HTMLElement {
  const box = el('div', 'shot-box')
  box.dataset.rid = r.id
  const img = document.createElement('img')
  img.src = r.shot
  img.alt = ''
  // A card with rows is decided on its rows only: a whole-card allow here would undo a row's deny.
  if (grantRows(r).length > 0) box.append(img, button('x', 'close', 'shot-close', 'close'))
  else box.append(img, decisions(r.id, s.working.has(r.id), 'shot-bar'), button('x', 'close', 'shot-close', 'close'))
  return box
}
