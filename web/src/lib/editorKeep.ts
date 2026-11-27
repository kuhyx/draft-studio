/**
 * The editor's unsaved edits, mirrored into IndexedDB (unsaved.ts) and replayed on reopen:
 * the replay is the 409 rebase (EditorCore.rebase) with the stored base as the base.
 */
import { type ChangeDesc, Transaction } from '@codemirror/state'
import { api, ApiError, type DraftState } from './api'
import type { Pending } from './editorDom'
import type { EditorCore } from './editorCore'
import { applyRemote } from './editorRemote'
import { authorship, remoteEdit, setAuthorship } from './editorState'
import { baseOf, type Keeper, makeLocks, openDb, type Session, type Snapshot, startSession, type Unsaved } from './unsaved'
import { changesBetween } from './sync'

const pair = ([a, b]: readonly [number, number]): [number, number] => [a, b]

/** What the server has not acknowledged (null: nothing). While a save flies, its text is part of it. */
export function snapshotOf(page: EditorCore): Snapshot | null {
  if (!page.docLoaded || !(page.dirty || page.flying)) return null
  return {
    baseVersion: page.base.version,
    baseText: page.base.text,
    sent: page.flying ? page.sent : null,
    text: page.text(),
    claude: page.view.state.field(authorship).ranges.map(pair),
    to: page.to.value,
    subject: page.subject.value,
    toAuthor: page.toAuthor,
    subjectAuthor: page.subjectAuthor,
    state: page.state,
  }
}

/** Carry spans the page tracks (pending proposals, upload drops) through an edit; an edit inside a proposal makes it stale. */
export function mapSpans(changes: ChangeDesc, pending: Iterable<Pending>, drops: Iterable<{ pos: number }>): void {
  for (const p of pending) {
    if (changes.touchesRange(p.from, p.to)) p.stale = true
    p.from = changes.mapPos(p.from, -1)
    p.to = changes.mapPos(p.to, 1)
  }
  for (const d of drops) d.pos = changes.mapPos(d.pos)
}

/**
 * Put gone tabs' unsaved edits back, oldest first. Each record's text is shown, then the changes the
 * page already holds (the server's state, the records before it) are merged under its edits: the 409
 * rebase (`applyRemote`) with the record's own base. The page keeps the server state as its base, so
 * what is left unsaved is the diff against it; this page's own record replaces the adopted ones.
 */
export async function restoreUnsaved(page: EditorCore, recs: readonly Unsaved[], keeper: Keeper): Promise<void> {
  if (!page.docLoaded || recs.length === 0) return
  keeper.adopt(recs.map((r) => r.owner))
  for (const rec of recs) await replay(page, rec)
  page.paintAuthors()
  page.local = changesBetween(page.base.text, page.text())
  page.syncProposals(page.state)
  const { to, subject } = page.state.meta
  if (!page.local.empty || page.to.value !== to || page.subject.value !== subject) page.schedule()
  else page.keep()
}

async function replay(page: EditorCore, rec: Unsaved): Promise<void> {
  // A save in flight when the tab died may have landed: then only the edits after it are unsaved.
  const moved = page.state.meta.version > rec.baseVersion
  const versions = rec.sent !== null && moved ? await api.history(page.id).catch(() => []) : []
  const base = baseOf(rec, versions)
  const { view } = page
  const here = { ...page.state, text: page.text(), claude: view.state.field(authorship).ranges.map(pair) }
  view.dispatch({
    changes: { from: 0, to: view.state.doc.length, insert: rec.text },
    effects: setAuthorship.of({ ranges: rec.claude, rev: view.state.field(authorship).rev }),
    annotations: [remoteEdit.of(true), Transaction.addToHistory.of(false)],
  })
  applyRemote(view, base.text, changesBetween(base.text, rec.text), here)
  // A field this tab changed from what it knew wins; one it left alone follows the server.
  const was = rec.state.meta
  if (rec.to !== was.to) [page.to.value, page.toAuthor] = [rec.to, rec.toAuthor]
  if (rec.subject !== was.subject) [page.subject.value, page.subjectAuthor] = [rec.subject, rec.subjectAuthor]
}

/** The draft's state; with the server unreachable, the state the newest unsaved record grew from (they save once it answers). */
export async function loadState(id: string, recs: readonly Unsaved[]): Promise<DraftState> {
  try {
    return await api.get(id)
  } catch (err) {
    const last = recs.at(-1)
    if (last && err instanceof ApiError && err.code === 'offline') return last.state
    throw err
  }
}

/** The real browser's store for draft `id`; one per page. */
export const browserSession = (id: string): Session =>
  startSession(id, {
    db: openDb(),
    locks: makeLocks(navigator.locks),
    owner: crypto.randomUUID(),
    onError: (err) => {
      console.error('unsaved edits not stored', err)
    },
  })
