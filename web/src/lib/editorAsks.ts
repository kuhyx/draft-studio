/**
 * Selection actions: questions (shown as cards) and proposals. Several may be
 * in flight at once. A selection touching pending proposals comments on them:
 * Claude gets the old replacement and the comment, and its answer replaces
 * them in place. Elsewhere a request starts a proposal of its own. The server
 * keeps the proposals (proposals.py), lands each answer once and tells every tab;
 * an answer that could not land, or lost made-up image links or screenshots,
 * pulses its span here (signals.ts). An answer the server holds (202, held.ts) keeps its span
 * tracked under the held id until the release lands through the same path.
 */
import { type Ask, api, type Held, type Proposal, type ReleasedProposal } from './api'
import { $, type EditorCore, type Hold, type Pending } from './editorCore'
import { reviseTarget } from './editorState'
import type { SelectionAction } from './editorView'
import { lostSome } from './held'
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
        api.propose(p.id, text, pending.from, pending.to, instruction, target?.previous, target?.ids),
      )
      if (prop && 'held' in prop) {
        held = true
        this.onHeld(prop.held, { pending })
      } else if (prop) {
        this.landed(prop, pending)
      }
    } finally {
      if (!held) p.pending.delete(pending)
    }
  }

  /** A held proposal the server released (it already landed there): the asking tab signals what was lost. */
  released(rel: ReleasedProposal, mine: Hold['mine']): void {
    if (mine) this.landed(rel, mine.pending)
    void this.page.pull()
  }

  /**
   * The server landed `prop` for the span this tab asked on (`at`, mapped through your edits). Your own edit
   * inside it wins silently (the span is stale); the proposal itself arrives with the state.
   */
  private landed(prop: Pick<Proposal, 'outcome' | 'stripped' | 'dropped'>, at: Pending): void {
    if (at.stale) return
    const { view } = this.page
    if (prop.outcome === 'overlap' || prop.outcome === 'gone') pulse(view, at.from, at.to, prop.outcome)
    else if (prop.outcome === 'ok' && lostSome(prop)) pulse(view, at.from, at.to, 'stripped')
    void this.page.pull()
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
