/**
 * Editor page core: the CodeMirror view, the mail fields and the autosave loop
 * every part of the page shares (bar, history, intake). Members are public so
 * the parts in editorBar.ts and editorHistory.ts can drive the same state.
 */
import { ChangeSet } from '@codemirror/state'
import type { EditorView, ViewUpdate } from '@codemirror/view'
import { api, ApiError, type Author, type DraftState, type Held, StaleError } from './api'
import { inWork } from './approve'
import { applyRemote } from './editorRemote'
import { authorship, loadDoc, remoteEdit, serverLoad } from './editorState'
import { createEditor, type SelectionAction } from './editorView'
import { setStatus } from './status'

// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- typed DOM lookup
export const $ = <T extends HTMLElement>(sel: string): T => {
  const el = document.querySelector<T>(sel)
  if (!el) throw new Error(`missing ${sel}`)
  return el
}

export interface Pending {
  from: number
  to: number
  stale: boolean
}

/** A held answer (held.ts); `mine`: the span this page asked on, still tracked in `pending`. */
export interface Hold {
  held: Held
  mine?: { pending: Pending; ids?: readonly number[] }
}

export abstract class EditorCore {
  readonly view: EditorView
  private timer: ReturnType<typeof setTimeout> | undefined
  dirty = false
  private saving: Promise<void> = Promise.resolve()
  /** A save is on the wire: its changes are neither in `base` nor in `local`. */
  private flying = false
  /** The saved state this tab's text grew from (what a save sends as `base`). */
  base: { version: number; text: string }
  /** Unsaved changes on top of `base.text` (composed per transaction). */
  local: ChangeSet
  private inFlight = 0
  /** Saved version the undo walk stands on; +Infinity once the text moved on. */
  cursor: number
  readonly pending = new Set<Pending>()
  /** Answers the server holds, by held id; the status stays busy while any is held. */
  readonly holds = new Map<string, Hold>()
  /**
   * The editor shows the draft's text. Page-local on purpose: an autosave reply
   * may already say "editing" for a held first draft this page has not loaded.
   */
  docLoaded: boolean
  /** Insert points of uploads in flight, mapped through edits made meanwhile. */
  readonly drops = new Set<{ pos: number }>()
  toAuthor: Author
  subjectAuthor: Author
  readonly to = $<HTMLInputElement>('#to')
  readonly subject = $<HTMLInputElement>('#subject')
  readonly lang = $<HTMLInputElement>('#lang')

  constructor(
    readonly id: string,
    public state: DraftState,
    readonly status: HTMLElement,
  ) {
    this.cursor = state.meta.version
    this.base = { version: state.meta.version, text: state.text }
    this.local = ChangeSet.empty(state.text.length)
    this.docLoaded = inWork(state.meta.phase)
    this.toAuthor = state.meta.toAuthor
    this.subjectAuthor = state.meta.subjectAuthor
    this.view = createEditor(
      $('#editor'),
      {
        onUpdate: (u) => { this.onUpdate(u); },
        onAction: (a, f, t, i) => void this.onAction(a, f, t, i),
        onImages: (files, pos, how) => void this.addImages(files, pos, how),
      },
      state.meta.lang,
      id,
    )
    this.view.dispatch(loadDoc(this.view.state, state.text, state.claude, false))
    this.dirty = false
  }

  protected abstract onAction(action: SelectionAction, from: number, to: number, instruction: string): Promise<void>
  protected abstract addImages(files: File[], pos: number, how: 'paste' | 'drop'): Promise<void>
  /** Repaint whatever shows server state (the bar's approve and export buttons). */
  protected abstract paintState(s: DraftState): void
  /** Repaint what another tab moved since `prev` (intake, asks, a first draft); never steals focus. */
  protected abstract remote(s: DraftState, prev: DraftState): void

  text(): string {
    return this.view.state.doc.toString()
  }

  paintAuthors(): void {
    this.to.dataset.author = this.toAuthor
    this.subject.dataset.author = this.subjectAuthor
  }

  // -- autosave -----------------------------------------------------------
  private onUpdate(u: ViewUpdate): void {
    for (const tr of u.transactions) {
      if (tr.annotation(serverLoad)) this.rebased(this.state.meta.version, tr.newDoc.toString(), ChangeSet.empty(tr.newDoc.length))
      else if (!tr.annotation(remoteEdit)) this.local = this.local.compose(tr.changes)
    }
    // Another tab's text maps pending spans (its edit there makes them stale) but is not unsaved here.
    const remote = u.transactions.some((t) => t.annotation(remoteEdit))
    if (u.docChanged) {
      for (const p of this.pending) {
        if (u.changes.touchesRange(p.from, p.to)) p.stale = true
        p.from = u.changes.mapPos(p.from, -1)
        p.to = u.changes.mapPos(p.to, 1)
      }
      for (const d of this.drops) d.pos = u.changes.mapPos(d.pos)
      if (u.transactions.some((t) => t.isUserEvent('input') || t.isUserEvent('delete') || t.isUserEvent('claude'))) {
        this.cursor = Number.POSITIVE_INFINITY
      }
    }
    if (!remote && (u.docChanged || u.startState.field(authorship) !== u.state.field(authorship))) this.schedule()
  }

