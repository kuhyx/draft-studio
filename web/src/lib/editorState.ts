/**
 * CodeMirror state for the draft: who wrote what, and Claude's pending proposal.
 * Pure state (no view), so the ownership rules are unit-testable.
 */
import { invertedEffects } from '@codemirror/commands'
import {
  Annotation,
  type EditorState,
  type Extension,
  StateEffect,
  StateField,
  Transaction,
  type TransactionSpec,
} from '@codemirror/state'
import { applyChanges, normalize, type Range } from './authorship'
import { type Hunk, wordHunks } from './diff'

/** Marks a transaction as Claude text accepted by you. */
export const claudeEdit = Annotation.define<boolean>()

interface AuthorshipValue {
  readonly ranges: readonly Range[]
  readonly rev: number
}
/** Replace the authorship snapshot (exported for tests). */
export const setAuthorship = StateEffect.define<AuthorshipValue>()

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
  readonly from: number
  readonly to: number
  readonly hunks: readonly Hunk[]
}
const setProposal = StateEffect.define<Proposal | null>()
const ownProposalEdit = Annotation.define<boolean>()

/** The pending Claude proposal for one span, or null. */
export const proposal = StateField.define<Proposal | null>({
  create: () => null,
  update(value, tr) {
    for (const e of tr.effects) if (e.is(setProposal)) return e.value
    if (!value || !tr.docChanged) return value
    // Any edit of yours touching the span makes the proposal stale: drop it, no merge.
    if (tr.annotation(ownProposalEdit) !== true && tr.changes.touchesRange(value.from, value.to)) return null
    const m = tr.changes
    return {
      from: m.mapPos(value.from, -1),
      to: m.mapPos(value.to, 1),
      hunks: value.hunks.map((h) => ({ from: m.mapPos(h.from, 1), to: Math.max(m.mapPos(h.to, -1), m.mapPos(h.from, 1)), insert: h.insert })),
    }
  },
})

export const authorshipExtensions: Extension = [authorship, authorshipHistory, proposal]

/** Replace the whole doc (load, restore, first draft) with its authorship. */
export function loadDoc(
  state: EditorState,
  text: string,
  claude: readonly Range[],
  addToHistory: boolean,
): TransactionSpec {
  return {
    changes: { from: 0, to: state.doc.length, insert: text },
    effects: [setAuthorship.of({ ranges: claude, rev: state.field(authorship).rev }), setProposal.of(null)],
    annotations: [Transaction.addToHistory.of(addToHistory)],
  }
}

/** Turn Claude's replacement for [from, to) into a pending word-diff proposal. */
export function setProposalFor(state: EditorState, from: number, to: number, replacement: string): TransactionSpec {
  const hunks = wordHunks(state.sliceDoc(from, to), replacement, from)
  return { effects: setProposal.of(hunks.length > 0 ? { from, to, hunks } : null) }
}

function pick(p: Proposal, which: readonly number[] | 'all'): { chosen: Hunk[]; rest: Hunk[] } {
  const chosen = p.hunks.filter((_h, i) => which === 'all' || which.includes(i))
  return { chosen, rest: p.hunks.filter((h) => !chosen.includes(h)) }
}

/** Apply some (or all) hunks as one undoable step; Claude's text gets the tint. */
export function acceptHunks(state: EditorState, which: readonly number[] | 'all'): TransactionSpec {
  const p = state.field(proposal)
  if (!p) return {}
  const { chosen, rest } = pick(p, which)
  const changes = state.changes(chosen.map((h) => ({ from: h.from, to: h.to, insert: h.insert })))
  const mapped = rest.map((h) => ({
    from: changes.mapPos(h.from, 1),
    to: Math.max(changes.mapPos(h.to, -1), changes.mapPos(h.from, 1)),
    insert: h.insert,
  }))
  const next = mapped.length > 0 ? { from: changes.mapPos(p.from, -1), to: changes.mapPos(p.to, 1), hunks: mapped } : null
  return {
    changes,
    effects: setProposal.of(next),
    annotations: [claudeEdit.of(true), ownProposalEdit.of(true), Transaction.userEvent.of('claude.accept')],
  }
}

/** Drop some (or all) hunks; the text is untouched. */
export function rejectHunks(state: EditorState, which: readonly number[] | 'all'): TransactionSpec {
  const p = state.field(proposal)
  if (!p) return {}
  const { rest } = pick(p, which)
  return { effects: setProposal.of(rest.length > 0 ? { ...p, hunks: rest } : null) }
}
