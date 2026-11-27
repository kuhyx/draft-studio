import { describe, expect, it } from 'vitest'
import type { CaptureRequest } from './api'
import { decided, grantCall, grantRows, rest, toggle, type Pick } from './grantPick'

const A = 'https://api.example/impersonate'
const B = 'https://chat.example/messages'
const C = 'https://ga.example/collect'
// Junk on purpose too: a server may send anything in these fields.
const card = (blocked?: unknown, announces?: unknown): CaptureRequest =>
  ({
    id: 'r1',
    reason: 'request',
    detail: `POST ${A}`,
    shot: '/s/r1',
    ...(blocked === undefined ? {} : { blocked }),
    ...(announces === undefined ? {} : { announces }),
  }) as CaptureRequest

describe('grant rows of a card', () => {
  it('one row per blocked pair, in order, keeping the pair the server sent', () => {
    const rows = grantRows(card([['POST', A], ['POST', B], ['SUBMIT', C]]))
    expect(rows.map((r) => [r.method, r.url, r.pair, r.preflight])).toEqual([
      ['POST', A, ['POST', A], false],
      ['POST', B, ['POST', B], false],
      ['SUBMIT', C, ['SUBMIT', C], false],
    ])
  })

  it('a click refusal (no or empty blocked) has no rows: the card keeps its single deny/allow', () => {
    expect(grantRows(card())).toEqual([])
    expect(grantRows(card([]))).toEqual([])
    expect(grantRows(card('junk'))).toEqual([])
    expect(grantRows(card([['POST'], [1, 2]]))).toEqual([])
  })

  it('a preflight announcing a write shows as that write, marked, and still posts the OPTIONS pair', () => {
    const rows = grantRows(card([['OPTIONS', A], ['POST', B], ['OPTIONS', C]], ['post', null, null]))
    expect(rows.map((r) => [r.method, r.url, r.pair, r.preflight])).toEqual([
      ['POST', A, ['OPTIONS', A], true],
      ['POST', B, ['POST', B], false],
      ['OPTIONS', C, ['OPTIONS', C], false],
    ])
  })

  it('announces is optional and tolerated when malformed or short', () => {
    for (const odd of [undefined, 'x', [1], []]) {
      expect(grantRows(card([['OPTIONS', A]], odd)).map((r) => [r.method, r.preflight])).toEqual([['OPTIONS', false]])
    }
    // an announce on a non-OPTIONS row is ignored
    expect(grantRows(card([['POST', A]], ['PUT']))[0]?.preflight).toBe(false)
  })
})

describe('picks', () => {
  it('toggle decides a row, a second click on the same side undecides it', () => {
    let p: readonly Pick[] = []
    p = toggle(p, 3, 1, 'a')
    expect(p).toEqual([undefined, 'a', undefined])
    p = toggle(p, 3, 1, 'd')
    expect(p).toEqual([undefined, 'd', undefined])
    p = toggle(p, 3, 1, 'd')
    expect(p).toEqual([undefined, undefined, undefined])
  })

  it('rest decides only the undecided rows', () => {
    expect(rest(['a', undefined, 'd', undefined], 4, 'd')).toEqual(['a', 'd', 'd', 'd'])
    expect(rest([], 2, 'a')).toEqual(['a', 'a'])
  })

  it('decided only when every row has a side', () => {
    expect(decided([], 0)).toBe(false)
    expect(decided(['a', undefined], 2)).toBe(false)
    expect(decided(['a'], 2)).toBe(false)
    expect(decided(['a', 'd'], 2)).toBe(true)
  })
})

describe('the call send makes', () => {
  const rows = grantRows(card([['POST', A], ['OPTIONS', B], ['POST', C]], [null, 'POST', null]))

  it('some allowed: a partial grant with exactly the allowed pairs, OPTIONS kept as sent', () => {
    expect(grantCall(rows, ['d', 'a', 'd'])).toEqual({ route: 'grant', allow: [['OPTIONS', B]] })
    expect(grantCall(rows, ['a', 'd', 'a'])).toEqual({
      route: 'grant',
      allow: [
        ['POST', A],
        ['POST', C],
      ],
    })
  })

  it('all allowed: the whole card (no allow list); all denied: the deny route', () => {
    expect(grantCall(rows, ['a', 'a', 'a'])).toEqual({ route: 'grant' })
    expect(grantCall(rows, ['d', 'd', 'd'])).toEqual({ route: 'deny' })
  })

  it('nothing to send while a row is undecided', () => {
    expect(grantCall(rows, ['a', undefined, 'd'])).toBeNull()
  })
})
