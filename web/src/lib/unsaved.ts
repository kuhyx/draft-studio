/**
 * Unsaved edits survive a reload, a crash or a closed tab (offline-first): the editor mirrors
 * what the server has not acknowledged into IndexedDB, one record per (draft, tab). A page that
 * opens the draft takes the records of tabs that are gone (Web Locks tell: a live tab
 * holds `draft-studio-tab-<owner>`), replays them, oldest first, over the server text through the 409 rebase
 * merge (editorKeep.ts); the records go once this tab's own is written, and that one when the server acknowledges.
 */
import { z } from 'zod'
import { stateSchema } from './api'

const rangeSchema = z.tuple([z.number().int(), z.number().int()])
const authorSchema = z.enum(['you', 'claude'])
/** `baseVersion`/`baseText`: the saved state the text grew from; `sent`: the text a save in flight carries. */
const unsavedSchema = z.object({
  id: z.string(),
  owner: z.string(),
  at: z.number(),
  baseVersion: z.number().int(),
  baseText: z.string(),
  sent: z.string().nullable(),
  text: z.string(),
  claude: z.array(rangeSchema),
  /** The mail fields as typed, with who wrote them. */
  to: z.string(),
  subject: z.string(),
  toAuthor: authorSchema,
  subjectAuthor: authorSchema,
  /** The saved state the tab knew: what a reload can open on while the server is unreachable. */
  state: stateSchema,
})
export type Unsaved = z.infer<typeof unsavedSchema>
export type Snapshot = Omit<Unsaved, 'id' | 'owner' | 'at'>

export interface Db {
  /** Put `rec` (or delete the tab's record when null); `drop`: other tabs' records to delete with it. */
  write: (id: string, owner: string, rec: Unsaved | null, drop?: readonly string[]) => Promise<void>
  all: (id: string) => Promise<Unsaved[]>
}

const STORE = 'unsaved'
const once = <T>(r: IDBRequest<T>): Promise<T> =>
  new Promise((ok, no) => {
    r.onsuccess = () => {
      ok(r.result)
    }
    r.onerror = () => {
      no(new Error(String(r.error)))
    }
  })

export function openDb(factory: IDBFactory = indexedDB): Db {
  let opened: Promise<IDBDatabase> | undefined
  const open = (): Promise<IDBDatabase> => {
    const req = factory.open('draft-studio', 1)
    req.onupgradeneeded = () => {
      req.result.createObjectStore(STORE, { keyPath: ['id', 'owner'] })
    }
    return once(req)
  }
  const db = () => (opened ??= open())
  return {
    write: async (id, owner, rec, drop) => {
      const tx = (await db()).transaction(STORE, 'readwrite')
      const store = tx.objectStore(STORE)
      if (rec) store.put(rec)
      else store.delete([id, owner])
      for (const o of drop ?? []) store.delete([id, o])
      await new Promise<void>((ok, no) => {
        tx.oncomplete = () => {
          ok()
        }
        tx.onerror = tx.onabort = () => {
          no(new Error(String(tx.error)))
        }
      })
    },
    all: async (id) => {
      const store = (await db()).transaction(STORE).objectStore(STORE)
      // [id, <any string>] sorts below [id, []]: arrays come after strings in key order.
      const rows: unknown[] = await once(store.getAll(IDBKeyRange.bound([id], [id, []])))
      return rows.flatMap((r) => {
        const p = unsavedSchema.safeParse(r)
        return p.success ? [p.data] : []
      })
    },
  }
}

/** Writes in order, newest snapshot wins: a burst of keystrokes costs one write. A failure is reported, never fatal. */
export class Keeper {
  private last: Snapshot | null | undefined
  private queue: Promise<void> = Promise.resolve()
  private drop: readonly string[] = []

  constructor(
    readonly id: string,
    readonly owner: string,
    private readonly db: Db,
    private readonly onError: (err: unknown) => void,
    private readonly now: () => number = Date.now,
  ) {}

  /** The records of these tabs are replayed here: the next write that lands replaces them. */
  adopt(owners: readonly string[]): void {
    this.drop = owners
  }

  /** `null`: nothing is unsaved. */
  write(snap: Snapshot | null): void {
    this.last = snap
    this.queue = this.queue.then(() => this.drain())
  }

  idle(): Promise<void> {
    return this.queue
  }

  private async drain(): Promise<void> {
    if (this.last === undefined) return
    const snap = this.last
    this.last = undefined
    const rec = snap && { ...snap, id: this.id, owner: this.owner, at: this.now() }
    try {
      await this.db.write(this.id, this.owner, rec, this.drop)
      this.drop = []
    } catch (err) {
      this.onError(err)
    }
  }
}

export interface Locks {
  /** Hold this tab's lock for as long as the tab lives. */
  hold: (name: string) => void
  /** Nobody holds `name`: the tab that owned it is gone. */
  free: (name: string) => Promise<boolean>
}

/** No Web Locks: no way to tell a live tab from a gone one, so every other tab counts as gone. */
export const noLocks: Locks = { hold: () => undefined, free: () => Promise.resolve(true) }

export function makeLocks(manager: LockManager | undefined): Locks {
  if (!manager) return noLocks
  return {
    hold: (name) => {
      void manager.request(name, () => new Promise<void>(() => undefined))
    },
    free: (name) => manager.request(name, { ifAvailable: true }, (lock) => lock !== null),
  }
}

const lockName = (owner: string) => `draft-studio-tab-${owner}`

/** Every record of this draft whose tab is gone (never this tab's own), oldest first. */
export async function takeOrphans(db: Db, locks: Locks, id: string, self: string): Promise<Unsaved[]> {
  const recs = (await db.all(id)).filter((r) => r.owner !== self).sort((a, b) => a.at - b.at)
  const gone = await Promise.all(recs.map((r) => locks.free(lockName(r.owner))))
  return recs.filter((_, i) => gone[i])
}

/**
 * The base a record's edits grew from. A save that was in flight when the tab died may
 * have landed: its version then holds `sent`, and only the edits after it are unsaved.
 */
export function baseOf(
  rec: Unsaved,
  versions: readonly { v?: number | undefined; text?: string | undefined }[],
): { version: number; text: string } {
  for (const h of versions.toReversed()) {
    if (rec.sent !== null && h.v !== undefined && h.v > rec.baseVersion && h.text === rec.sent) {
      return { version: h.v, text: rec.sent }
    }
  }
  return { version: rec.baseVersion, text: rec.baseText }
}

export interface SessionDeps {
  db: Db
  locks: Locks
  owner: string
  onError: (err: unknown) => void
}

export interface Session {
  keeper: Keeper
  db: Db
  /** Unsaved edits of this draft left by tabs that are gone, oldest first. */
  orphans: () => Promise<Unsaved[]>
}

/** This tab's side of the store for draft `id`. */
export function startSession(id: string, deps: SessionDeps): Session {
  const { db, locks, owner, onError } = deps
  locks.hold(lockName(owner))
  return {
    keeper: new Keeper(id, owner, db, onError),
    db,
    orphans: () =>
      takeOrphans(db, locks, id, owner).catch((err: unknown) => {
        onError(err)
        return []
      }),
  }
}
