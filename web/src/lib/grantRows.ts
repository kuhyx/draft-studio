/**
 * The per-request rows of a permission card (grantPick.ts holds the rules): a row per blocked
 * (method, url) with deny (x) and allow (check); the pressed one fills red/green and tints the row.
 * Footer: deny-rest and allow-rest (only the undecided rows), one summary dot per row (amber, green,
 * red), send (off until every row is decided). Once sent every button locks and send spins.
 */
import type { CaptureRequest } from './api'
import { button, detailButton, el, glyph, reasonOf } from './cardDom'
import { decided, type GrantRow, type Pick } from './grantPick'

export interface RowsState {
  readonly picks: readonly Pick[]
  readonly sent: boolean
  /** Keys of the rows whose url kuhy opened (`<rid> <i>`). */
  readonly opened: ReadonlySet<string>
}

export const rowKey = (rid: string, i: number): string => `${rid} ${String(i)}`
const cls = (p: Pick): string => p ?? 'u'

function side(name: string, key: string, colour: string, act: string, mine: boolean, other: boolean, sent: boolean) {
  const b = button(name, key, other ? `${colour} dim` : colour, act, sent)
  b.setAttribute('aria-pressed', String(mine))
  return b
}

function pairRow(r: CaptureRequest, row: GrantRow, i: number, s: RowsState): HTMLElement {
  const p = s.picks[i]
  const box = el('div', `pair ${cls(p)}`)
  box.dataset.i = String(i)
  const lead = el('span', 'pair-lead')
  const [name, key] = reasonOf(r.reason)
  lead.append(glyph(name, key))
  if (row.preflight) lead.append(glyph('preflight', 'preflight', 'preflight'))
  const act = el('div', 'pair-act')
  act.append(
    side('x', 'deny', 'deny', 'pick-deny', p === 'd', p === 'a', s.sent),
    side('check', 'allow', 'grant', 'pick-allow', p === 'a', p === 'd', s.sent),
  )
  box.append(lead, detailButton(`${row.method} ${row.url}`, s.opened.has(rowKey(r.id, i))), act)
  return box
}

/** The rows and the footer of one request. */
export function grantBody(r: CaptureRequest, rows: readonly GrantRow[], s: RowsState): HTMLElement[] {
  const pairs = el('div', 'pairs')
  pairs.append(...rows.map((row, i) => pairRow(r, row, i, s)))
  const bar = el('div', 'req-actions grant-bar')
  bar.dataset.rid = r.id
  const all = decided(s.picks, rows.length)
  const dots = el('span', 'dots')
  dots.setAttribute('aria-hidden', 'true')
  dots.append(...rows.map((_, i) => el('i', cls(s.picks[i]))))
  bar.append(
    button('xAll', 'denyRest', 'deny', 'deny-rest', all || s.sent),
    button('checkAll', 'allowRest', 'grant', 'allow-rest', all || s.sent),
    dots,
    el('span', 'grow'),
    button(s.sent ? 'spinner' : 'send', 'sendGrant', 'send', 'send', !all || s.sent),
  )
  return [pairs, bar]
}
