/** Landing another tab's saved state in this tab's view (the rebase itself is pure: sync.ts). */
import { type ChangeSet, Transaction } from '@codemirror/state'
import type { EditorView } from '@codemirror/view'
import type { DraftState } from './api'
import { authorship, proposalsOf, remoteEdit, setAuthorship } from './editorState'
import { pulse } from './signals'
import { merge } from './sync'

/**
 * Apply the server's changes since `base` over the unsaved `local` ones (cursor and
 * selection map along, undo skips them) and return the unsaved changes on the new
 * base. A pending proposal the other tab edited is dropped, as for your own edit,
 * and its span pulses the cross: no silent drops.
 */
export function applyRemote(view: EditorView, base: string, local: ChangeSet, s: DraftState): ChangeSet {
  const field = view.state.field(authorship)
  const m = merge(base, local, field.ranges, s)
  const hit = proposalsOf(view.state).filter((p) => m.remote.touchesRange(p.from, p.to))
  view.dispatch({
    changes: m.remote,
    effects: setAuthorship.of({ ranges: m.ranges, rev: field.rev }),
    annotations: [remoteEdit.of(true), Transaction.addToHistory.of(false)],
  })
  for (const p of hit) pulse(view, m.remote.mapPos(p.from, -1), m.remote.mapPos(p.to, 1), 'gone')
  return m.local
}
