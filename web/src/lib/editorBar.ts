/** Editor bar actions on the whole draft: the approve toggle and the Gmail draft. */
import { api, type DraftState } from './api'
import { inWork, paintApprove, toggleApproval } from './approve'
import { $, type EditorCore } from './editorCore'
import { setStatus } from './status'

export class EditorBar {
  private readonly approveBtn = $('#approve')
  private readonly gmailBtn = $('#gmail')

  constructor(private readonly page: EditorCore) {}

  wire(): void {
    this.gmailBtn.addEventListener('click', () => void this.gmail())
    this.approveBtn.addEventListener('click', () => void this.approve())
  }

  /** Show the approval as the server reported it; the buttons live only while the editor shows. */
  paint(s: DraftState): void {
    this.gmailBtn.hidden = s.meta.kind !== 'mail'
    this.approveBtn.hidden = !inWork(s.meta.phase)
    paintApprove(this.approveBtn, s.approved)
  }

  private async approve(): Promise<void> {
    const p = this.page
    await p.flush()
    const s = await p.busy(() => toggleApproval(api, p.id, p.state.approved, p.text()))
    if (s) p.setState(s)
  }

  private async gmail(): Promise<void> {
    const p = this.page
    p.dirty = true
    await p.flush()
    const out = await p.busy(() => api.gmail(p.id))
    if (out) {
      p.state.meta.gmailDraftId = out.id
      document.body.dataset.gmail = out.id
      setStatus(p.status, 'done')
    }
  }
}
