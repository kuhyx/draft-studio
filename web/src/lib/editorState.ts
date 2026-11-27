/**
 * CodeMirror state for the draft: who wrote what, and Claude's pending proposals.
 * Pure state (no view), so the ownership rules are unit-testable.
 */
import { invertedEffects } from '@codemirror/commands'
import {
  Annotation,
  type ChangeDesc,
  type EditorState,
  type Extension,
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

export interface Proposal {
  /** Stable while pending; a revision keeps the id of what it replaces. */
  readonly id: number
  readonly from: number
  readonly to: number
  readonly hunks: readonly Hunk[]
}
interface Pending {
  readonly list: readonly Proposal[]
  /** Next fresh id: never reused, so a late revision cannot hit a newer proposal. */
  readonly seq: number
}
const setPending = StateEffect.define<Pending>()
const ownProposalEdit = Annotation.define<boolean>()

const mapHunk = (h: Hunk, m: ChangeDesc): Hunk => ({
  from: m.mapPos(h.from, 1),
  to: Math.max(m.mapPos(h.to, -1), m.mapPos(h.from, 1)),
  insert: h.insert,
})
const mapProposal = (p: Proposal, m: ChangeDesc, hunks = p.hunks): Proposal => ({
  ...p,
  from: m.mapPos(p.from, -1),
  to: m.mapPos(p.to, 1),
  hunks: hunks.map((h) => mapHunk(h, m)),
})
const overlaps = (p: Proposal, from: number, to: number) => from < p.to && p.from < to

/** Claude's pending proposals, each for its own span; spans never overlap. */
export const proposals = StateField.define<Pending>({
  create: () => ({ list: [], seq: 1 }),
  update(value, tr) {
    for (const e of tr.effects) if (e.is(setPending)) return e.value
    if (!tr.docChanged || value.list.length === 0) return value
    // Your edit touching a span makes that proposal stale: drop it, no merge. The others move along.
    const own = tr.annotation(ownProposalEdit) === true
    const kept = value.list.filter((p) => own || !tr.changes.touchesRange(p.from, p.to))
    return { list: kept.map((p) => mapProposal(p, tr.changes)), seq: value.seq }
  },
})

export const proposalsOf = (state: EditorState): readonly Proposal[] => state.field(proposals).list

export const authorshipExtensions: Extension = [authorship, authorshipHistory, proposals]

/** Replace the whole doc (load, restore, first draft) with its authorship. */
export function loadDoc(
  state: EditorState,
  text: string,
  claude: readonly Range[],
  addToHistory: boolean,
): TransactionSpec {
  return {
    changes: { from: 0, to: state.doc.length, insert: text },
    effects: [
      setAuthorship.of({ ranges: claude, rev: state.field(authorship).rev }),
      setPending.of({ list: [], seq: state.field(proposals).seq }),
    ],
    annotations: [Transaction.addToHistory.of(addToHistory), serverLoad.of(true)],
  }
}

/**
 * Whether Claude's answer for [from, to) can land: `gone` when a proposal it
 * revises is no longer pending (kuhy rejected it meanwhile), `overlap` when it
 * overlaps another pending proposal. The editor signals both drops.
 */
export function proposalOutcome(
  state: EditorState,
  from: number,
  to: number,
  replaces: readonly number[] = [],
): 'ok' | 'overlap' | 'gone' {
  const { list } = state.field(proposals)
  if (!replaces.every((id) => list.some((p) => p.id === id))) return 'gone'
  return list.some((p) => !replaces.includes(p.id) && overlaps(p, from, to)) ? 'overlap' : 'ok'
}

/**
 * Turn Claude's replacement for [from, to) into a pending word-diff proposal.
 * `replaces`: the proposals this answer revises (all must still be pending).
 * An answer that cannot land (proposalOutcome) is dropped.
 */
export function setProposalFor(
  state: EditorState,
  from: number,
  to: number,
  replacement: string,
  replaces: readonly number[] = [],
): TransactionSpec {
  if (proposalOutcome(state, from, to, replaces) !== 'ok') return {}
  const { list, seq } = state.field(proposals)
  const others = list.filter((p) => !replaces.includes(p.id))
  const hunks = wordHunks(state.sliceDoc(from, to), replacement, from)
  const id = replaces[0] ?? seq
  const added = hunks.length > 0 ? [{ id, from, to, hunks }] : []
  return { effects: setPending.of({ list: [...others, ...added].toSorted((a, b) => a.from - b.from), seq: seq + 1 }) }
}

/** What Claude proposed for [from, to): the doc text there with the proposals' hunks applied. */
function proposedText(state: EditorState, from: number, to: number, ps: readonly Proposal[]): string {
  const shifted = ps.flatMap((p) => p.hunks).map((h) => ({ ...h, from: h.from - from, to: h.to - from }))
  return applyHunks(state.sliceDoc(from, to), shifted)
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
  return { from: a, to: b, ids: hit.map((p) => p.id), previous: proposedText(state, a, b, hit) }
}

/** Every hunk of every proposal, or some hunks of one. */
export type HunkPick = 'all' | { readonly id: number; readonly hunks: readonly number[] }

function pick(list: readonly Proposal[], which: HunkPick): Map<number, readonly Hunk[]> | null {
  if (which === 'all' ? list.length === 0 : !list.some((p) => p.id === which.id)) return null
  const chosen = new Map<number, readonly Hunk[]>()
  for (const p of list) {
    if (which === 'all') chosen.set(p.id, p.hunks)
    else if (p.id === which.id) chosen.set(p.id, p.hunks.filter((_h, i) => which.hunks.includes(i)))
  }
  return chosen
}

/** Apply the picked hunks as one undoable step; Claude's text gets the tint. */
export function acceptHunks(state: EditorState, which: HunkPick): TransactionSpec {
  const { list, seq } = state.field(proposals)
  const chosen = pick(list, which)
  if (!chosen) return {}
  const all = [...chosen.values()].flat().toSorted((a, b) => a.from - b.from)
  const changes = state.changes(all.map((h) => ({ from: h.from, to: h.to, insert: h.insert })))
  const next = list
    .map((p) => mapProposal(p, changes, p.hunks.filter((h) => !all.includes(h))))
    .filter((p) => p.hunks.length > 0)
  return {
    changes,
    effects: setPending.of({ list: next, seq }),
    annotations: [claudeEdit.of(true), ownProposalEdit.of(true), Transaction.userEvent.of('claude.accept')],
  }
}

/** Drop the picked hunks; the text is untouched. */
export function rejectHunks(state: EditorState, which: HunkPick): TransactionSpec {
  const { list, seq } = state.field(proposals)
  const chosen = pick(list, which)
  if (!chosen) return {}
  const next = list
    .map((p) => ({ ...p, hunks: p.hunks.filter((h) => !chosen.get(p.id)?.includes(h)) }))
    .filter((p) => p.hunks.length > 0)
  return { effects: setPending.of({ list: next, seq }) }
}
