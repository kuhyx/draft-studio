/** Editor page controller: intake, editor, selection actions, asks, history, mail fields, Gmail. */
import { redo, undo, undoDepth } from '@codemirror/commands'
import type { EditorView, ViewUpdate } from '@codemirror/view'
import { type Ask, api, ApiError, type Author, type DraftState, type Version } from './api'
import { initLocale, watchBuild } from './boot'
import { authorship, loadDoc, setProposalFor } from './editorState'
import { createEditor, langAttrs, langCompartment, type SelectionAction } from './editorView'
import { label } from './i18n'
import { icon } from './icons'
import { setStatus } from './status'

// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- typed DOM lookup
const $ = <T extends HTMLElement>(sel: string): T => {
  const el = document.querySelector<T>(sel)
  if (!el) throw new Error(`missing ${sel}`)
  return el
}

interface Pending {
  from: number
  to: number
  stale: boolean
}

export async function bootEditor(): Promise<void> {
  const id = new URLSearchParams(location.search).get('id') ?? ''
  const status = $<HTMLElement>('#status')
  await initLocale(document)
  let state: DraftState
  try {
    state = await api.get(id)
  } catch (err) {
    setStatus(status, 'error', err instanceof ApiError ? err.code : 'server')
    document.body.dataset.ready = '1'
    return
  }
  const page = new EditorPage(id, state, status)
  page.render()
  watchBuild(document, () => page.flush())
  document.body.dataset.ready = '1'
}

class EditorPage {
  private view: EditorView
  private timer: ReturnType<typeof setTimeout> | undefined
  private dirty = false
  private saving: Promise<void> = Promise.resolve()
  private cursor: number
  private pending = new Set<Pending>()
  private toAuthor: Author
  private subjectAuthor: Author
  private readonly to = $<HTMLInputElement>('#to')
  private readonly subject = $<HTMLInputElement>('#subject')
  private readonly lang = $<HTMLInputElement>('#lang')

  constructor(
    private readonly id: string,
    private state: DraftState,
    private readonly status: HTMLElement,
  ) {
    this.cursor = state.meta.version
    this.toAuthor = state.meta.toAuthor
    this.subjectAuthor = state.meta.subjectAuthor
    this.view = createEditor(
      $('#editor'),
      { onUpdate: (u) => { this.onUpdate(u); }, onAction: (a, f, t, i) => void this.onAction(a, f, t, i) },
      state.meta.lang,
    )
    this.view.dispatch(loadDoc(this.view.state, state.text, state.claude, false))
    this.dirty = false
    // Read-only handle for the headless E2E driver (selection set-up, state reads).
    Object.assign(window, { draftStudio: { view: this.view, page: this } })
  }

  render(): void {
    const { meta } = this.state
    document.body.dataset.kind = meta.kind
    $('#kind').innerHTML = icon(meta.kind)
    $('#kind').dataset.label = meta.kind === 'mail' ? 'kindMail' : 'kindText'
    $('#kind').setAttribute('aria-label', label($('#kind').dataset.label ?? ''))
    $('#mail-fields').hidden = meta.kind !== 'mail'
    $('#gmail').hidden = meta.kind !== 'mail'
    this.to.value = meta.to
    this.subject.value = meta.subject
    this.lang.value = meta.lang
    this.paintAuthors()
    this.renderIntake()
    this.renderAsks()
    setStatus(this.status, 'saved')
    this.wire()
  }

