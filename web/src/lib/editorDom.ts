/** Shared pieces of the editor page: the typed DOM lookup and the span / hold records. */
import type { Held } from './api'

// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- typed DOM lookup
export const $ = <T extends HTMLElement>(sel: string): T => {
  const el = document.querySelector<T>(sel)
  if (!el) throw new Error(`missing ${sel}`)
  return el
}

export interface Pending {
  from: number
  to: number
  stale: boolean
}

/** A held answer (held.ts); `mine`: the span this page asked on, still tracked in `pending`. */
export interface Hold {
  held: Held
  mine?: { pending: Pending }
}

