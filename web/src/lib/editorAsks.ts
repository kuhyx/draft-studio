/**
 * Selection actions: questions (shown as cards) and proposals. Several may be
 * in flight at once. A selection touching pending proposals comments on them:
 * Claude gets the old replacement and the comment, and its answer replaces
 * them in place. Elsewhere a request starts a proposal of its own. An answer
 * that cannot land, or lost made-up image links, pulses its span (signals.ts).
 */
import { type Ask, api } from './api'
import { $, type EditorCore, type Pending } from './editorCore'
import { proposalOutcome, reviseTarget, setProposalFor } from './editorState'
import type { SelectionAction } from './editorView'
import { pulse } from './signals'

export class EditorAsks {
  constructor(private readonly page: EditorCore) {}

  async act(action: SelectionAction, from: number, to: number, instruction: string): Promise<void> {
    const p = this.page
    const text = p.text()
    if (action === 'ask') {
      const pending: Pending = { from, to, stale: false }
      p.pending.add(pending)
      try {
        const ask = await p.busy(() => api.ask(p.id, text, from, to, instruction))
        if (ask) {
          p.state.meta.asks.push(ask)
          this.render()
        }
      } finally {
        p.pending.delete(pending)
      }
      return
    }
    const target = reviseTarget(p.view.state, from, to)
    const pending: Pending = { from: target?.from ?? from, to: target?.to ?? to, stale: false }
    p.pending.add(pending)
    try {
      const prop = await p.busy(() =>
        api.propose(p.id, text, pending.from, pending.to, instruction, target?.previous),
      )
      // Your edit inside the span while Claude worked wins: drop the stale answer.
      if (prop && !pending.stale) {
        const { from: a, to: b } = pending
        const outcome = proposalOutcome(p.view.state, a, b, target?.ids)
        p.view.dispatch(setProposalFor(p.view.state, a, b, prop.replacement, target?.ids))
        if (outcome !== 'ok') pulse(p.view, a, b, outcome)
        else if (prop.stripped.length > 0) pulse(p.view, a, b, 'stripped')
      }
    } finally {
      p.pending.delete(pending)
    }
  }

  render(): void {
    const { asks } = this.page.state.meta
    const panel = $('#asks')
    panel.replaceChildren(...asks.toReversed().map((a) => askCard(a)))
    panel.hidden = asks.length === 0
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
