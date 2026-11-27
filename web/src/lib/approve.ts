/**
 * The approve toggle: kuhy marks the draft he reviewed as fit to send. The
 * server hashes draft.md on disk; any later edit of the text, To or Subject
 * drops the approval there, and the next saved state repaints this button.
 * State shows as icon shape (empty vs ticked shield), colour and aria-pressed;
 * the aria-label stays one constant string.
 */
import type { Phase } from './api'
import { icon } from './icons'

export function paintApprove(btn: HTMLElement, approved: boolean): void {
  btn.setAttribute('aria-pressed', String(approved))
  btn.dataset.icon = approved ? 'approved' : 'approve'
  btn.innerHTML = icon(btn.dataset.icon)
}

/** Export buttons work only on approved text: disabled for clicks and for screen readers. */
export function paintExport(btn: HTMLButtonElement, approved: boolean): void {
  btn.disabled = !approved
  btn.setAttribute('aria-disabled', String(!approved))
}

/** Phases that show the editor: approval does not close it. */
export function inWork(phase: Phase): boolean {
  return phase === 'editing' || phase === 'approved'
}

interface ApprovalClient<S> {
  approve: (id: string, text: string) => Promise<S>
  unapprove: (id: string) => Promise<S>
}

/** Approve the text the editor shows (the server refuses it if the saved file differs), or take it back. */
export function toggleApproval<S>(client: ApprovalClient<S>, id: string, approved: boolean, text: string): Promise<S> {
  return approved ? client.unapprove(id) : client.approve(id, text)
}
