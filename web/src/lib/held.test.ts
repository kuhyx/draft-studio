import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, type HeldListing, type ReleasedProposal } from './api'
import { forgotten, HeldPoller, landing, lostSome, MAX_MS, MIN_MS } from './held'

const meta = {
  id: 'd1',
  kind: 'text',
  lang: 'pl',
  to: '',
  subject: '',
  toAuthor: 'you',
  subjectAuthor: 'claude',
  gmailDraftId: '',
  brief: 'b',
  intake: [],
  phase: 'editing',
  asks: [],
  claude: [],
  version: 3,
  updated: 1,
}
const state = { meta, text: 'Draft', claude: [], approved: false }
// The server's <request> carries more than the client reads: the permission card needs it all.
const request = {
  id: 'r1',
  draftId: 'd1',
  spec: { url: 'http://127.0.0.1:1/index.html', name: 'shot-1', steps: [], highlight: null },
  step: 0,
  stepText: 'Zapisz',
  reason: 'request',
  detail: 'POST http://127.0.0.1:1/api/save',
  allow: { step: 0, click: false, requests: [['POST', 'http://127.0.0.1:1/api/save']] },
  grants: [],
  status: 'pending',
  created: 1,
  shot: '/api/captures/r1/shot',
}
const held = { id: 'h1', kind: 'proposal', from: 0, to: 4, requests: [request], login: [], busy: false }
const heldDraft = { id: 'h0', kind: 'draft', from: null, to: null, requests: [], login: ['https://a.b'], busy: true }
const relProp = {
  id: 'h2',
  kind: 'proposal',
  replacement: 'new',
  from: 2,
  to: 5,
  span: 'abc',
  previous: null,
  stripped: [],
  dropped: [{ key: 'shot-1', reason: 'denied' }],
}
const relDraft = { id: 'h3', kind: 'draft', questions: [], state, stripped: [] }
const listing = { held: [held, heldDraft], released: [relProp, relDraft] }

