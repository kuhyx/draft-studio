/**
 * The editor's held answers (held.ts): tracks each hold by its held id, polls
 * the server, lands what it released once and collects it. The permission
 * card and the login button read `page.holds` (or the `ds-held` event on
 * document, detail: the holds) and call `poller.poke()` after a grant, deny or
 * `api.retry`, so the release shows without waiting out the backoff.
 */
import { api, type Held, type HeldListing, type ReleasedDraft, type ReleasedProposal } from './api'
import type { EditorCore, Hold } from './editorCore'
import { forgotten, HeldPoller } from './held'

export interface Landers {
  proposal: (released: ReleasedProposal, mine: Hold['mine']) => void
  draft: (released: ReleasedDraft) => void
}

export class EditorHeld {
  readonly poller: HeldPoller
  /** Released ids already landed: a failed collect must not land them twice. */
  private readonly landed = new Set<string>()
  /** Holds known when the last listing was asked for: only those can be missing from it. */
  private asked = new Set<string>()

  constructor(
    private readonly page: EditorCore,
    private readonly land: Landers,
  ) {
    this.poller = new HeldPoller({
      list: () => {
        this.asked = new Set(page.holds.keys())
        return api.held(page.id)
      },
      onListing: (listing) => this.take(listing),
      doc: document,
      win: window,
    })
  }

  /** A 202 from propose/brief/intake: keep the hold (and its tracked span) until the server releases it. */
  hold(held: Held, mine?: Hold['mine']): void {
    this.page.holds.set(held.id, { held, mine })
    this.changed()
    this.poller.watch()
  }

  private async take(listing: HeldListing): Promise<void> {
    const shown: string[] = []
    for (const rel of listing.released) {
      shown.push(rel.id)
      if (this.landed.has(rel.id)) continue
      this.landed.add(rel.id)
      if (rel.kind === 'proposal') this.land.proposal(rel, this.page.holds.get(rel.id)?.mine)
      else this.land.draft(rel)
      this.forget(rel.id)
    }
    for (const held of listing.held) this.page.holds.set(held.id, { held, mine: this.page.holds.get(held.id)?.mine })
    for (const id of forgotten(listing, this.asked)) this.forget(id)
    this.changed()
    if (shown.length > 0) await api.collect(this.page.id, shown).catch(() => 0)
  }

  private forget(id: string): void {
    const mine = this.page.holds.get(id)?.mine
    if (mine) this.page.pending.delete(mine.pending)
    this.page.holds.delete(id)
  }

  private changed(): void {
    this.page.idleStatus()
    document.dispatchEvent(new CustomEvent('ds-held', { detail: [...this.page.holds.values()].map((h) => h.held) }))
  }
}