  schedule(): void {
    this.dirty = true
    this.idleStatus()
    clearTimeout(this.timer)
    this.timer = setTimeout(() => void this.flush(), 400)
  }

  async flush(): Promise<void> {
    clearTimeout(this.timer)
    await this.saving
    if (!this.dirty) return
    this.dirty = false
    // Before the first draft the editor is empty: a field edit then must not save that
    // empty text over a draft the server writes meanwhile (a held first draft).
    const body = this.docLoaded
      ? { text: this.text(), claude: this.view.state.field(authorship).ranges, base: this.base.version }
      : {}
    const sent = this.local
    this.local = ChangeSet.empty(this.view.state.doc.length)
    this.flying = true
    const patch = {
      ...body,
      to: this.to.value,
      subject: this.subject.value,
      toAuthor: this.toAuthor,
      subjectAuthor: this.subjectAuthor,
      lang: this.lang.value.trim(),
    }
    this.saving = api
      .save(this.id, patch)
      .then((s) => {
        this.flying = false
        this.setState(s)
        if (this.docLoaded) this.base = { version: s.meta.version, text: s.text }
        if (this.cursor === Number.POSITIVE_INFINITY) this.cursor = s.meta.version
        if (!this.dirty) this.idleStatus()
      })
      .catch((err: unknown) => {
        this.flying = false
        this.local = sent.compose(this.local)
        this.dirty = true
        // Another tab saved first: take its state under these edits and save again.
        if (err instanceof StaleError) this.rebase(err.state)
        else this.fail(err)
        // Server unreachable: the edits stay here and go out once it answers again.
        if (err instanceof ApiError && err.code === 'offline') this.timer = setTimeout(() => void this.flush(), 2000)
      })
    await this.saving
  }

  /** Fetch the saved state after another tab changed it (feed.ts); never while a save flies. */
  async pull(): Promise<void> {
    await this.saving
    const s = await api.get(this.id).catch(() => null)
    if (!s || this.flying || s.meta.version < this.base.version) return
    const prev = this.state
    if (this.docLoaded && s.meta.version > this.base.version) this.rebase(s)
    else this.setState(s)
    this.remote(s, prev)
    if (this.dirty) this.schedule()
  }

  /** Put the newer saved state `s` under this tab's unsaved edits; nothing of either is lost. */
  rebase(s: DraftState): void {
    const local = applyRemote(this.view, this.base.text, this.local, s)
    const before = this.state.meta
    // A field you did not touch since the last state follows the other tab.
    if (this.to.value === before.to) [this.to.value, this.toAuthor] = [s.meta.to, s.meta.toAuthor]
    if (this.subject.value === before.subject) [this.subject.value, this.subjectAuthor] = [s.meta.subject, s.meta.subjectAuthor]
    if (this.lang.value.trim() === before.lang) this.lang.value = s.meta.lang
    this.paintAuthors()
    this.setState(s)
    this.rebased(s.meta.version, s.text, local)
    if (!local.empty || this.to.value !== s.meta.to || this.subject.value !== s.meta.subject) this.schedule()
  }

  private rebased(version: number, text: string, local: ChangeSet): void {
    this.base = { version, text }
    this.local = local
  }

  /** Every server state repaints the bar: an edit there may have dropped the approval. */
  setState(s: DraftState): void {
    this.state = s
    this.paintState(s)
  }

  /** The resting status: busy while an answer is held, else unsaved or saved. */
  idleStatus(): void {
    setStatus(this.status, this.holds.size > 0 ? 'busy' : this.dirty ? 'unsaved' : 'saved')
    if (this.holds.size > 0) document.body.dataset.held = String(this.holds.size)
    else delete document.body.dataset.held
  }

  fail(err: unknown): void {
    setStatus(this.status, 'error', err instanceof ApiError ? err.code : 'server')
  }

  /** Run a server call; several may be in flight, and the page stays busy until the last one ends. */
  async busy<T>(work: () => Promise<T>): Promise<T | undefined> {
    this.inFlight += 1
    setStatus(this.status, 'busy')
    document.body.dataset.busy = '1'
    try {
      const out = await work()
      if (this.inFlight === 1) this.idleStatus()
      return out
    } catch (err) {
      this.fail(err)
      return undefined
    } finally {
      this.inFlight -= 1
      if (this.inFlight === 0) delete document.body.dataset.busy
    }
  }
}
