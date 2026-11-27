/** Editor page controller: intake and images; the bar, history and selection actions live in their own modules. */
import { redo } from '@codemirror/commands'
import { api, ApiError, type DraftState, type Held, type ReleasedDraft, type Step } from './api'
import { inWork } from './approve'
import { initLocale, watchBuild } from './boot'
import { EditorAsks } from './editorAsks'
import { EditorBar } from './editorBar'
import { EditorCard } from './editorCard'
import { EditorHeld } from './editorHeld'
import { $, EditorCore } from './editorCore'
import { EditorHistory } from './editorHistory'
import { watchDrafts } from './feed'
import { loadDoc } from './editorState'
import { setHeldSpans } from './heldSpans'
import { langAttrs, langCompartment, type SelectionAction } from './editorView'
import { lostSome } from './held'
import { label } from './i18n'
import { icon } from './icons'
import { insertSpec, uploadAll } from './images'
import { pulse } from './signals'
import { setStatus } from './status'

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
  // Another tab (or window) saved this draft: show it here within a second.
  watchDrafts((changed) => {
    if (changed === id || changed === '*') void page.pull()
  })
  document.body.dataset.ready = '1'
}

class EditorPage extends EditorCore {
  private readonly bar = new EditorBar(this)
  private readonly history = new EditorHistory(this)
  private readonly asks = new EditorAsks(this, (h, mine) => {
    this.held.hold(h, mine)
  })
  private readonly held = new EditorHeld(this, {
    proposal: (rel, mine) => {
      this.asks.released(rel, mine)
    },
    draft: (rel) => void this.releasedDraft(rel),
  })
  private readonly card = new EditorCard({
    id: this.id,
    holds: this.holds,
    fail: (err) => {
      this.fail(err)
    },
    poke: () => {
      this.held.poller.poke()
    },
    spans: (spans) => {
      this.view.dispatch({ effects: setHeldSpans.of(spans) })
    },
    work: $('#work'),
    after: $('#intake-form'),
    doc: document,
  })

  constructor(id: string, state: DraftState, status: HTMLElement) {
    super(id, state, status)
    // Read-only handle for the headless E2E driver (selection set-up, state reads).
    // `held`: the held-answer poller (editorHeld.ts); `card`: the permission card (editorCard.ts).
    Object.assign(window, { draftStudio: { view: this.view, page: this, held: this.held, card: this.card } })
  }

  render(): void {
    const { meta } = this.state
    document.body.dataset.kind = meta.kind
    $('#kind').innerHTML = icon(meta.kind)
    $('#kind').dataset.label = meta.kind === 'mail' ? 'kindMail' : 'kindText'
    $('#kind').setAttribute('aria-label', label($('#kind').dataset.label ?? ''))
    $('#mail-fields').hidden = meta.kind !== 'mail'
    this.to.value = meta.to
    this.subject.value = meta.subject
    this.lang.value = meta.lang
    this.paintAuthors()
    this.renderIntake()
    this.asks.render()
    this.idleStatus()
    this.wire()
    // Anything held or released while the page was closed.
    void this.held.poller.poll()
  }

  protected paintState(s: DraftState): void {
    this.bar.paint(s)
  }

  /** Another tab answered the intake, got the first draft or asked Claude: show it here too. */
  protected remote(s: DraftState): void {
    if (!this.docLoaded && inWork(s.meta.phase)) this.applyStep({ questions: [], state: s, stripped: [], dropped: [] })
    else if (!this.docLoaded) this.renderIntake()
    this.asks.render()
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
    $('#redo').addEventListener('click', () => {
      redo(this.view)
      this.view.focus()
    })
    this.history.wire()
    this.bar.wire()
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

  // -- images -------------------------------------------------------------
  /** Upload pasted/dropped images, then put their markdown on its own line at the (mapped) point. */
  protected async addImages(files: File[], pos: number, how: 'paste' | 'drop'): Promise<void> {
    if (!files.length) {
      setStatus(this.status, 'error', 'bad-image')
      return
    }
    const spot = { pos }
    this.drops.add(spot)
    const out = await this.busy(() => uploadAll(files, (f) => api.upload(this.id, f)))
    this.drops.delete(spot)
    if (out?.mds.length) {
      const { from, insert, cursor } = insertSpec(this.text(), spot.pos, out.mds)
      this.view.dispatch({ changes: { from, insert }, selection: { anchor: cursor }, userEvent: `input.${how}` })
    }
    if (out?.error) setStatus(this.status, 'error', out.error)
  }

  // -- intake -------------------------------------------------------------
  private renderIntake(): void {
    const { meta } = this.state
    $<HTMLFormElement>('#brief-form').hidden = meta.phase !== 'brief'
    $<HTMLFormElement>('#intake-form').hidden = meta.phase !== 'intake'
    $('#work').hidden = !inWork(meta.phase)
    this.bar.paint(this.state)
    const list = $('#questions')
    list.replaceChildren(...meta.intake.filter((q) => q.a === null).map((q, i) => questionRow(q.q, i, meta.lang)))
    if (meta.phase === 'intake') list.querySelector('textarea')?.focus()
    if (meta.phase === 'brief') $<HTMLTextAreaElement>('#brief').focus()
  }

  private applyStep(step: Step): void {
    const { questions, state } = step
    this.setState(state)
    if (questions.length === 0) {
      this.docLoaded = true
      this.view.dispatch(loadDoc(this.view.state, state.text, state.claude, true))
      // The first draft lost made-up image links: pulse the whole text.
      if (lostSome(step)) pulse(this.view, 0, state.text.length, 'stripped')
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
    this.took(await this.busy(() => api.brief(this.id, brief)))
  }

  private async sendAnswers(): Promise<void> {
    const answers = [...$('#questions').querySelectorAll('textarea')].map((t) => t.value)
    this.took(await this.busy(() => api.intake(this.id, answers)))
  }

  private took(step: Step | { held: Held } | undefined): void {
    if (step && 'held' in step) this.held.hold(step.held)
    else if (step) this.applyStep(step)
  }

  /**
   * A held first draft, already saved server-side: shown with the state saved
   * since (field edits made during the hold). A page that already shows the
   * text (reloaded after the release) has nothing to apply.
   */
  private async releasedDraft(rel: ReleasedDraft): Promise<void> {
    if (this.docLoaded) return
    const state = await this.busy(() => api.get(this.id))
    if (state) this.applyStep({ ...rel, state })
  }

  // -- selection actions ----------------------------------------------------
  protected onAction(action: SelectionAction, from: number, to: number, instruction: string): Promise<void> {
    return this.asks.act(action, from, to, instruction)
  }
}

function questionRow(question: string, i: number, lang: string): HTMLElement {
  const row = document.createElement('label')
  row.className = 'question'
  const text = document.createElement('span')
  text.className = 'q-text'
  text.textContent = question
  text.dir = 'auto'
  const input = document.createElement('textarea')
  input.rows = 2
  input.name = `a${i}`
  input.lang = lang
  input.dir = 'auto'
  row.append(text, input)
  return row
}
