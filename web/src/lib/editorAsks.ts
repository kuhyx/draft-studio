/**
 * Selection actions: questions (shown as cards) and proposals. Several may be
 * in flight at once. A selection touching pending proposals comments on them:
 * Claude gets the old replacement and the comment, and its answer replaces
 * them in place. Elsewhere a request starts a proposal of its own. An answer
 * that cannot land, or lost made-up image links or screenshots, pulses its
 * span (signals.ts). An answer the server holds (202, held.ts) keeps its span
 * tracked under the held id until the release lands through the same path.
 */
import { type Ask, api, type Held, type Proposal, type ReleasedProposal } from './api'
import { $, type EditorCore, type Hold, type Pending } from './editorCore'
import { proposalOutcome, reviseTarget, setProposalFor } from './editorState'
import type { SelectionAction } from './editorView'
import { landing, lostSome } from './held'
import { pulse } from './signals'

export class EditorAsks {
  constructor(
    private readonly page: EditorCore,
    private readonly onHeld: (held: Held, mine: Hold['mine']) => void,
  ) {}

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
    let held = false
    try {
      const prop = await p.busy(() =>
        api.propose(p.id, text, pending.from, pending.to, instruction, target?.previous),
      )
      if (prop && 'held' in prop) {
        held = true
        this.onHeld(prop.held, { pending, ids: target?.ids })
      } else if (prop && !pending.stale) {
        // Your edit inside the span while Claude worked wins: drop the stale answer.
        this.land(prop, pending.from, pending.to, target?.ids)
      }
    } finally {
      if (!held) p.pending.delete(pending)
    }
  }

  /** A released held proposal: at its tracked span, or (after a reload) where the doc still holds it. */
  released(rel: ReleasedProposal, mine: Hold['mine']): void {
    const at = landing(rel, mine, this.page.text())
    if (at) this.land(rel, at.from, at.to, at.ids)
  }

  private land(prop: Proposal, a: number, b: number, ids?: readonly number[]): void {
    const { view } = this.page
    const outcome = proposalOutcome(view.state, a, b, ids)
    view.dispatch(setProposalFor(view.state, a, b, prop.replacement, ids))
    if (outcome !== 'ok') pulse(view, a, b, outcome)
    else if (lostSome(prop)) pulse(view, a, b, 'stripped')
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
