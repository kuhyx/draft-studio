/**
 * CodeMirror state for the draft: who wrote what, and Claude's pending proposals.
 * Pure state (no view), so the ownership rules are unit-testable.
 */
import { invertedEffects } from '@codemirror/commands'
import {
  Annotation,
  type ChangeDesc,
  type ChangeSet,
  type EditorState,
  type Extension,
  Facet,
  StateEffect,
  StateField,
  Transaction,
  type TransactionSpec,
} from '@codemirror/state'
import { applyChanges, normalize, type Range } from './authorship'
import { applyHunks, type Hunk, wordHunks } from './diff'

/** Marks a transaction as Claude text accepted by you. */
export const claudeEdit = Annotation.define<boolean>()

interface AuthorshipValue {
  readonly ranges: readonly Range[]
  readonly rev: number
}
/**
 * Replace the authorship snapshot; undo steps map through another tab's changes too.
 * A snapshot may reach past the mapped doc (it was taken before its own step): clamp first.
 */
const mapEnd = (m: ChangeDesc, pos: number, assoc: number) => m.mapPos(Math.min(pos, m.length), assoc)
export const setAuthorship = StateEffect.define<AuthorshipValue>({
  map: (v, m) => ({ ...v, ranges: v.ranges.map(([a, b]) => [mapEnd(m, a, 1), mapEnd(m, b, -1)] as const) }),
})
/** Another tab's saved changes, rebased over this tab's unsaved ones (sync.ts): not an edit here. */
export const remoteEdit = Annotation.define<boolean>()
/** loadDoc: the doc now is the server state the page holds; nothing is unsaved. */
export const serverLoad = Annotation.define<boolean>()

/** Claude-written ranges; the rest of the doc is yours. */
export const authorship = StateField.define<AuthorshipValue>({
  create: () => ({ ranges: [], rev: 0 }),
  update(value, tr) {
    const sets = tr.effects.filter((e) => e.is(setAuthorship)).map((e) => e.value)
    if (sets.length > 0) {
      // An undone history group carries one snapshot per step: the oldest wins.
      const oldest = sets.reduce((a, b) => (b.rev < a.rev ? b : a))
      const len = tr.newDoc.length
      const clamped = oldest.ranges.map(([a, b]) => [Math.min(a, len), Math.min(b, len)] as const)
      return { ranges: normalize(clamped), rev: value.rev + 1 }
    }
    if (!tr.docChanged) return value
    const inserted: { fromB: number; toB: number }[] = []
    tr.changes.iterChangedRanges((_fA, _tA, fromB, toB) => inserted.push({ fromB, toB }))
    const byClaude = tr.annotation(claudeEdit) === true
    return {
      ranges: applyChanges(value.ranges, inserted, (p, a) => tr.changes.mapPos(p, a), byClaude),
      rev: value.rev + 1,
    }
  },
})

/** Undo/redo also restores tinting: each step records the ranges before it. */
const authorshipHistory = invertedEffects.of((tr) =>
  tr.docChanged || tr.effects.some((e) => e.is(setAuthorship))
    ? [setAuthorship.of(tr.startState.field(authorship))]
    : [],
)

/** Claude's pending proposals as the editor shows them (the server holds them: proposals.py). */
export interface Proposal {
  readonly id: number
  /** Moves whenever the server changes the proposal; a resolve carries it (409 when it moved on). */
  readonly rev: number
  readonly from: number
  readonly to: number
  readonly hunks: readonly Hunk[]
}
interface Pending {
  readonly list: readonly Proposal[]
}
const setPending = StateEffect.define<Pending>()

const mapHunk = (h: Hunk, m: ChangeDesc): Hunk => ({
  from: m.mapPos(h.from, 1),
  to: Math.max(m.mapPos(h.to, -1), m.mapPos(h.from, 1)),
  insert: h.insert,
})
const mapProposal = (p: Proposal, m: ChangeDesc): Proposal => ({
  ...p,
  from: m.mapPos(p.from, -1),
  to: m.mapPos(p.to, 1),
  hunks: p.hunks.map((h) => mapHunk(h, m)),
})
const overlaps = (p: Proposal, from: number, to: number) => from < p.to && p.from < to

/** Claude's pending proposals, each for its own span; spans never overlap. */
export const proposals = StateField.define<Pending>({
  create: () => ({ list: [] }),
  update(value, tr) {
    for (const e of tr.effects) if (e.is(setPending)) return e.value
    if (!tr.docChanged || value.list.length === 0) return value
    // Your edit touching a span makes that proposal stale: drop it, no merge. The others move along.
    const kept = value.list.filter((p) => !tr.changes.touchesRange(p.from, p.to))
    return { list: kept.map((p) => mapProposal(p, tr.changes)) }
  },
})

