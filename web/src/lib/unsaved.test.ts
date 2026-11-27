import 'fake-indexeddb/auto'
import { IDBFactory } from 'fake-indexeddb'
import { describe, expect, it, vi } from 'vitest'
import type { DraftState } from './api'
import {
  baseOf,
  type Db,
  Keeper,
  type Locks,
  makeLocks,
  noLocks,
  openDb,
  type Snapshot,
  startSession,
  takeOrphans,
  type Unsaved,
} from './unsaved'

const state = (version: number, text: string): DraftState => ({
  meta: {
    id: 'a', kind: 'text', lang: '', to: '', subject: '', toAuthor: 'you', subjectAuthor: 'you', gmailDraftId: '',
    brief: '', intake: [], phase: 'editing', asks: [], claude: [], version, updated: 0,
  },
  text,
  claude: [],
  approved: false,
  proposals: [], gone: {},
})
const snap = (text: string, over: Partial<Snapshot> = {}): Snapshot => ({
  baseVersion: 3, baseText: 'base', sent: null, text, claude: [[0, 2]], to: 'a@b', subject: 'Hi', toAuthor: 'you', subjectAuthor: 'claude',
  state: state(3, 'base'), ...over,
})
const rec = (owner: string, at: number, over: Partial<Unsaved> = {}): Unsaved => ({ ...snap('t'), id: 'a', owner, at, ...over })

describe('openDb', () => {
  it('keeps one record per (draft, tab) and lists a draft\'s records', async () => {
    const db = openDb(new IDBFactory())
    await db.write('a', 'tab1', rec('tab1', 1))
    await db.write('a', 'tab2', rec('tab2', 2))
    await db.write('b', 'tab1', rec('tab1', 3, { id: 'b' }))
    await db.write('a', 'tab1', rec('tab1', 4, { text: 'newer' }))
    const got = await db.all('a')
    expect(got.map((r) => [r.owner, r.text])).toEqual([['tab1', 'newer'], ['tab2', 't']])
    expect((await db.all('b')).length).toBe(1)
  })

  it('removes the record, and the one it replaces, in the same write', async () => {
    const db = openDb(new IDBFactory())
    await db.write('a', 'old', rec('old', 1))
    await db.write('a', 'me', rec('me', 2), ['old', 'older'])
    expect((await db.all('a')).map((r) => r.owner)).toEqual(['me'])
    await db.write('a', 'me', null)
    expect(await db.all('a')).toEqual([])
  })

  it('skips a record that no longer has the shape', async () => {
    const db = openDb(new IDBFactory())
    await db.write('a', 'tab1', rec('tab1', 1))
    await db.write('a', 'bad', { ...rec('bad', 2), baseVersion: 'x' } as unknown as Unsaved)
    expect((await db.all('a')).map((r) => r.owner)).toEqual(['tab1'])
  })

  it('rejects when the database cannot open', async () => {
    const failing = {
      open: () => {
        const r: Record<string, unknown> = { error: new Error('no idb') }
        queueMicrotask(() => {
          ;(r.onerror as () => void)()
        })
        return r
      },
    }
    await expect(openDb(failing as unknown as IDBFactory).all('a')).rejects.toThrow('no idb')
  })

  it('rejects when the write transaction fails', async () => {
    const store = { put: vi.fn(), delete: vi.fn() }
    const tx: Record<string, unknown> = { objectStore: () => store, error: new Error('quota') }
    const idb = {
      transaction: () => {
        queueMicrotask(() => {
          ;(tx.onerror as () => void)()
        })
        return tx
      },
    }
    const factory = {
      open: () => {
        const r: Record<string, unknown> = { result: idb }
        queueMicrotask(() => {
          ;(r.onsuccess as () => void)()
        })
        return r
      },
    }
    await expect(openDb(factory as unknown as IDBFactory).write('a', 'x', rec('x', 1))).rejects.toThrow('quota')
  })
})

describe('Keeper', () => {
  const fakeDb = () => {
    const calls: [string, Unsaved | null, readonly string[] | undefined][] = []
    const db: Db = {
      write: (_id, owner, r, drop) => {
        calls.push([owner, r, drop])
        return Promise.resolve()
      },
      all: () => Promise.resolve([]),
    }
    return { db, calls }
  }

  it('writes the latest snapshot only while an earlier write runs', async () => {
    const { db, calls } = fakeDb()
    const k = new Keeper('a', 'me', db, vi.fn(), () => 9)
    k.write(snap('one'))
    k.write(snap('two'))
    k.write(snap('three'))
    await k.idle()
    expect(calls.map((c) => c[1]?.text)).toEqual(['three'])
    expect(calls[0]?.[1]).toMatchObject({ id: 'a', owner: 'me', at: 9 })
  })

  it('removes the record when nothing is unsaved', async () => {
    const { db, calls } = fakeDb()
    const k = new Keeper('a', 'me', db, vi.fn())
    k.write(snap('one'))
    await k.idle()
    k.write(null)
    await k.idle()
    expect(calls.map((c) => c[1]?.text ?? null)).toEqual(['one', null])
  })

  it('drops the adopted record with its first write only', async () => {
    const { db, calls } = fakeDb()
    const k = new Keeper('a', 'me', db, vi.fn())
    k.adopt(['old', 'older'])
    k.write(snap('one'))
    await k.idle()
    k.write(snap('two'))
    await k.idle()
    expect(calls.map((c) => c[2])).toEqual([['old', 'older'], []])
  })

  it('reports a failed write, keeps the adopted record, and goes on', async () => {
    const calls: (readonly string[] | undefined)[] = []
    let n = 0
    const db: Db = {
      write: (_i, _o, _r, drop) => {
        calls.push(drop)
        return n++ === 0 ? Promise.reject(new Error('quota')) : Promise.resolve()
      },
      all: () => Promise.resolve([]),
    }
    const onError = vi.fn()
    const k = new Keeper('a', 'me', db, onError)
    k.adopt(['old'])
    k.write(snap('one'))
    await k.idle()
    k.write(snap('two'))
    await k.idle()
    expect(onError).toHaveBeenCalledTimes(1)
    expect(calls).toEqual([['old'], ['old']])
  })
})

