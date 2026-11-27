/**
 * The capture permission card in the editor: one card per held answer, in
 * the right column on wide screens (instead of the asks) and docked under the
 * text on phones; while the first draft itself is held (#work hidden) it sits
 * under the brief/intake form. Repaints on every `ds-held` listing
 * (editorHeld.ts), keeps each hold's colour, and lights the held span of the
 * card under the pointer (heldSpans.ts). Blocked pairs are decided row by row (editorPicks.ts), sent
 * once all are. After every grant, deny, login or retry it pokes the poller so the release shows at once.
 *
 * A grant or deny that answers 409/422 only means the hold moved on (another
 * refusal, a login, a busy profile, another tab acted): that is shown by the
 * next listing, not as an error. Only real failures reach the page's status.
 */
import { api, ApiError, type CaptureRequest } from './api'
import type { Hold } from './editorCore'
import { Picks } from './editorPicks'
import type { HeldSpan } from './heldSpans'
import { hued, holdSpans } from './cardHues'
import { heldCard, loginKey, retryKey, shotView, signature, type CardState } from './permCard'

export interface CardDeps {
  readonly id: string
  readonly holds: ReadonlyMap<string, Hold>
  readonly fail: (err: unknown) => void
  readonly poke: () => void
  readonly spans: (spans: HeldSpan[]) => void
  /** The editor column, and where the card goes while it is hidden. */
  readonly work: HTMLElement
  readonly after: HTMLElement
  readonly doc: Document
}

const LOUD = new Set(['offline', 'server', 'bad-response', 'login-failed'])
const loud = (err: unknown): boolean => !(err instanceof ApiError) || LOUD.has(err.code)

export class EditorCard {
  readonly desk: HTMLElement
  readonly dialog: HTMLDialogElement
  private readonly working = new Set<string>()
  /** Request ids whose call returned: their spinner stops with the next listing. */
  private readonly settled = new Set<string>()
  private readonly retrying = new Set<string>()
  /** Login key -> the hold and its signature at the click: spins until the hold changes. */
  private readonly waiting = new Map<string, { hid: string; sig: string }>()
  private readonly opened = new Set<string>()
  private readonly picks = new Picks()
  private readonly hues = new Map<string, number>()
  private lit: string | null = null
  private zoom: string | null = null
  private painted = ''
  private marked = ''
  private readonly onHeld = () => {
    this.listing()
  }

  constructor(private readonly deps: CardDeps) {
    this.desk = deps.doc.createElement('aside')
    this.desk.id = 'held-desk'
    this.desk.className = 'held-desk'
    this.desk.hidden = true
    this.dialog = deps.doc.createElement('dialog')
    this.dialog.className = 'shot-view'
    deps.doc.body.append(this.dialog)
    deps.work.append(this.desk)
    for (const root of [this.desk, this.dialog]) {
      root.addEventListener('click', (e) => {
        this.click(e.target as Element)
      })
    }
    this.desk.addEventListener('pointerover', (e) => {
      this.light((e.target as Element).closest<HTMLElement>('.held')?.dataset.held ?? null)
    })
    this.desk.addEventListener('pointerleave', () => {
      this.light(null)
    })
    deps.doc.addEventListener('ds-held', this.onHeld)
  }

  destroy(): void {
    this.deps.doc.removeEventListener('ds-held', this.onHeld)
  }

  /** A new listing: settle what it answers, then repaint. */
  private listing(): void {
    const live = new Set([...this.deps.holds.values()].flatMap((h) => h.held.requests.map((r) => r.id)))
    for (const rid of [...this.working]) if (this.settled.has(rid) || !live.has(rid)) this.working.delete(rid)
    this.settled.clear()
    this.picks.prune(live)
    for (const [key, { hid, sig }] of this.waiting) {
      const h = this.deps.holds.get(hid)
      if (!h || signature(h.held) !== sig) this.waiting.delete(key)
    }
    this.render()
  }

