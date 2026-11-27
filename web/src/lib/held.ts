/**
 * Held answers: a proposal or first draft the server keeps back while one of
 * Claude's screenshots waits on kuhy's permission, a login or a busy capture
 * profile (shapes in draft_studio/capture/__init__.py). The page polls
 * GET /held with a 1 s -> 15 s backoff, never while hidden, and lands each
 * released answer once (editorAsks.ts, editorPage.ts), then collects it.
 */
import type { HeldListing, ReleasedProposal } from './api'

export const MIN_MS = 1000
export const MAX_MS = 15_000

export interface PollerDeps {
  list: () => Promise<HeldListing>
  onListing: (listing: HeldListing) => Promise<void> | undefined
  doc: EventTarget & { readonly visibilityState: DocumentVisibilityState }
  win: EventTarget
}

export class HeldPoller {
  private delay = MIN_MS
  private timer: ReturnType<typeof setTimeout> | undefined
  /** Something is (or may be) held: keep polling. */
  private active = false
  private running = false
  /** A poke arrived while a request ran: poll again right after it. */
  private again = false

  constructor(private readonly deps: PollerDeps) {
    deps.doc.addEventListener('visibilitychange', () => {
      if (deps.doc.visibilityState === 'visible') this.resume()
    })
    deps.win.addEventListener('focus', () => {
      this.resume()
    })
  }

  /** A hold just started: first poll in 1 s. */
  watch(): void {
    this.active = true
    this.delay = MIN_MS
    this.schedule()
  }

  /** Poll now and restart the backoff (a grant, a deny, a login retry). */
  poke(): void {
    this.active = true
    this.delay = MIN_MS
    void this.poll()
  }

  /** One GET /held; a hidden page waits for visibilitychange or focus instead. */
  async poll(): Promise<void> {
    clearTimeout(this.timer)
    if (this.running) {
      this.again = true
      return
    }
    if (this.deps.doc.visibilityState === 'hidden') return
    this.running = true
    try {
      const listing = await this.deps.list()
      this.active = listing.held.length > 0
      await this.deps.onListing(listing)
    } catch {
      // Offline or a bad reply: keep the hold and try again later.
    } finally {
      this.running = false
    }
    if (this.again) {
      this.again = false
      await this.poll()
    } else if (this.active) this.schedule()
  }

  private resume(): void {
    if (this.active) this.poke()
  }

  private schedule(): void {
    clearTimeout(this.timer)
    this.timer = setTimeout(() => void this.poll(), this.delay)
    this.delay = Math.min(this.delay * 2, MAX_MS)
  }
}

/** A proposal this page asked for: its span mapped through kuhy's edits, and what it revises. */
export interface Mine {
  readonly pending: { readonly from: number; readonly to: number; readonly stale: boolean }
  readonly ids?: readonly number[]
}

export interface Landing {
  readonly from: number
  readonly to: number
  readonly ids: readonly number[]
}

/**
 * Where a released proposal lands, or null when kuhy's edit made it stale (a
 * silent drop: his edit wins). Asked in this page: the mapped span. After a
 * reload (or from another tab) the old proposals are gone, so it lands as a
 * fresh proposal, and only while the doc still holds the span it was asked on.
 */
export function landing(rel: ReleasedProposal, mine: Mine | undefined, doc: string): Landing | null {
  if (mine) return mine.pending.stale ? null : { from: mine.pending.from, to: mine.pending.to, ids: mine.ids ?? [] }
  return doc.slice(rel.from, rel.to) === rel.span && rel.to <= doc.length ? { from: rel.from, to: rel.to, ids: [] } : null
}

/** Whether the answer lost a made-up image link or a screenshot line: the span pulses `stripped`. */
export const lostSome = (a: { readonly stripped: readonly string[]; readonly dropped: readonly unknown[] }): boolean =>
  a.stripped.length > 0 || a.dropped.length > 0

/** Holds this page tracks that the server neither holds nor released (expired, or collected elsewhere). */
export function forgotten(listing: HeldListing, mine: Iterable<string>): string[] {
  const known = new Set([...listing.held, ...listing.released].map((h) => h.id))
  return [...mine].filter((id) => !known.has(id))
}
