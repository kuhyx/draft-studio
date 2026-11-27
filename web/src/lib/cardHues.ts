/** The permission cards' colours and their spans in the text (editorCard.ts): one hue per hold, kept while it lives. */
import type { Hold } from './editorCore'
import type { HeldSpan } from './heldSpans'
import { HUES, nextHue } from './permCard'

export type HuedHold = Hold & { readonly hue: string }

/** Every hold with its colour; `hues` (hold id -> index) forgets gone holds and gives new ones the lowest free one. */
export function hued(holds: ReadonlyMap<string, Hold>, hues: Map<string, number>): HuedHold[] {
  for (const id of hues.keys()) if (!holds.has(id)) hues.delete(id)
  return [...holds.values()].map((h) => {
    let i = hues.get(h.held.id)
    if (i === undefined) {
      i = nextHue([...hues.values()])
      hues.set(h.held.id, i)
    }
    return { ...h, hue: HUES[i % HUES.length] }
  })
}

/** The held spans to mark: a stale pending proposal has none. */
export function holdSpans(holds: readonly HuedHold[], lit: string | null): HeldSpan[] {
  return holds.flatMap((h): HeldSpan[] => {
    const at = h.mine ? (h.mine.pending.stale ? null : h.mine.pending) : h.held
    if (at?.from == null || at.to == null) return []
    return [{ id: h.held.id, from: at.from, to: at.to, hue: h.hue, lit: lit === h.held.id, failed: h.held.failed.length > 0 }]
  })
}
