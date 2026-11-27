/**
 * Held answers: a proposal or first draft the server keeps back while one of
 * Claude's screenshots waits on kuhy's permission, a login or a busy capture
 * profile (shapes in draft_studio/capture/__init__.py). The page polls
 * GET /held with a 1 s -> 15 s backoff, never while hidden, and shows what
 * the server released (the proposal itself already lives on the server, proposals.py) and collects it.
 */
import { ApiError, type HeldListing } from './api'

export const MIN_MS = 1000
export const MAX_MS = 15_000

export interface PollerDeps {
  list: () => Promise<HeldListing>
  onListing: (listing: HeldListing) => Promise<void> | undefined
  /** A failed poll; polling backs off and goes on either way. */
  onError: (err: unknown) => void
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

  /**
   * One GET /held; a hidden page waits for visibilitychange or focus instead, unless `force`d (the
   * change feed said another tab moved something: the view must agree before it is shown).
   */
  async poll(force = false): Promise<void> {
    clearTimeout(this.timer)
    if (this.running) {
      this.again = true
      return
    }
    if (this.deps.doc.visibilityState === 'hidden' && !force) return
    this.running = true
    try {
      const listing = await this.deps.list()
      this.active = listing.held.length > 0
      await this.deps.onListing(listing)
    } catch (err) {
      this.deps.onError(err)
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

/** Whether the answer lost a made-up image link or a screenshot line: the span pulses `stripped`. */
export const lostSome = (a: { readonly stripped: readonly string[]; readonly dropped: readonly unknown[] }): boolean =>
  a.stripped.length > 0 || a.dropped.length > 0

/** Whether a failed poll shows on the status icon: being offline a moment is not worth it. */
export const loud = (err: unknown): boolean => !(err instanceof ApiError && err.code === 'offline')

/** Holds this page tracks that the server neither holds nor released (expired, or collected elsewhere). */
export function forgotten(listing: HeldListing, mine: Iterable<string>): string[] {
  const known = new Set([...listing.held, ...listing.released].map((h) => h.id))
  return [...mine].filter((id) => !known.has(id))
}
