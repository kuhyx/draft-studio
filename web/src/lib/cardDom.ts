/** DOM helpers shared by the permission card (permCard.ts) and its per-request grant rows (grantRows.ts). */
import { label } from './i18n'
import { icon, iconButton } from './icons'

/** Reason -> [glyph, tip key] (capture/__init__.py <request>.reason). */
export const REASONS: Readonly<Partial<Record<string, readonly [string, string]>>> = {
  'deny-list': ['ban', 'reasonDenyList'],
  submit: ['post', 'reasonSubmit'],
  input: ['keys', 'reasonInput'],
  request: ['swap', 'reasonRequest'],
  'form-submit': ['form', 'reasonForm'],
}
const OTHER = ['swap', 'reasonRequest'] as const

export const reasonOf = (reason: string): readonly [string, string] => REASONS[reason] ?? OTHER

export function el(tag: string, cls: string): HTMLElement {
  const e = document.createElement(tag)
  e.className = cls
  return e
}

export function button(name: string, key: string, cls: string, act: string, disabled = false): HTMLButtonElement {
  const b = iconButton(name, key, cls)
  b.dataset.act = act
  b.disabled = disabled
  b.setAttribute('aria-label', label(key))
  return b
}

/** A glyph with a tip but no action (reason, busy): same bubble as the buttons. */
export function glyph(name: string, key: string, cls = ''): HTMLElement {
  const g = el('span', `icon-btn reason ${cls}`.trim())
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

/** The clamped detail line (3 lines, 2 on a phone); a click opens it whole. */
export function detailButton(text: string, open: boolean): HTMLElement {
  const detail = el('button', open ? 'detail open' : 'detail')
  Object.assign(detail, { type: 'button', dir: 'auto' })
  detail.dataset.act = 'detail'
  const txt = el('span', 'txt')
  txt.append(...detailNodes(text))
  detail.append(txt)
  return detail
}
