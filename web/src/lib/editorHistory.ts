/** Undo past the session into saved versions, and the history dialog with its restore buttons. */
import { undo, undoDepth } from '@codemirror/commands'
import { api, type Version } from './api'
import { $, type EditorCore } from './editorCore'
import { loadDoc } from './editorState'
import { label } from './i18n'
import { icon } from './icons'
import { setStatus } from './status'

export class EditorHistory {
  constructor(private readonly page: EditorCore) {}

  wire(): void {
    $('#undo').addEventListener('click', () => void this.undo())
    $('#history-open').addEventListener('click', () => void this.open())
    $('#history-close').addEventListener('click', () => { $<HTMLDialogElement>('#history').close(); })
  }

  private async undo(): Promise<void> {
    const p = this.page
    if (undoDepth(p.view.state) > 0) {
      undo(p.view)
      p.view.focus()
      return
    }
    // Nothing left in this session: keep walking back through saved versions.
    await p.flush()
    const versions = await p.busy(() => api.history(p.id))
    if (!versions) return
    const current = p.text()
    const before = versions
      .filter((v): v is Version & { v: number; text: string } => v.v !== undefined && v.text !== undefined)
      .filter((v) => v.v < Math.min(p.cursor, p.state.meta.version) && v.text !== current)
      .at(-1)
    if (!before) return
    const restored = await p.busy(() => api.restore(p.id, before.v))
    if (!restored) return
    p.cursor = before.v
    p.setState(restored)
    p.view.dispatch(loadDoc(p.view.state, restored.text, restored.claude, false))
    p.dirty = false
    setStatus(p.status, 'saved')
  }

  private async open(): Promise<void> {
    const p = this.page
    await p.flush()
    const versions = await p.busy(() => api.history(p.id))
    if (!versions) return
    $('#history-list').replaceChildren(
      ...versions
        .toReversed()
        .map((v) => this.item(v))
        .filter((li): li is HTMLLIElement => li !== null),
    )
    $<HTMLDialogElement>('#history').showModal()
  }

  private item(v: Version): HTMLLIElement | null {
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
  }

  private async restore(v: number): Promise<void> {
    const p = this.page
    const restored = await p.busy(() => api.restore(p.id, v))
    if (!restored) return
    p.setState(restored)
    // In the editor's history, so the restore itself can be undone.
    p.view.dispatch(loadDoc(p.view.state, restored.text, restored.claude, true))
    p.to.value = restored.meta.to
    p.subject.value = restored.meta.subject
    p.toAuthor = restored.meta.toAuthor
    p.subjectAuthor = restored.meta.subjectAuthor
    p.paintAuthors()
    p.cursor = restored.meta.version
    p.dirty = false
    $<HTMLDialogElement>('#history').close()
  }
}
