import { diffWordsWithSpace } from 'diff'

/** One accept/reject unit: replace document [from, to) with insert. */
export interface Hunk {
  readonly from: number
  readonly to: number
  readonly insert: string
}

/** Word-level hunks turning oldText into newText, positioned at offset in the doc. */
export function wordHunks(oldText: string, newText: string, offset: number): Hunk[] {
  const hunks: Hunk[] = []
  let pos = offset
  let open: { from: number; to: number; insert: string } | null = null
  for (const part of diffWordsWithSpace(oldText, newText)) {
    if (!part.added && !part.removed) {
      if (open) hunks.push(open)
      open = null
      pos += part.value.length
      continue
    }
    open ??= { from: pos, to: pos, insert: '' }
    if (part.removed) {
      pos += part.value.length
      open.to = pos
    } else {
      open.insert += part.value
    }
  }
  if (open) hunks.push(open)
  return hunks
}

/** Apply hunks (any subset, any order) to a document string. */
export function applyHunks(doc: string, hunks: readonly Hunk[]): string {
  let out = doc
  for (const h of hunks.toSorted((a, b) => b.from - a.from)) {
    out = out.slice(0, h.from) + h.insert + out.slice(h.to)
  }
  return out
}
