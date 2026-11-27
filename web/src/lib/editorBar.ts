/** Editor bar actions on the whole draft: the approve toggle, the Gmail draft, the PDF export and the WhatsApp copy. */
import { api, type DraftState } from './api'
import { inWork, paintApprove, paintExport, toggleApproval } from './approve'
import { copyApproved, flashIcon } from './copyText'
import { $, type EditorCore } from './editorCore'
import { setStatus } from './status'

export class EditorBar {
  private readonly approveBtn = $('#approve')
  private readonly gmailBtn = $('#gmail')
  private readonly pdfBtn = $<HTMLButtonElement>('#export-pdf')
  private readonly waBtn = $<HTMLButtonElement>('#copy-whatsapp')

  constructor(private readonly page: EditorCore) {}

  wire(): void {
    this.gmailBtn.addEventListener('click', () => void this.gmail())
    this.approveBtn.addEventListener('click', () => void this.approve())
    this.pdfBtn.addEventListener('click', () => void this.pdf())
    this.waBtn.addEventListener('click', () => void this.whatsapp())
  }

  /** Show the approval as the server reported it; the buttons live only while the editor shows. */
  paint(s: DraftState): void {
    this.gmailBtn.hidden = s.meta.kind !== 'mail'
    this.approveBtn.hidden = !inWork(s.meta.phase)
    paintApprove(this.approveBtn, s.approved)
    this.pdfBtn.hidden = !inWork(s.meta.phase)
    paintExport(this.pdfBtn, s.approved)
    this.waBtn.hidden = !inWork(s.meta.phase)
    paintExport(this.waBtn, s.approved)
  }

  private async approve(): Promise<void> {
    const p = this.page
    await p.flush()
    const s = await p.busy(() => toggleApproval(api, p.id, p.state.approved, p.text()))
    if (s) p.setState(s)
  }

  /** Download the approved text as PDF; an edit still in the autosave window drops the approval first. */
  private async pdf(): Promise<void> {
    const p = this.page
    await p.flush()
    if (!p.state.approved) return
    const file = await p.busy(() => api.pdf(p.id))
    if (!file) return
    const url = URL.createObjectURL(file.blob)
    const a = document.createElement('a')
    a.href = url
    a.download = file.name
    a.click()
    // The click starts the download at once; the URL only has to outlive that.
    setTimeout(() => { URL.revokeObjectURL(url); }, 60_000)
    document.body.dataset.pdf = file.name
  }

  /** Copy the approved saved text as WhatsApp text; a clipboard refusal goes to the generic error status. */
  private async whatsapp(): Promise<void> {
    const p = this.page
    await p.flush()
    if (!p.state.approved) return
    const s = await p.busy(() => copyApproved(api, p.id, (t) => navigator.clipboard.writeText(t)))
    if (!s) return
    if (s.approved) flashIcon(this.waBtn, 1500)
    else p.setState(s)
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
