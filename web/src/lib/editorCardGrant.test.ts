import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, type CaptureRequest } from './api'
import { changed, click, flush, gate, held, type Kit, mountCard, q, req } from '../test/cardKit'

const A = 'https://api.example/a'
const B = 'https://api.example/b'
const C = 'https://api.example/c'
const rows = (id = 'r1'): CaptureRequest => ({
  ...req(id),
  blocked: [
    ['POST', A],
    ['POST', B],
    ['POST', C],
  ],
})
const pick = (i: number, side: 'allow' | 'deny') => {
  click(`.pair[data-i="${String(i)}"] [data-act=pick-${side}]`)
}
const sendBtn = () => q('[data-act=send]')

let kit: Kit

beforeEach(() => {
  kit = mountCard()
})

afterEach(() => {
  kit.card.destroy()
  vi.restoreAllMocks()
})

describe('per-request grant on the card', () => {
  it('picks rows, sends only when all are decided, posts exactly the allowed pairs', async () => {
    const g = gate<Record<string, unknown>>()
    const grant = vi.spyOn(api, 'grant').mockReturnValue(g.promise)
    kit.show(held('h1', { requests: [rows()] }))
    expect(sendBtn()?.disabled).toBe(true)
    pick(1, 'allow')
    pick(0, 'deny')
    expect(sendBtn()?.disabled).toBe(true)
    click('[data-act=send]') // disabled: nothing happens
    expect(grant).not.toHaveBeenCalled()
    click('[data-act=deny-rest]')
    expect([...document.querySelectorAll('.dots i')].map((i) => i.className)).toEqual(['d', 'a', 'd'])
    expect(sendBtn()?.disabled).toBe(false)
    click('[data-act=send]')
    click('[data-act=send]')
    expect(grant).toHaveBeenCalledTimes(1)
    expect(grant).toHaveBeenCalledWith('r1', [['POST', B]])
    expect(q('.req')?.classList.contains('locked')).toBe(true)
    changed()
    expect(sendBtn()?.disabled).toBe(true)
    g.resolve({})
    await flush()
    expect(kit.deps.poke).toHaveBeenCalledTimes(1)
    expect(kit.deps.fail).not.toHaveBeenCalled()
  })

  it('a second click on the pressed side undecides the row; allow-rest fills only the undecided', () => {
    kit.show(held('h1', { requests: [rows()] }))
    pick(0, 'deny')
    pick(0, 'deny')
    pick(2, 'deny')
    click('[data-act=allow-rest]')
    expect([...document.querySelectorAll('.dots i')].map((i) => i.className)).toEqual(['a', 'a', 'd'])
  })

  it('all denied uses the deny route; all allowed grants the whole card', async () => {
    const deny = vi.spyOn(api, 'deny').mockResolvedValue({})
    const grant = vi.spyOn(api, 'grant').mockResolvedValue({})
    kit.show(held('h1', { requests: [rows('r1'), rows('r2')] }))
    click('[data-rid="r1"] [data-act=deny-rest]')
    click('[data-rid="r1"] [data-act=send]')
    click('[data-rid="r2"] [data-act=allow-rest]')
    click('[data-rid="r2"] [data-act=send]')
    await flush()
    expect(deny).toHaveBeenCalledWith('r1')
    expect(grant).toHaveBeenCalledWith('r2', undefined)
  })

  it('a loud failure unlocks the card and keeps the decisions to send again; a quiet one waits for the listing', async () => {
    const grant = vi.spyOn(api, 'grant').mockRejectedValueOnce(new ApiError('offline'))
    kit.show(held('h1', { requests: [rows()] }))
    pick(0, 'allow')
    click('[data-act=deny-rest]')
    click('[data-act=send]')
    await flush()
    expect(kit.deps.fail).toHaveBeenCalledWith(new ApiError('offline'))
    expect(sendBtn()?.disabled).toBe(false)
    expect(q('.req')?.classList.contains('locked')).toBe(false)
    grant.mockRejectedValueOnce(new ApiError('not-pending'))
    click('[data-act=send]')
    await flush()
    expect(kit.deps.fail).toHaveBeenCalledTimes(1)
    expect(grant).toHaveBeenLastCalledWith('r1', [['POST', A]])
  })

  it('decisions of a request gone from the listing are dropped', () => {
    kit.show(held('h1', { requests: [rows()] }))
    pick(0, 'allow')
    kit.show(held('h1', { requests: [rows('r2')] }))
    kit.show(held('h1', { requests: [rows()] }))
    expect([...document.querySelectorAll('.dots i')].map((i) => i.className)).toEqual(['u', 'u', 'u'])
  })

  it('a request gone before its listing repainted the card sends nothing', () => {
    const grant = vi.spyOn(api, 'grant')
    kit.show(held('h1', { requests: [rows()] }))
    click('[data-act=allow-rest]')
    kit.deps.holds.clear()
    click('[data-act=send]')
    expect(grant).not.toHaveBeenCalled()
  })

  it('a row url opens on click and closes on the next', () => {
    kit.show(held('h1', { requests: [rows()] }))
    click('.pair[data-i="1"] .detail')
    expect(q('.pair[data-i="1"] .detail')?.classList.contains('open')).toBe(true)
    expect(q('.pair[data-i="0"] .detail')?.classList.contains('open')).toBe(false)
    click('.pair[data-i="1"] .detail')
    expect(q('.pair[data-i="1"] .detail')?.classList.contains('open')).toBe(false)
  })
})
