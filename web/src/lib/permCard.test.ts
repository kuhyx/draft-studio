import { describe, expect, it } from 'vitest'
import type { CaptureRequest, Held } from './api'
import { detailNodes, heldCard, host, HUES, nextHue, REASONS, shotView, signature, type CardState } from './permCard'

const req = (id: string, reason: string, detail: string): CaptureRequest => ({
  id,
  reason,
  detail,
  shot: `/api/captures/${id}/shot`,
})
const hold = (over: Partial<Held> = {}): Held => ({
  id: 'h1',
  kind: 'proposal',
  from: 0,
  to: 4,
  requests: [],
  login: [],
  busy: false,
  tries: 0,
  failed: [],
  ...over,
})
const idle: CardState = { working: new Set(), waiting: new Set(), opened: new Set() }
const label = (el: Element | null | undefined) => el?.getAttribute('aria-label')

describe('failed hold', () => {
  const failed = [
    { key: 'cf-odnow', reason: 'no-target' },
    { key: 'cf-menu', reason: 'bad-spec' },
  ]

  it('shows one row per dropped shot with its reason, then dismiss and retry', () => {
    const card = heldCard(hold({ failed }), HUES[0], idle)
    const rows = [...card.querySelectorAll<HTMLElement>('.failed')]
    expect(rows.map((r) => [r.dataset.key, r.querySelector('.origin')?.textContent])).toEqual([
      ['cf-odnow', 'cf-odnow no-target'],
      ['cf-menu', 'cf-menu bad-spec'],
    ])
    expect(rows.map((r) => r.querySelector<HTMLElement>('.reason')?.dataset.label)).toEqual(['missingImage', 'missingImage'])
    expect([...card.querySelectorAll<HTMLElement>('.failed-bar button')].map((b) => b.dataset.act)).toEqual(['dismiss', 'retry'])
    expect(card.querySelector('.req, .login')).toBeNull()
  })

  it('spins the retry while it runs', () => {
    const card = heldCard(hold({ failed }), HUES[0], { ...idle, working: new Set(['retry h1']) })
    expect(card.querySelector('.failed-bar')?.classList.contains('working')).toBe(true)
    expect(card.querySelector<HTMLButtonElement>('[data-act="retry"]')?.disabled).toBe(true)
  })

  it('a hold without failures has no failed rows, and the signature follows the failures', () => {
    expect(heldCard(hold(), HUES[0], idle).querySelector('.failed')).toBeNull()
    expect(signature(hold())).not.toBe(signature(hold({ failed })))
  })
})

