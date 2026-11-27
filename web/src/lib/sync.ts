/**
 * Merging another tab's saved state into this tab's unsaved edits (the 409
 * rebase and the change feed both land here). Pure, so the rules are unit-tested:
 * - neither tab's edits are lost: the server's changes are rebased over yours;
 * - your typing stays yours; Claude text keeps its tint only where it came from.
 */
import { ChangeSet } from '@codemirror/state'
import { diffChars } from 'diff'
import { applyChanges, type Inserted, normalize, type Range } from './authorship'

/** The changes turning `base` into `next` (character diff: the server only sends texts). */
export function changesBetween(base: string, next: string): ChangeSet {
  const specs: { from: number; to: number; insert: string }[] = []
  let pos = 0
  for (const part of diffChars(base, next)) {
    if (part.added) specs.push({ from: pos, to: pos, insert: part.value })
    else if (part.removed) specs.push({ from: pos, to: pos + part.value.length, insert: '' })
    if (!part.added) pos += part.value.length
  }
  return ChangeSet.of(specs, base.length)
}

const insertedBy = (cs: ChangeSet): Inserted[] => {
  const out: Inserted[] = []
  cs.iterChangedRanges((_fA, _tA, fromB, toB) => {
    if (toB > fromB) out.push({ fromB, toB })
  })
  return out
}

/** Ranges carried through changes made by you (inserted text is yours). */
const through = (ranges: readonly Range[], cs: ChangeSet): Range[] =>
  applyChanges(ranges, insertedBy(cs), (p, a) => cs.mapPos(p, a), false)

const within = (ranges: readonly Range[], spans: readonly Inserted[]): Range[] =>
  spans.flatMap(({ fromB, toB }) =>
    ranges.map(([a, b]) => [Math.max(a, fromB), Math.min(b, toB)] as const).filter(([a, b]) => b > a),
  )

export interface Merge {
  /** Dispatch on this tab's doc: the server's changes rebased over the unsaved local ones. */
  readonly remote: ChangeSet
  /** The unsaved local changes on top of the server text: what the next save carries. */
  readonly local: ChangeSet
  /** Claude ranges of the merged text. */
  readonly ranges: Range[]
}

/**
 * `base`: the text both sides grew from; `local`: this tab's unsaved changes on it;
 * `mine`: this tab's Claude ranges; `server`: the newer saved state.
 */
export function merge(
  base: string,
  local: ChangeSet,
  mine: readonly Range[],
  server: { readonly text: string; readonly claude: readonly Range[] },
): Merge {
  const theirs = changesBetween(base, server.text)
  if (local.empty) return { remote: theirs, local: ChangeSet.empty(server.text.length), ranges: normalize(server.claude) }
  // local then remote == theirs then rebased local (CodeMirror's map contract).
  const remote = theirs.map(local)
  const rebased = local.map(theirs, true)
  const spans = insertedBy(remote)
  const ranges = normalize([...through(mine, remote), ...within(through(server.claude, rebased), spans)])
  return { remote, local: rebased, ranges }
}
