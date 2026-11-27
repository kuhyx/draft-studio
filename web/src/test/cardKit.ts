/** Shared set-up of the permission card tests (editorCard*.test.ts): a page stub, holds and helpers. */
import { type Mock, vi } from 'vitest'
import type { CaptureRequest, Held } from '../lib/api'
import { EditorCard, type CardDeps } from '../lib/editorCard'
import type { Hold } from '../lib/editorCore'
import type { HeldSpan } from '../lib/heldSpans'

export const req = (id: string): CaptureRequest => ({
  id,
  reason: 'request',
  detail: `POST https://a.example/${id}`,
  shot: `/s/${id}`,
})

export const held = (id: string, over: Partial<Held> = {}): Held => ({
  id,
  kind: 'proposal',
  from: 0,
  to: 3,
  requests: [],
  login: [],
  busy: false,
  tries: 0,
  failed: [],
  ...over,
})

/** A promise the test settles by hand, so the in-flight state can be checked. */
export function gate<T>(): { promise: Promise<T>; resolve: (v: T) => void } {
  let resolve: (v: T) => void = () => undefined
  const promise = new Promise<T>((ok) => {
    resolve = ok
  })
  return { promise, resolve }
}

export const flush = (): Promise<unknown> => new Promise((r) => setTimeout(r, 0))
export const changed = (): boolean => document.dispatchEvent(new CustomEvent('ds-held'))
export const q = (sel: string): HTMLButtonElement | null => document.querySelector<HTMLButtonElement>(sel)

export function click(sel: string): void {
  const el = q(sel)
  if (!el) throw new Error(`missing ${sel}`)
  el.click()
}

export interface Kit {
  deps: CardDeps & { holds: Map<string, Hold>; fail: Mock<(err: unknown) => void>; poke: Mock<() => void> }
  spans: HeldSpan[][]
  card: EditorCard
  /** Replace the holds and send the listing event. */
  show: (...holds: Held[]) => void
}

/** A fresh page (#intake, #work), a dialog that opens in jsdom, and a card on it. */
export function mountCard(): Kit {
  document.body.innerHTML = '<form id="intake"></form><main id="work"></main>'
  HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
    this.open = true
  }
  HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
    this.open = false
  }
  const spans: HeldSpan[][] = []
  const deps: Kit['deps'] = {
    id: 'd1',
    holds: new Map(),
    fail: vi.fn<(err: unknown) => void>(),
    poke: vi.fn<() => void>(),
    spans: (s) => spans.push(s),
    work: q('#work') ?? document.body,
    after: q('#intake') ?? document.body,
    doc: document,
  }
  const show = (...holds: Held[]) => {
    deps.holds.clear()
    for (const h of holds) deps.holds.set(h.id, { held: h })
    changed()
  }
  return { deps, spans, card: new EditorCard(deps), show }
}