  private render(): void {
    const holds = hued(this.deps.holds, this.hues)
    const spans = holdSpans(holds, this.lit)
    const working = new Set([...this.working, ...this.retrying])
    const state: CardState = { working, waiting: new Set(this.waiting.keys()), opened: this.opened, picks: this.picks.view }
    const marks = JSON.stringify(spans)
    if (marks !== this.marked) {
      this.marked = marks
      this.deps.spans(spans)
    }
    // Hover only relights the span: rebuilding the card under the pointer would eat the click.
    const cards = holds.map((h) => [h.held, h.hue])
    const key = JSON.stringify([cards, [...working], [...state.waiting], [...this.opened], this.picks.key(), this.deps.work.hidden])
    if (key === this.painted) return
    this.painted = key
    this.desk.replaceChildren(...holds.map((h) => heldCard(h.held, h.hue, state)))
    this.desk.hidden = holds.length === 0
    if (this.deps.work.hidden) this.deps.after.after(this.desk)
    else this.deps.work.append(this.desk)
    const shown = this.request(this.zoom)
    if (shown) this.dialog.replaceChildren(shotView(shown, state))
    else this.dialog.close()
  }

  private request(rid: string | null): CaptureRequest | undefined {
    for (const h of this.deps.holds.values()) {
      const found = h.held.requests.find((r) => r.id === rid)
      if (found) return found
    }
    return undefined
  }

  private light(hid: string | null): void {
    this.lit = hid
    this.render()
  }

  private click(target: Element): void {
    const b = target.closest<HTMLElement>('[data-act]')
    const act = b?.dataset.act
    const rid = b?.closest<HTMLElement>('[data-rid]')?.dataset.rid ?? ''
    const hid = b?.closest<HTMLElement>('[data-held]')?.dataset.held ?? ''
    switch (act) {
      case 'zoom':
        this.zoom = rid
        this.painted = ''
        this.render()
        this.dialog.showModal()
        return
      case 'close':
        this.zoom = null
        this.dialog.close()
        return
      case 'detail': {
        const i = b?.closest<HTMLElement>('[data-i]')?.dataset.i
        const key = i === undefined ? rid : `${rid} ${i}`
        if (!this.opened.delete(key)) this.opened.add(key)
        this.render()
        return
      }
      case 'grant':
        void this.decide(rid, () => api.grant(rid))
        return
      case 'deny':
        void this.decide(rid, () => api.deny(rid))
        return
      case 'send':
        this.send(rid)
        return
      case 'login':
        void this.login(hid, String(b?.dataset.origin))
        return
      case 'retry':
        void this.retry(hid)
        return
      case 'dismiss':
        void this.dismiss(hid)
        return
      default:
        if (this.picks.act(this.request(rid), act, Number(b?.closest<HTMLElement>('[data-i]')?.dataset.i))) this.render()
    }
  }

  /** Send a card's row decisions: partial grant, whole grant or deny (grantPick.ts `grantCall`). */
  private send(rid: string): void {
    const call = this.picks.call(this.request(rid))
    if (!call) return // the request left before its listing repainted the card
    void this.decide(rid, () => (call.route === 'deny' ? api.deny(rid) : api.grant(rid, call.allow)))
  }

  private async decide(rid: string, run: () => Promise<unknown>): Promise<void> {
    this.working.add(rid)
    this.zoom = null
    this.render()
    try {
      await run()
    } catch (err) {
      if (loud(err)) {
        this.working.delete(rid)
        this.deps.fail(err)
        this.render()
      }
    }
    this.settled.add(rid)
    this.deps.poke()
  }

  private async login(hid: string, origin: string): Promise<void> {
    const key = loginKey(hid, origin)
    const h = this.deps.holds.get(hid)
    if (!h) return
    this.waiting.set(key, { hid, sig: signature(h.held) })
    this.render()
    try {
      await api.login(this.deps.id, hid, origin)
    } catch (err) {
      this.waiting.delete(key)
      if (loud(err)) this.deps.fail(err)
      this.render()
      if (err instanceof ApiError && err.code === 'profile-busy') this.shake(key)
    }
    this.deps.poke()
  }

  /** profile-busy: the row shakes, its button is back (a login window is open elsewhere). */
  private shake(key: string): void {
    for (const row of this.desk.querySelectorAll<HTMLElement>('.login')) {
      if (row.dataset.key === key) row.classList.add('shake')
    }
  }

  /** A failed hold goes: the server forgets it, the next listing drops its card and span. */
  private async dismiss(hid: string): Promise<void> {
    try {
      await api.collect(this.deps.id, [hid])
    } catch (err) {
      if (loud(err)) this.deps.fail(err)
    }
    this.deps.poke()
  }

  private async retry(hid: string): Promise<void> {
    const key = retryKey(hid)
    this.retrying.add(key)
    this.render()
    try {
      await api.retry(this.deps.id, hid)
    } catch (err) {
      if (loud(err)) this.deps.fail(err)
    }
    this.retrying.delete(key)
    this.render()
    this.deps.poke()
  }
}