describe('permission card', () => {
  it('shows one row per request: shot, reason glyph, detail, deny and allow', () => {
    const card = heldCard(hold({ requests: [req('r1', 'submit', 'POST https://a.example/x')] }), 'var(--cat-2)', idle)
    expect(card.dataset.held).toBe('h1')
    expect(card.style.getPropertyValue('--hc')).toBe('var(--cat-2)')
    const row = card.querySelector<HTMLElement>('.req')
    expect(row?.dataset.rid).toBe('r1')
    expect(row?.querySelector('img')?.getAttribute('src')).toBe('/api/captures/r1/shot')
    expect(row?.querySelector<HTMLElement>('.shot')?.dataset.act).toBe('zoom')
    expect(row?.querySelector<HTMLElement>('.reason')?.dataset.label).toBe('reasonSubmit')
    expect(label(row?.querySelector('.reason'))).toBe('reasonSubmit')
    expect(row?.querySelector('.method')?.textContent).toBe('POST')
    expect(row?.querySelector('.detail')?.textContent).toBe('POST https://a.example/x')
    expect([...(row?.querySelectorAll<HTMLElement>('.req-actions .icon-btn') ?? [])].map((b) => b.dataset.act)).toEqual([
      'deny',
      'grant',
    ])
    expect(card.textContent).not.toMatch(/undefined|NaN|object Object/)
  })

  it('maps every reason to its own glyph and tip, an unknown one to the request glyph', () => {
    const keys = Object.keys(REASONS).map((r) => {
      const el = heldCard(hold({ requests: [req('r', r, 'Zapisz')] }), HUES[0], idle).querySelector<HTMLElement>('.reason')
      return el?.dataset.label
    })
    expect(new Set(keys).size).toBe(5)
    const odd = heldCard(hold({ requests: [req('r', 'new-reason', 'x')] }), HUES[0], idle)
    expect(odd.querySelector<HTMLElement>('.reason')?.dataset.label).toBe('reasonRequest')
  })

  it('a request in flight spins and disables both buttons; an opened detail stays opened', () => {
    const state: CardState = { working: new Set(['r1']), waiting: new Set(), opened: new Set(['r1']) }
    const row = heldCard(hold({ requests: [req('r1', 'input', 'Numer klienta')] }), HUES[0], state)
    expect(row.querySelector('.req')?.classList.contains('working')).toBe(true)
    expect([...row.querySelectorAll<HTMLButtonElement>('.req-actions button')].every((b) => b.disabled)).toBe(true)
    expect(row.querySelector('.grant')?.innerHTML).toContain('M12 3a9')
    expect(row.querySelector('.detail')?.classList.contains('open')).toBe(true)
  })

  it('login rows: key button and the host; waiting spins and disables', () => {
    const h = hold({ login: ['https://panel.example', 'https://sso.example:8443'] })
    const card = heldCard(h, HUES[0], { ...idle, waiting: new Set(['h1 https://sso.example:8443']) })
    const rows = [...card.querySelectorAll<HTMLElement>('.login')]
    expect(rows.map((r) => r.querySelector('.origin')?.textContent)).toEqual(['panel.example', 'sso.example:8443'])
    const [first, second] = rows.map((r) => r.querySelector<HTMLButtonElement>('button'))
    expect(first?.dataset.origin).toBe('https://panel.example')
    expect([first?.dataset.label, first?.disabled, second?.disabled]).toEqual(['login', false, true])
    expect(rows[1]?.classList.contains('waiting')).toBe(true)
  })

  it('a busy hold shows the hourglass and a retry button that spins while retrying', () => {
    const card = heldCard(hold({ busy: true }), HUES[0], idle)
    expect(card.querySelector<HTMLElement>('.busy .reason')?.dataset.label).toBe('heldBusy')
    const btn = card.querySelector<HTMLButtonElement>('.busy button')
    expect([btn?.dataset.act, btn?.dataset.label, btn?.disabled]).toEqual(['retry', 'retry', false])
    const spinning = heldCard(hold({ busy: true }), HUES[0], { ...idle, working: new Set(['retry h1']) })
    expect(spinning.querySelector<HTMLButtonElement>('.busy button')?.disabled).toBe(true)
  })

  it('the large view holds the shot, its own deny/allow and a close button', () => {
    const box = shotView(req('r9', 'request', 'POST https://a.example/'), idle)
    expect(box.querySelector('img')?.getAttribute('src')).toBe('/api/captures/r9/shot')
    const acts = [...box.querySelectorAll<HTMLElement>('[data-act]')].map((b) => [b.dataset.act, b.closest<HTMLElement>('[data-rid]')?.dataset.rid])
    expect(acts).toEqual([
      ['deny', 'r9'],
      ['grant', 'r9'],
      ['close', 'r9'],
    ])
    const busy = shotView(req('r9', 'request', 'x'), { ...idle, working: new Set(['r9']) })
    expect(busy.querySelector<HTMLButtonElement>('.grant')?.disabled).toBe(true)
  })

  it('splits METHOD url, keeps other details whole', () => {
    expect(detailNodes('GET https://a.example/ q').map((n) => n.textContent)).toEqual(['GET', ' https://a.example/ q'])
    expect(detailNodes('Zapłać teraz').map((n) => n.textContent)).toEqual(['Zapłać teraz'])
  })

  it('host: the origin without its scheme; a bad origin stays as is', () => {
    expect(host('https://a.example')).toBe('a.example')
    expect(host('not a url')).toBe('not a url')
  })

  it('signature changes when the hold moves', () => {
    const a = hold({ login: ['https://a.example'] })
    expect(signature(a)).toBe(signature({ ...a }))
    expect(signature(a)).not.toBe(signature({ ...a, login: [] }))
    expect(signature(a)).not.toBe(signature({ ...a, busy: true }))
    expect(signature(a)).not.toBe(signature({ ...a, requests: [req('r', 'input', 'x')] }))
    expect(signature(a)).not.toBe(signature({ ...a, tries: 1 })) // a retry that failed the same way
  })

  it('nextHue: the lowest free colour, wrapping when all are taken', () => {
    expect(nextHue([])).toBe(0)
    expect(nextHue([0, 2])).toBe(1)
    expect(nextHue(HUES.map((_, i) => i))).toBe(HUES.length)
    expect(HUES[0]).toBe('var(--accent)')
  })
})
