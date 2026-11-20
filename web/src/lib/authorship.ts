/**
 * Authorship of draft text as a list of Claude-written ranges [from, to).
 * Everything outside these ranges is yours. Rules (kuhy's ownership spec):
 * - text you type, paste or edit is always yours, even inside Claude text;
 * - only an accepted Claude proposal adds Claude ranges;
 * - Claude touching text around yours never retags yours.
 */
export type Range = readonly [number, number]
export type MapPos = (pos: number, assoc: number) => number
export interface Inserted {
  readonly fromB: number
  readonly toB: number
}

export function normalize(ranges: readonly Range[]): Range[] {
  const sorted = ranges.filter(([a, b]) => b > a).toSorted((x, y) => x[0] - y[0])
  const out: [number, number][] = []
  for (const [a, b] of sorted) {
    const last = out.at(-1)
    if (last && a <= last[1]) last[1] = Math.max(last[1], b)
    else out.push([a, b])
  }
  return out
}

export function subtractRange(ranges: readonly Range[], from: number, to: number): Range[] {
  if (to <= from) return [...ranges]
  const out: Range[] = []
  for (const [a, b] of ranges) {
    if (b <= from || a >= to) {
      out.push([a, b])
      continue
    }
    if (a < from) out.push([a, from])
    if (b > to) out.push([to, b])
  }
  return normalize(out)
}

export function addRange(ranges: readonly Range[], from: number, to: number): Range[] {
  return normalize([...ranges, [from, to]])
}

/**
 * Carry ranges through an edit. Range ends map inward (from sticks right,
 * to sticks left) so typing at a boundary never widens Claude text; then each
 * inserted span is cut out (your edit) or added (an accepted Claude hunk).
 */
export function applyChanges(
  ranges: readonly Range[],
  inserted: readonly Inserted[],
  mapPos: MapPos,
  byClaude: boolean,
): Range[] {
  let out = normalize(ranges.map(([a, b]) => [mapPos(a, 1), mapPos(b, -1)] as const))
  for (const { fromB, toB } of inserted) {
    out = byClaude ? addRange(out, fromB, toB) : subtractRange(out, fromB, toB)
  }
  return out
}
