import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { changed, click, held, type Kit, mountCard, q, req } from '../test/cardKit'

let kit: Kit

beforeEach(() => {
  kit = mountCard()
})

afterEach(() => {
  kit.card.destroy()
  vi.restoreAllMocks()
})

describe('editor permission card', () => {
  it('one card per hold in the work column; hidden when nothing is held', () => {
    expect(kit.card.desk.hidden).toBe(true)
    kit.show(held('h1', { requests: [req('r1')] }), held('h2', { login: ['https://a.example'] }))
    expect(kit.card.desk.parentElement?.id).toBe('work')
    expect([...kit.card.desk.querySelectorAll<HTMLElement>('.held')].map((c) => c.dataset.held)).toEqual(['h1', 'h2'])
    expect(kit.card.desk.hidden).toBe(false)
    kit.show()
    expect(kit.card.desk.hidden).toBe(true)
  })

  it('spans: the tracked span when this page asked, else the server span; none for a first draft or a stale one', () => {
    kit.deps.holds.set('a', { held: held('a'), mine: { pending: { from: 4, to: 9, stale: false } } })
    kit.deps.holds.set('b', { held: held('b', { from: 1, to: 2 }) })
    kit.deps.holds.set('c', { held: held('c', { kind: 'draft', from: null, to: null }) })
    kit.deps.holds.set('d', { held: held('d'), mine: { pending: { from: 0, to: 1, stale: true } } })
    kit.deps.holds.set('e', { held: held('e', { from: 1, to: null }) })
    changed()
    expect(kit.spans.at(-1)?.map((s) => [s.id, s.from, s.to, s.hue])).toEqual([
      ['a', 4, 9, 'var(--accent)'],
      ['b', 1, 2, 'var(--cat-2)'],
    ])
  })

  it('a colour stays with its hold; a new hold takes the lowest free one', () => {
    kit.show(held('h1'), held('h2'))
    kit.show(held('h2'))
    expect(kit.spans.at(-1)?.[0]?.hue).toBe('var(--cat-2)')
    kit.show(held('h2'), held('h3'))
    expect(kit.spans.at(-1)?.map((s) => s.hue)).toEqual(['var(--cat-2)', 'var(--accent)'])
  })

  it('nothing changed: no repaint', () => {
    kit.show(held('h1', { requests: [req('r1')] }))
    const before = kit.card.desk.firstElementChild
    const n = kit.spans.length
    changed()
    expect(kit.card.desk.firstElementChild).toBe(before)
    expect(kit.spans.length).toBe(n)
  })

  it('while #work is hidden (held first draft) the card sits after the intake form', () => {
    kit.deps.work.hidden = true
    kit.show(held('h1', { kind: 'draft', from: null, to: null, busy: true }))
    expect(kit.card.desk.previousElementSibling?.id).toBe('intake')
  })

  it('a click on the detail expands it, and it stays expanded across repaints', () => {
    kit.show(held('h1', { requests: [req('r1')] }))
    click('.detail')
    expect(q('.detail')?.classList.contains('open')).toBe(true)
    kit.show(held('h1', { requests: [req('r1')], login: ['https://b.example'] }))
    expect(q('.detail')?.classList.contains('open')).toBe(true)
    click('.detail')
    expect(q('.detail')?.classList.contains('open')).toBe(false)
    kit.card.desk.click() // a click on no control does nothing
  })

  it('hovering a card lights its span', () => {
    kit.show(held('h1'), held('h2'))
    const before = q('[data-held="h2"]')
    before?.dispatchEvent(new Event('pointerover', { bubbles: true }))
    expect(q('[data-held="h2"]')).toBe(before) // relit, not rebuilt: a click there must still land
    expect(kit.spans.at(-1)?.map((s) => s.lit)).toEqual([false, true])
    kit.card.desk.dispatchEvent(new Event('pointerover', { bubbles: true }))
    kit.card.desk.dispatchEvent(new Event('pointerleave'))
    expect(kit.spans.at(-1)?.map((s) => s.lit)).toEqual([false, false])
  })
})
