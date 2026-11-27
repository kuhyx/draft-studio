/**
 * Editor page core: the CodeMirror view, the mail fields and the autosave loop
 * every part of the page shares (bar, history, intake). Members are public so
 * the parts in editorBar.ts and editorHistory.ts can drive the same state.
 */
import type { EditorView, ViewUpdate } from '@codemirror/view'
import { api, ApiError, type Author, type DraftState, type Held } from './api'
import { inWork } from './approve'
import { authorship, loadDoc } from './editorState'
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

  text(): string {
    return this.view.state.doc.toString()
  }

  paintAuthors(): void {
    this.to.dataset.author = this.toAuthor
    this.subject.dataset.author = this.subjectAuthor
  }

  // -- autosave -----------------------------------------------------------
  private onUpdate(u: ViewUpdate): void {
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
    if (u.docChanged || u.startState.field(authorship) !== u.state.field(authorship)) this.schedule()
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
      ? { text: this.text(), claude: this.view.state.field(authorship).ranges }
      : {}
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
        this.setState(s)
        if (this.cursor === Number.POSITIVE_INFINITY) this.cursor = s.meta.version
        if (!this.dirty) this.idleStatus()
      })
      .catch((err: unknown) => {
        this.dirty = true
        this.fail(err)
      })
    await this.saving
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