export const proposalsOf = (state: EditorState): readonly Proposal[] => state.field(proposals).list

export const authorshipExtensions: Extension = [authorship, authorshipHistory, proposals]

/** Accept or reject was clicked: `which` hunks (the page asks the server, editorResolve.ts). */
export type Resolver = (action: 'accept' | 'reject', which: HunkPick) => void
export const resolver = Facet.define<Resolver, Resolver>({ combine: (all) => all[0] ?? (() => undefined) })

/** Replace the whole doc (load, restore, first draft) with its authorship; no proposal until the server's arrive. */
export function loadDoc(
  state: EditorState,
  text: string,
  claude: readonly Range[],
  addToHistory: boolean,
): TransactionSpec {
  return {
    changes: { from: 0, to: state.doc.length, insert: text },
    effects: [setAuthorship.of({ ranges: claude, rev: state.field(authorship).rev }), setPending.of({ list: [] })],
    annotations: [Transaction.addToHistory.of(addToHistory), serverLoad.of(true)],
  }
}

/** A pending proposal as the server holds it: `from`/`to` on its text, `replacement` for that span. */
export interface ServerProposal {
  readonly id: number
  readonly rev: number
  readonly from: number
  readonly to: number
  readonly replacement: string
}

/**
 * Show the server's proposals on this doc. `local`: this tab's unsaved changes on top of the text the
 * server's positions refer to; an unsaved edit touching a span drops that proposal here, as typing does.
 */
export function syncProposals(state: EditorState, server: readonly ServerProposal[], local: ChangeSet): TransactionSpec {
  const list = server.flatMap((p): Proposal[] => {
    if (p.to > local.length || local.touchesRange(p.from, p.to)) return []
    const from = local.mapPos(p.from, -1)
    const to = local.mapPos(p.to, 1)
    const hunks = wordHunks(state.sliceDoc(from, to), p.replacement, from)
    return hunks.length > 0 ? [{ id: p.id, rev: p.rev, from, to, hunks }] : []
  })
  return JSON.stringify(list) === JSON.stringify(proposalsOf(state)) ? {} : { effects: setPending.of({ list }) }
}

/** The doc text of [from, to) with `hunks` (document positions) applied. */
function appliedTo(state: EditorState, from: number, to: number, hunks: readonly Hunk[]): string {
  return applyHunks(state.sliceDoc(from, to), hunks.map((h) => ({ ...h, from: h.from - from, to: h.to - from })))
}

export interface ReviseTarget {
  readonly from: number
  readonly to: number
  readonly ids: readonly number[]
  /** The pending replacement Claude proposed for [from, to). */
  readonly previous: string
}

/** A selection touching pending proposals comments on them: the span widens to the union. */
export function reviseTarget(state: EditorState, from: number, to: number): ReviseTarget | null {
  const hit = proposalsOf(state).filter((p) => overlaps(p, from, to))
  if (hit.length === 0) return null
  const a = Math.min(from, ...hit.map((p) => p.from))
  const b = Math.max(to, ...hit.map((p) => p.to))
  return { from: a, to: b, ids: hit.map((p) => p.id), previous: appliedTo(state, a, b, hit.flatMap((p) => p.hunks)) }
}

/** Every hunk of every proposal, or some hunks of one. */
export type HunkPick = 'all' | { readonly id: number; readonly hunks: readonly number[] }

function pick(list: readonly Proposal[], which: HunkPick): Map<number, readonly Hunk[]> {
  const chosen = new Map<number, readonly Hunk[]>()
  for (const p of list) {
    if (which === 'all') chosen.set(p.id, p.hunks)
    else if (p.id === which.id) chosen.set(p.id, p.hunks.filter((_h, i) => which.hunks.includes(i)))
  }
  return chosen
}

/** One call to the server: proposal `id` at `rev`; `text`: what its span becomes when only some hunks go through. */
export interface Resolution {
  readonly id: number
  readonly rev: number
  readonly text?: string
}

/**
 * What to ask the server for when `which` hunks are accepted or rejected. A proposal with all its hunks
 * picked is resolved whole. With some picked, `text` is the span with the hunks that go through applied:
 * the accepted ones (accept) or the ones left over (reject).
 */
export function resolutions(state: EditorState, action: 'accept' | 'reject', which: HunkPick): Resolution[] {
  const chosen = pick(proposalsOf(state), which)
  return proposalsOf(state).flatMap((p): Resolution[] => {
    const picked = chosen.get(p.id) ?? []
    if (picked.length === 0) return []
    if (picked.length === p.hunks.length) return [{ id: p.id, rev: p.rev }]
    const through = action === 'accept' ? picked : p.hunks.filter((h) => !picked.includes(h))
    return [{ id: p.id, rev: p.rev, text: appliedTo(state, p.from, p.to, through) }]
  })
}
