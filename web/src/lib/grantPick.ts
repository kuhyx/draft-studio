/**
 * Per-request grant (kuhy approved the two-buttons-per-row mockup, 2026-11-27): a card's `blocked`
 * (method, url) pairs become one row each that kuhy allows or denies; send stays off until every row
 * is decided (no silent default). The call: some allowed -> partial grant with exactly those pairs
 * (`POST /api/captures/<rid>/grant {"allow"}`), all allowed -> the whole card, all denied -> deny.
 * Pure: the DOM is grantRows.ts, the clicks editorCard.ts.
 */
import type { CaptureRequest } from './api'

/** A row's decision: allowed, denied, or not yet (undefined). */
export type Pick = 'a' | 'd' | undefined
export type Pair = readonly [string, string]

export interface GrantRow {
  /** What the row shows: an OPTIONS preflight that announces a write shows as that write. */
  readonly method: string
  readonly url: string
  /** What the server blocked, sent back verbatim (an OPTIONS row stays OPTIONS). */
  readonly pair: Pair
  readonly preflight: boolean
}

const isPair = (p: unknown): p is Pair =>
  Array.isArray(p) && p.length === 2 && typeof p[0] === 'string' && typeof p[1] === 'string'

/** The card's rows; none for a click refusal (no blocked pairs: the card keeps its single deny/allow). */
export function grantRows(r: CaptureRequest): GrantRow[] {
  const blocked: unknown = r.blocked
  if (!Array.isArray(blocked) || !blocked.every(isPair)) return []
  const raw: unknown = r.announces
  const announces: readonly unknown[] = Array.isArray(raw) ? raw : []
  return blocked.map((pair, i) => {
    const said = announces[i]
    const preflight = pair[0] === 'OPTIONS' && typeof said === 'string' && said !== ''
    return { method: preflight ? said.toUpperCase() : pair[0], url: pair[1], pair, preflight }
  })
}

const sized = (picks: readonly Pick[], n: number): Pick[] => Array.from({ length: n }, (_, i) => picks[i])

/** Decide row `i` as `side`; the same side again undecides it. */
export function toggle(picks: readonly Pick[], n: number, i: number, side: 'a' | 'd'): Pick[] {
  const out = sized(picks, n)
  out[i] = out[i] === side ? undefined : side
  return out
}

/** Decide every undecided row as `side`; decided rows keep theirs. */
export function rest(picks: readonly Pick[], n: number, side: 'a' | 'd'): Pick[] {
  return sized(picks, n).map((p) => p ?? side)
}

export function decided(picks: readonly Pick[], n: number): boolean {
  return n > 0 && sized(picks, n).every((p) => p !== undefined)
}

export type GrantCall = { route: 'deny' } | { route: 'grant'; allow?: Pair[] }

/** What send posts, or null while a row is undecided. */
export function grantCall(rows: readonly GrantRow[], picks: readonly Pick[]): GrantCall | null {
  if (!decided(picks, rows.length)) return null
  const allow = rows.filter((_, i) => picks[i] === 'a').map((r) => r.pair)
  if (allow.length === 0) return { route: 'deny' }
  return allow.length === rows.length ? { route: 'grant' } : { route: 'grant', allow }
}
