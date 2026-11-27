/**
 * The capture permission card's DOM: one card per held answer (held.ts), with
 * a row per refused request (shot, reason glyph, the clicked text or METHOD
 * url, deny and allow), a login row per origin and a busy row. Chrome is
 * icon-only; only data shows as text. Buttons carry `data-act`; the controller
 * (editorCard.ts) handles the clicks and the in-flight state given here.
 */
import type { CaptureRequest, Held } from './api'
import { label } from './i18n'
import { icon, iconButton } from './icons'

/** What is in flight: request ids, `retry <hid>` and `<hid> <origin>` login keys; details kuhy expanded. */
export interface CardState {
  readonly working: ReadonlySet<string>
  readonly waiting: ReadonlySet<string>
  readonly opened: ReadonlySet<string>
}

/** Reason -> [glyph, tip key] (capture/__init__.py <request>.reason). */
export const REASONS: Readonly<Partial<Record<string, readonly [string, string]>>> = {
  'deny-list': ['ban', 'reasonDenyList'],
  submit: ['post', 'reasonSubmit'],
  input: ['keys', 'reasonInput'],
  request: ['swap', 'reasonRequest'],
  'form-submit': ['form', 'reasonForm'],
}
const OTHER = ['swap', 'reasonRequest'] as const

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

function el(tag: string, cls: string): HTMLElement {
  const e = document.createElement(tag)
  e.className = cls
  return e
}

function button(name: string, key: string, cls: string, act: string, disabled = false): HTMLButtonElement {
  const b = iconButton(name, key, cls)
  b.dataset.act = act
  b.disabled = disabled
  b.setAttribute('aria-label', label(key))
  return b
}

/** A glyph with a tip but no action (reason, busy): same bubble as the buttons. */
function glyph(name: string, key: string): HTMLElement {
  const g = el('span', 'icon-btn reason')
  g.dataset.label = key
  g.setAttribute('role', 'img')
  g.setAttribute('aria-label', label(key))
  g.innerHTML = icon(name)
  return g
}

/** "POST https://..." -> the method in bold, then the url; any other detail whole. */
export function detailNodes(detail: string): Node[] {
  const m = /^([A-Z]+) (\S.*)$/.exec(detail)
  if (!m) return [document.createTextNode(detail)]
  const method = el('b', 'method')
  method.textContent = m[1]
  return [method, document.createTextNode(` ${m[2]}`)]
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
  const row = el('article', busy ? 'req working' : 'req')
  row.dataset.rid = r.id
  const shot = button('expand', 'enlarge', 'shot', 'zoom')
  const img = document.createElement('img')
  img.src = r.shot
  img.alt = ''
  shot.replaceChildren(img, Object.assign(el('span', 'shot-zoom'), { innerHTML: icon('expand') }))
  const [name, key] = REASONS[r.reason] ?? OTHER
  const detail = el('button', s.opened.has(r.id) ? 'detail open' : 'detail')
  Object.assign(detail, { type: 'button', dir: 'auto' })
  detail.dataset.act = 'detail'
  const txt = el('span', 'txt')
  txt.append(...detailNodes(r.detail))
  detail.append(txt)
  const info = el('div', 'req-info')
  info.append(glyph(name, key), detail)
  const body = el('div', 'req-body')
  body.append(info, decisions(r.id, busy, 'req-actions'))
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
  row.append(b, where)
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

/** The large view of one shot, with its own deny and allow. */
export function shotView(r: CaptureRequest, s: CardState): HTMLElement {
  const box = el('div', 'shot-box')
  box.dataset.rid = r.id
  const img = document.createElement('img')
  img.src = r.shot
  img.alt = ''
  box.append(img, decisions(r.id, s.working.has(r.id), 'shot-bar'), button('x', 'close', 'shot-close', 'close'))
  return box
}
