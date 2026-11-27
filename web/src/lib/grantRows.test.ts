import { describe, expect, it } from 'vitest'
import type { CaptureRequest, Held } from './api'
import type { Pick } from './grantPick'
import { heldCard, HUES, shotView, type CardState } from './permCard'

const A = 'https://api.example/impersonate'
const B = 'https://chat.example/messages'
const C = 'https://ga.example/collect'
const req = (over: Partial<CaptureRequest> = {}): CaptureRequest => ({
  id: 'r1',
  reason: 'request',
  detail: `POST ${A}`,
  shot: '/s/r1',
  blocked: [
    ['POST', A],
    ['POST', B],
    ['OPTIONS', C],
  ],
  ...over,
})
const hold = (r: CaptureRequest): Held => ({
  id: 'h1',
  kind: 'proposal',
  from: 0,
  to: 4,
  requests: [r],
  login: [],
  busy: false,
  tries: 0,
  failed: [],
})
const state = (picks: readonly Pick[] = [], over: Partial<CardState> = {}): CardState => ({
  working: new Set(),
  waiting: new Set(),
  opened: new Set(),
  picks: new Map([['r1', picks]]),
  ...over,
})
const paint = (picks: readonly Pick[] = [], over: Partial<CardState> = {}, r = req()) =>
  heldCard(hold(r), HUES[0], state(picks, over))
const acts = (el: Element, sel: string) => [...el.querySelectorAll<HTMLElement>(sel)].map((b) => b.dataset.act)
const btn = (el: Element, sel: string) => el.querySelector<HTMLButtonElement>(sel)

describe('per-request grant rows', () => {
  it('one row per blocked pair: glyph, METHOD url, deny then allow; footer: deny-rest, allow-rest, dots, send', () => {
    const card = paint()
    const rows = [...card.querySelectorAll<HTMLElement>('.pair')]
    expect(rows.map((r) => [r.dataset.i, r.querySelector('.detail')?.textContent])).toEqual([
      ['0', `POST ${A}`],
      ['1', `POST ${B}`],
      ['2', `OPTIONS ${C}`],
    ])
    expect(rows.map((r) => acts(r, '[data-act]'))).toEqual(Array(3).fill(['detail', 'pick-deny', 'pick-allow']))
    expect(acts(card, '.req-actions [data-act]')).toEqual(['deny-rest', 'allow-rest', 'send'])
    expect([...card.querySelectorAll('.dots i')].map((i) => i.className)).toEqual(['u', 'u', 'u'])
    expect(card.querySelector('.req-info')).toBeNull()
    expect(card.textContent).not.toMatch(/undefined|NaN|object Object/)
  })

  it('send is disabled until every row is decided; the decided button is pressed and the row tinted', () => {
    expect(btn(paint(), '[data-act=send]')?.disabled).toBe(true)
    const half = paint(['a', 'd'])
    expect(btn(half, '[data-act=send]')?.disabled).toBe(true)
    expect([...half.querySelectorAll('.pair')].map((r) => r.classList.contains('a') || r.classList.contains('d') ? r.className : 'pair u')).toEqual([
      'pair a',
      'pair d',
      'pair u',
    ])
    const allow = btn(half, '.pair[data-i="0"] [data-act=pick-allow]')
    const deny = btn(half, '.pair[data-i="0"] [data-act=pick-deny]')
    expect([allow?.getAttribute('aria-pressed'), deny?.getAttribute('aria-pressed')]).toEqual(['true', 'false'])
    expect([...half.querySelectorAll('.dots i')].map((i) => i.className)).toEqual(['a', 'd', 'u'])
    expect([btn(half, '[data-act=deny-rest]')?.disabled, btn(half, '[data-act=allow-rest]')?.disabled]).toEqual([false, false])
    const full = paint(['a', 'd', 'd'])
    expect(btn(full, '[data-act=send]')?.disabled).toBe(false)
    expect([btn(full, '[data-act=deny-rest]')?.disabled, btn(full, '[data-act=allow-rest]')?.disabled]).toEqual([true, true])
  })

  it('sent: every button locked, the send glyph spins, the decisions stay visible', () => {
    const card = paint(['a', 'd', 'd'], { working: new Set(['r1']) })
    expect(card.querySelector('.req')?.classList.contains('locked')).toBe(true)
    expect([...card.querySelectorAll<HTMLButtonElement>('.pair-act button, .req-actions button')].every((b) => b.disabled)).toBe(true)
    expect(btn(card, '[data-act=send]')?.innerHTML).toContain('M12 3a9')
    expect(btn(card, '.pair[data-i="0"] [data-act=pick-allow]')?.getAttribute('aria-pressed')).toBe('true')
  })

  it('a long url clamps until clicked; opened is per row', () => {
    const card = paint([], { opened: new Set(['r1 1']) })
    expect([...card.querySelectorAll('.pair .detail')].map((d) => d.classList.contains('open'))).toEqual([false, true, false])
  })

  it('a preflight row shows the announced method and a preflight mark', () => {
    const card = paint([], {}, req({ announces: [null, null, 'POST'] }))
    const row = card.querySelector('.pair[data-i="2"]')
    expect(row?.querySelector('.detail')?.textContent).toBe(`POST ${C}`)
    const mark = row?.querySelector<HTMLElement>('.preflight')
    expect([mark?.dataset.label, mark?.getAttribute('aria-label'), mark?.getAttribute('role')]).toEqual([
      'preflight',
      'preflight',
      'img',
    ])
    expect(card.querySelectorAll('.preflight').length).toBe(1)
  })

  it('every button has a tip key and an aria-label', () => {
    for (const b of paint().querySelectorAll<HTMLButtonElement>('.pair-act button, .req-actions button')) {
      expect(b.dataset.label).toBeTruthy()
      expect(b.getAttribute('aria-label')).toBeTruthy()
    }
  })

  it('the large view of a card with rows has only close: a whole-card allow there would undo a row deny', () => {
    expect(acts(shotView(req(), state()), '[data-act]')).toEqual(['close'])
  })

  it('a click refusal (no blocked pairs) keeps the single deny/allow', () => {
    const card = paint([], {}, req({ reason: 'deny-list', detail: 'Zakończ', blocked: [] }))
    expect(card.querySelector('.pair')).toBeNull()
    expect(acts(card, '.req-actions [data-act]')).toEqual(['deny', 'grant'])
  })
})