function reply(body: unknown, status = 200) {
  const fn = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(() =>
    Promise.resolve(new Response(JSON.stringify(body), { status })),
  )
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('held api', () => {
  it('lists held and released answers, keeping every request field', async () => {
    const f = reply(listing)
    const out = await api.held('d 1')
    expect(f.mock.calls[0]?.[0]).toBe('/api/drafts/d%201/held')
    expect(out.held[0]?.requests[0]).toEqual(request)
    expect(out.held[1]?.from).toBeNull()
    expect(out.released.map((r) => r.kind)).toEqual(['proposal', 'draft'])
    expect(out.released[1]?.dropped).toEqual([])
  })

  it('collects shown answers and retries a login hold', async () => {
    const c = reply({ collected: 2 })
    expect(await api.collect('d1', ['h2', 'h3'])).toBe(2)
    expect(c.mock.calls[0]?.[1]?.body).toBe(JSON.stringify({ collect: ['h2', 'h3'] }))
    const r = reply(listing)
    expect((await api.retry('d1', 'h0')).held).toHaveLength(2)
    expect(r.mock.calls[0]?.[1]?.body).toBe(JSON.stringify({ retry: 'h0' }))
  })

  it('accepts a 202 hold from propose, brief and intake', async () => {
    reply({ held }, 202)
    expect(await api.propose('d1', 'text', 0, 4, '[shot]')).toEqual({ held })
    reply({ held: heldDraft }, 202)
    expect(await api.brief('d1', 'b')).toEqual({ held: heldDraft })
    reply({ held: heldDraft }, 202)
    expect(await api.intake('d1', [])).toEqual({ held: heldDraft })
    reply({ replacement: 'r', from: 0, to: 1, dropped: [{ key: 'k', reason: 'url' }] })
    expect(await api.propose('d1', 'a', 0, 1, '')).toMatchObject({ stripped: [], dropped: [{ key: 'k' }] })
  })

  it('refuses a listing of the wrong shape', async () => {
    reply({ held: [{ ...held, kind: 'other' }], released: [] })
    await expect(api.held('d1')).rejects.toEqual(new ApiError('bad-response'))
  })
})

describe('released answers', () => {
  const rel = relProp as ReleasedProposal

  it('lands in the session where it was asked at the mapped span, with the revised ids', () => {
    const pending = { from: 7, to: 10, stale: false }
    expect(landing(rel, { pending, ids: [4] }, 'whatever')).toEqual({ from: 7, to: 10, ids: [4] })
    expect(landing(rel, { pending }, '')).toEqual({ from: 7, to: 10, ids: [] })
    expect(landing(rel, { pending: { ...pending, stale: true } }, '')).toBeNull()
  })

  it('lands after a reload only while the doc still holds the span', () => {
    expect(landing(rel, undefined, 'xxabcxx')).toEqual({ from: 2, to: 5, ids: [] })
    expect(landing(rel, undefined, 'xxabZxx')).toBeNull()
    expect(landing(rel, undefined, 'xxab')).toBeNull()
  })

  it('signals when an image or a screenshot was lost', () => {
    expect(lostSome({ stripped: [], dropped: [] })).toBe(false)
    expect(lostSome({ stripped: ['![](a.png)'], dropped: [] })).toBe(true)
    expect(lostSome({ stripped: [], dropped: [{ key: 'k', reason: 'denied' }] })).toBe(true)
  })

  it('names the holds the server no longer knows', () => {
    const l = listing as HeldListing
    expect(forgotten(l, ['h1', 'h2', 'gone'])).toEqual(['gone'])
  })
})

function rig(listings: (HeldListing | Error)[]) {
  vi.useFakeTimers()
  const vis: { visibilityState: DocumentVisibilityState } = { visibilityState: 'visible' }
  const doc = Object.assign(new EventTarget(), vis)
  const win = new EventTarget()
  const seen: HeldListing[] = []
  const list = vi.fn(() => {
    const next = listings.length > 1 ? listings.shift() : listings[0]
    return next instanceof Error || next === undefined ? Promise.reject(next ?? new Error('none')) : Promise.resolve(next)
  })
  const poller = new HeldPoller({ list, onListing: (l) => void seen.push(l), doc, win })
  return { doc, win, list, seen, poller }
}
const busy = { held: [held], released: [] } as HeldListing
const idle = { held: [], released: [] } as HeldListing

describe('held poller', () => {
  it('backs off from 1 s to 15 s while something is held', async () => {
    const { list, poller } = rig([busy])
    poller.watch()
    const gaps = [MIN_MS, 2000, 4000, 8000, MAX_MS, MAX_MS]
    for (const [i, gap] of gaps.entries()) {
      await vi.advanceTimersByTimeAsync(gap - 1)
      expect(list).toHaveBeenCalledTimes(i)
      await vi.advanceTimersByTimeAsync(1)
      expect(list).toHaveBeenCalledTimes(i + 1)
    }
  })

  it('stops once nothing is held and hands every listing on', async () => {
    const { list, seen, poller } = rig([busy, idle])
    poller.watch()
    await vi.advanceTimersByTimeAsync(MIN_MS + 2000)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(list).toHaveBeenCalledTimes(2)
    expect(seen).toEqual([busy, idle])
  })

  it('pauses while the page is hidden and resumes on visibilitychange or focus', async () => {
    const { doc, win, list, poller } = rig([busy])
    doc.visibilityState = 'hidden'
    poller.watch()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(list).not.toHaveBeenCalled()
    doc.dispatchEvent(new Event('visibilitychange'))
    expect(list).not.toHaveBeenCalled()
    doc.visibilityState = 'visible'
    doc.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(0)
    expect(list).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(MIN_MS)
    expect(list).toHaveBeenCalledTimes(2) // the backoff restarted at 1 s
    win.dispatchEvent(new Event('focus'))
    await vi.advanceTimersByTimeAsync(0)
    expect(list).toHaveBeenCalledTimes(3)
  })

  it('ignores focus while nothing is held', async () => {
    const { win, list, poller } = rig([idle])
    await poller.poll()
    win.dispatchEvent(new Event('focus'))
    await vi.advanceTimersByTimeAsync(60_000)
    expect(list).toHaveBeenCalledTimes(1)
  })

  it('runs one request at a time; a poke meanwhile polls again right after', async () => {
    const { list, seen, poller } = rig([busy])
    let release = () => undefined as unknown
    list.mockImplementationOnce(
      () =>
        new Promise((ok) => {
          release = () => {
            ok(idle)
          }
        }),
    )
    poller.poke()
    poller.poke()
    expect(list).toHaveBeenCalledTimes(1)
    release()
    await vi.advanceTimersByTimeAsync(0)
    expect(list).toHaveBeenCalledTimes(2)
    expect(seen).toEqual([idle, busy])
  })

  it('keeps backing off through failures', async () => {
    const { list, seen, poller } = rig([new Error('offline'), busy])
    poller.watch()
    await vi.advanceTimersByTimeAsync(MIN_MS)
    expect(seen).toEqual([])
    await vi.advanceTimersByTimeAsync(2000)
    expect(list).toHaveBeenCalledTimes(2)
    expect(seen).toEqual([busy])
  })
})