  private wire(): void {
    this.to.addEventListener('input', () => {
      this.toAuthor = 'you'
      this.paintAuthors()
      this.schedule()
    })
    this.subject.addEventListener('input', () => {
      this.subjectAuthor = 'you'
      this.paintAuthors()
      this.schedule()
    })
    this.lang.addEventListener('change', () => {
      this.view.dispatch({ effects: langCompartment.reconfigure(langAttrs(this.lang.value.trim())) })
      this.schedule()
    })
    $('#undo').addEventListener('click', () => void this.undo())
    $('#redo').addEventListener('click', () => {
      redo(this.view)
      this.view.focus()
    })
    $('#history-open').addEventListener('click', () => void this.openHistory())
    $('#history-close').addEventListener('click', () => { $<HTMLDialogElement>('#history').close(); })
    $('#gmail').addEventListener('click', () => void this.gmail())
    $('#brief-form').addEventListener('submit', (e) => {
      e.preventDefault()
      void this.sendBrief()
    })
    $('#intake-form').addEventListener('submit', (e) => {
      e.preventDefault()
      void this.sendAnswers()
    })
    window.addEventListener('pagehide', () => void this.flush())
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') void this.flush()
    })
  }

  private paintAuthors(): void {
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
      if (u.transactions.some((t) => t.isUserEvent('input') || t.isUserEvent('delete') || t.isUserEvent('claude'))) {
        this.cursor = Number.POSITIVE_INFINITY
      }
    }
    if (u.docChanged || u.startState.field(authorship) !== u.state.field(authorship)) this.schedule()
  }

  private schedule(): void {
    this.dirty = true
    setStatus(this.status, 'unsaved')
    clearTimeout(this.timer)
    this.timer = setTimeout(() => void this.flush(), 400)
  }

  async flush(): Promise<void> {
    clearTimeout(this.timer)
    await this.saving
    if (!this.dirty) return
    this.dirty = false
    const patch = {
      text: this.view.state.doc.toString(),
      claude: this.view.state.field(authorship).ranges,
      to: this.to.value,
      subject: this.subject.value,
      toAuthor: this.toAuthor,
      subjectAuthor: this.subjectAuthor,
      lang: this.lang.value.trim(),
    }
    this.saving = api
      .save(this.id, patch)
      .then((s) => {
        this.state = s
        if (this.cursor === Number.POSITIVE_INFINITY) this.cursor = s.meta.version
        if (!this.dirty) setStatus(this.status, 'saved')
      })
      .catch((err: unknown) => {
        this.dirty = true
        this.fail(err)
      })
    await this.saving
  }

  private fail(err: unknown): void {
    setStatus(this.status, 'error', err instanceof ApiError ? err.code : 'server')
  }

  private async busy<T>(work: () => Promise<T>): Promise<T | undefined> {
    setStatus(this.status, 'busy')
    document.body.dataset.busy = '1'
    try {
      const out = await work()
      setStatus(this.status, this.dirty ? 'unsaved' : 'saved')
      return out
    } catch (err) {
      this.fail(err)
      return undefined
    } finally {
      delete document.body.dataset.busy
    }
  }

  // -- intake -------------------------------------------------------------
  private renderIntake(): void {
    const { meta } = this.state
    const brief = $<HTMLFormElement>('#brief-form')
    const intake = $<HTMLFormElement>('#intake-form')
    brief.hidden = meta.phase !== 'brief'
    intake.hidden = meta.phase !== 'intake'
    $('#work').hidden = meta.phase !== 'editing'
    const list = $('#questions')
    list.replaceChildren()
    meta.intake
      .filter((q) => q.a === null)
      .forEach((q, i) => {
        const row = document.createElement('label')
        row.className = 'question'
        const text = document.createElement('span')
        text.className = 'q-text'
        text.textContent = q.q
        text.dir = 'auto'
        const input = document.createElement('textarea')
        input.rows = 2
        input.name = `a${i}`
        input.lang = meta.lang
        input.dir = 'auto'
        row.append(text, input)
        list.append(row)
      })
    if (meta.phase === 'intake') list.querySelector('textarea')?.focus()
    if (meta.phase === 'brief') $<HTMLTextAreaElement>('#brief').focus()
  }

  private applyStep(questions: string[], state: DraftState): void {
    this.state = state
    if (questions.length === 0) {
      this.view.dispatch(loadDoc(this.view.state, state.text, state.claude, true))
      if (state.meta.kind === 'mail' && this.subjectAuthor !== 'you') {
        this.subject.value = state.meta.subject
        this.subjectAuthor = state.meta.subjectAuthor
      }
    }
    this.lang.value = state.meta.lang
    this.view.dispatch({ effects: langCompartment.reconfigure(langAttrs(state.meta.lang)) })
    this.paintAuthors()
    this.renderIntake()
  }

  private async sendBrief(): Promise<void> {
    const brief = $<HTMLTextAreaElement>('#brief').value.trim()
    if (!brief) return
    const step = await this.busy(() => api.brief(this.id, brief))
    if (step) this.applyStep(step.questions, step.state)
  }

  private async sendAnswers(): Promise<void> {
    const answers = [...$('#questions').querySelectorAll('textarea')].map((t) => t.value)
    const step = await this.busy(() => api.intake(this.id, answers))
    if (step) this.applyStep(step.questions, step.state)
  }

  // -- selection actions ----------------------------------------------------
  private async onAction(action: SelectionAction, from: number, to: number, instruction: string): Promise<void> {
    const text = this.view.state.doc.toString()
    const pending: Pending = { from, to, stale: false }
    this.pending.add(pending)
    try {
      if (action === 'ask') {
        const ask = await this.busy(() => api.ask(this.id, text, from, to, instruction))
        if (ask) {
          this.state.meta.asks.push(ask)
          this.renderAsks()
        }
        return
      }
      const prop = await this.busy(() => api.propose(this.id, text, from, to, instruction))
      // Your edit inside the span while Claude worked wins: drop the stale answer.
      if (prop && !pending.stale) this.view.dispatch(setProposalFor(this.view.state, pending.from, pending.to, prop.replacement))
    } finally {
      this.pending.delete(pending)
    }
  }

  private renderAsks(): void {
    const panel = $('#asks')
    panel.replaceChildren(...this.state.meta.asks.toReversed().map((a) => askCard(a)))
    panel.hidden = this.state.meta.asks.length === 0
  }

  // -- history --------------------------------------------------------------
  private async undo(): Promise<void> {
    if (undoDepth(this.view.state) > 0) {
      undo(this.view)
      this.view.focus()
      return
    }
    // Nothing left in this session: keep walking back through saved versions.
    await this.flush()
    const versions = await this.busy(() => api.history(this.id))
    if (!versions) return
    const current = this.view.state.doc.toString()
    const before = versions
      .filter((v): v is Version & { v: number; text: string } => v.v !== undefined && v.text !== undefined)
      .filter((v) => v.v < Math.min(this.cursor, this.state.meta.version) && v.text !== current)
      .at(-1)
    if (!before) return
    const restored = await this.busy(() => api.restore(this.id, before.v))
    if (!restored) return
    this.cursor = before.v
    this.state = restored
    this.view.dispatch(loadDoc(this.view.state, restored.text, restored.claude, false))
    this.dirty = false
    setStatus(this.status, 'saved')
  }

  private async openHistory(): Promise<void> {
    await this.flush()
    const versions = await this.busy(() => api.history(this.id))
    if (!versions) return
    const list = $('#history-list')
    list.replaceChildren(
      ...versions.toReversed().map((v) => {
        const li = document.createElement('li')
        li.className = 'version'
        li.dataset.type = v.type
        const time = document.createElement('time')
        time.textContent = new Date(v.ts * 1000).toLocaleTimeString()
        li.append(time)
        if (v.v !== undefined && v.text !== undefined) {
          const { v: num, text } = v
          li.dataset.v = String(num)
          const preview = document.createElement('span')
          preview.className = 'preview'
          preview.textContent = text.slice(0, 160)
          preview.dir = 'auto'
          const btn = document.createElement('button')
          btn.type = 'button'
          btn.className = 'icon-btn restore'
          btn.innerHTML = icon('history')
          btn.dataset.label = 'restore'
          btn.setAttribute('aria-label', label('restore'))
          btn.addEventListener('click', () => void this.restore(num))
          li.append(preview, btn)
        } else if (v.question !== undefined) {
          li.innerHTML += icon('ask')
          const qa = document.createElement('span')
          qa.className = 'preview'
          qa.textContent = `${v.question} — ${v.answer ?? ''}`
          li.append(qa)
        } else {
          return null
        }
        return li
      }).filter((li): li is HTMLLIElement => li !== null),
    )
    $<HTMLDialogElement>('#history').showModal()
  }

  private async restore(v: number): Promise<void> {
    const restored = await this.busy(() => api.restore(this.id, v))
    if (!restored) return
    this.state = restored
    // In the editor's history, so the restore itself can be undone.
    this.view.dispatch(loadDoc(this.view.state, restored.text, restored.claude, true))
    this.to.value = restored.meta.to
    this.subject.value = restored.meta.subject
    this.toAuthor = restored.meta.toAuthor
    this.subjectAuthor = restored.meta.subjectAuthor
    this.paintAuthors()
    this.cursor = restored.meta.version
    this.dirty = false
    $<HTMLDialogElement>('#history').close()
  }

  // -- gmail ----------------------------------------------------------------
  private async gmail(): Promise<void> {
    this.dirty = true
    await this.flush()
    const out = await this.busy(() => api.gmail(this.id))
    if (out) {
      this.state.meta.gmailDraftId = out.id
      document.body.dataset.gmail = out.id
      setStatus(this.status, 'done')
    }
  }
}

function askCard(a: Ask): HTMLElement {
  const card = document.createElement('article')
  card.className = 'ask-card'
  const quote = document.createElement('blockquote')
  quote.textContent = a.span
  const q = document.createElement('p')
  q.className = 'ask-q'
  q.textContent = a.question
  const ans = document.createElement('p')
  ans.className = 'ask-a'
  ans.textContent = a.answer
  // User/Claude content keeps its own direction inside an RTL interface.
  for (const el of [quote, q, ans]) el.dir = 'auto'
  card.append(quote, q, ans)
  return card
}
