import { afterEach, describe, expect, it, vi } from 'vitest'
import { type FeedDeps, watchDrafts } from './feed'

interface Stream {
  opened: () => void
  data: (raw: string) => void
}

function fakes(withLocks: boolean) {
  const streams: Stream[] = []
  const posted: unknown[] = []
  let heard: ((data: unknown) => void) | undefined
  let granted: (() => Promise<void>) | undefined
  const deps: FeedDeps = {
    open: (opened, data) => streams.push({ opened, data }),
    channel: { listen: (cb) => (heard = cb), post: (m) => posted.push(m) },
    locks: withLocks
      ? {
          request: (_name, cb) => {
            granted = cb
            return new Promise(() => undefined)
          },
        }
      : undefined,
  }
  return { deps, streams, posted, hear: (d: unknown) => heard?.(d), grant: () => void granted?.() }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('watchDrafts', () => {
  it('the tab holding the lock streams and tells the other tabs', () => {
    const f = fakes(true)
    const seen = vi.fn()
    watchDrafts(seen, f.deps)
    expect(f.streams).toHaveLength(0) // no stream before the lock is ours
    f.grant()
    const s = f.streams[0]
    s.opened()
    s.data('{"id":"abc123"}')
    s.data('not json')
    s.data('{"nope":1}')
    expect(seen.mock.calls).toEqual([['*'], ['abc123']])
    expect(f.posted).toEqual([{ id: '*' }, { id: 'abc123' }])
  })

  it('a tab without the lock hears the leader over the channel', () => {
    const f = fakes(true)
    const seen = vi.fn()
    watchDrafts(seen, f.deps)
    f.hear({ id: 'def456' })
    f.hear('junk')
    expect(seen.mock.calls).toEqual([['def456']])
  })

  it('streams itself when the browser has no locks', () => {
    const f = fakes(false)
    const seen = vi.fn()
    watchDrafts(seen, f.deps)
    f.streams[0]?.data('{"id":"x1y2z3"}')
    expect(seen).toHaveBeenCalledWith('x1y2z3')
  })

  it('uses EventSource and BroadcastChannel by default', () => {
    const sources: { onopen: unknown; onmessage: ((e: { data: string }) => void) | null; url: string }[] = []
    const channels: { onmessage: ((e: { data: unknown }) => void) | null; sent: unknown[] }[] = []
    vi.stubGlobal(
      'EventSource',
      class {
        onopen = null
        onmessage = null
        constructor(readonly url: string) {
          sources.push(this)
        }
      },
    )
    vi.stubGlobal(
      'BroadcastChannel',
      class {
        onmessage = null
        sent: unknown[] = []
        constructor() {
          channels.push(this)
        }
        postMessage(m: unknown) {
          this.sent.push(m)
        }
      },
    )
    vi.stubGlobal('navigator', { locks: undefined })
    const seen = vi.fn()
    watchDrafts(seen)
    expect(sources[0]?.url).toBe('/api/events')
    sources[0]?.onmessage?.({ data: '{"id":"aaa111"}' })
    channels[0]?.onmessage?.({ data: { id: 'bbb222' } })
    expect(seen.mock.calls).toEqual([['aaa111'], ['bbb222']])
    expect(channels[0]?.sent).toEqual([{ id: 'aaa111' }])
  })
})