describe('takeOrphans', () => {
  const dbOf = (recs: Unsaved[]): Db => ({ write: () => Promise.resolve(), all: () => Promise.resolve(recs) })
  const locks = (alive: string[]): Locks => ({
    hold: vi.fn(),
    free: (name) => Promise.resolve(!alive.includes(name)),
  })

  it('takes every record whose tab is gone, oldest first, never a live tab\'s or its own', async () => {
    const recs = [rec('live', 9), rec('me', 8), rec('gone-new', 5), rec('gone-old', 1)]
    const alive = ['live', 'me'].map((o) => `draft-studio-tab-${o}`)
    const got = await takeOrphans(dbOf(recs), locks(alive), 'a', 'me')
    expect(got.map((r) => r.owner)).toEqual(['gone-old', 'gone-new'])
  })

  it('takes none when every other tab is alive or there is nothing', async () => {
    const live = locks(['draft-studio-tab-live'])
    expect(await takeOrphans(dbOf([rec('live', 1)]), live, 'a', 'me')).toEqual([])
    expect(await takeOrphans(dbOf([]), live, 'a', 'me')).toEqual([])
  })

  it('treats every other tab as gone when the browser has no locks', async () => {
    expect((await takeOrphans(dbOf([rec('x', 1)]), noLocks, 'a', 'me')).map((r) => r.owner)).toEqual(['x'])
    await expect(noLocks.free('x')).resolves.toBe(true)
    noLocks.hold('x')
  })
})

describe('makeLocks', () => {
  it('holds a lock for the tab\'s life and asks whether another tab\'s is free', async () => {
    const request = vi.fn((name: string, _opts: unknown, b?: (l: unknown) => unknown) => {
      if (!b) {
        ;(_opts as () => unknown)()
        return new Promise<void>(() => undefined)
      }
      return Promise.resolve(b(name === 'draft-studio-tab-busy' ? null : {}))
    })
    const l = makeLocks({ request } as unknown as LockManager)
    l.hold('draft-studio-tab-me')
    expect(request).toHaveBeenCalledWith('draft-studio-tab-me', expect.any(Function))
    await expect(l.free('draft-studio-tab-busy')).resolves.toBe(false)
    await expect(l.free('draft-studio-tab-gone')).resolves.toBe(true)
    expect(request).toHaveBeenCalledWith('draft-studio-tab-gone', { ifAvailable: true }, expect.any(Function))
    expect(makeLocks(undefined)).toBe(noLocks)
  })
})

describe('baseOf', () => {
  const r = rec('x', 1, { sent: 'sent text', baseVersion: 3, baseText: 'base' })
  it('moves the base to a version that holds what was in flight', () => {
    const versions = [{ v: 3, text: 'base' }, { v: 4, text: 'sent text' }, { v: 5, text: 'other' }]
    expect(baseOf(r, versions)).toEqual({ version: 4, text: 'sent text' })
  })
  it('keeps the base when the flight never landed, or nothing was in flight', () => {
    expect(baseOf(r, [{ v: 3, text: 'sent text' }, { v: 4, text: 'other' }, { text: 'sent text' }])).toEqual({
      version: 3,
      text: 'base',
    })
    expect(baseOf({ ...r, sent: null }, [{ v: 4, text: 'x' }])).toEqual({ version: 3, text: 'base' })
  })
})

describe('startSession', () => {
  const free: Locks = { hold: vi.fn(), free: () => Promise.resolve(true) }

  it('holds its lock, writes its own record and finds none to take', async () => {
    const hold = vi.fn()
    const db = openDb(new IDBFactory())
    const s = startSession('a', { db, locks: { ...free, hold }, owner: 'me', onError: vi.fn() })
    expect(hold).toHaveBeenCalledWith('draft-studio-tab-me')
    expect(await s.orphans()).toEqual([])
    s.keeper.write(snap('typed'))
    await s.keeper.idle()
    const s2 = startSession('a', { db, locks: free, owner: 'next', onError: vi.fn() })
    expect((await s2.orphans()).map((r) => r.text)).toEqual(['typed'])
  })

  it('reports a database that will not open and takes nothing', async () => {
    const onError = vi.fn()
    const db: Db = { write: () => Promise.resolve(), all: () => Promise.reject(new Error('blocked')) }
    const s = startSession('a', { db, locks: noLocks, owner: 'me', onError })
    expect(await s.orphans()).toEqual([])
    expect(onError).toHaveBeenCalledTimes(1)
  })
})
