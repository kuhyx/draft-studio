/**
 * The editor's per-request grant decisions (grantPick.ts rules, grantRows.ts DOM): kept per request
 * id across repaints, dropped once the request leaves the listing. editorCard.ts owns the sending.
 */
import type { CaptureRequest } from './api'
import { grantCall, grantRows, rest, toggle, type GrantCall, type Pick } from './grantPick'

const SIDES: Readonly<Partial<Record<string, readonly ['one' | 'rest', 'a' | 'd']>>> = {
  'pick-allow': ['one', 'a'],
  'pick-deny': ['one', 'd'],
  'allow-rest': ['rest', 'a'],
  'deny-rest': ['rest', 'd'],
}

export class Picks {
  private readonly map = new Map<string, Pick[]>()

  get view(): ReadonlyMap<string, readonly Pick[]> {
    return this.map
  }

  /** A stable text of every decision, for the repaint check. */
  key(): string {
    return JSON.stringify([...this.map])
  }

  /** Forget requests that are no longer listed. */
  prune(live: ReadonlySet<string>): void {
    for (const rid of [...this.map.keys()]) if (!live.has(rid)) this.map.delete(rid)
  }

  /** Apply a pick/rest act on request `r` (row `i` for a pick); false when `act` is not one. */
  act(r: CaptureRequest | undefined, act: string | undefined, i: number): boolean {
    const how = SIDES[act ?? '']
    if (!r || !how) return false
    const n = grantRows(r).length
    const now = this.map.get(r.id) ?? []
    this.map.set(r.id, how[0] === 'one' ? toggle(now, n, i, how[1]) : rest(now, n, how[1]))
    return true
  }

  /** What send posts for `r`, or null while a row is undecided. */
  call(r: CaptureRequest | undefined): GrantCall | null {
    return r ? grantCall(grantRows(r), this.map.get(r.id) ?? []) : null
  }
}
