/** Pure tooltip placement and the list of buttons that get an animated demo. */

export interface Box {
  left: number
  top: number
  right: number
  bottom: number
}
export interface Size {
  w: number
  h: number
}
export type Side = 'above' | 'below' | 'start' | 'end'
export interface Placement {
  x: number
  y: number
  side: Side
}

/** Only these actions get a looping demo; every other button shows text only. */
export const DEMOS: ReadonlySet<string> = new Set(['ask', 'rewrite', 'change', 'acceptHunk', 'rejectHunk'])

const GAP = 6
const EDGE = 6

const clamp = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(v, Math.max(lo, hi)))

/**
 * Where the bubble goes. Mouse: below, else above. Touch: above (the finger
 * covers what is below), else beside the button, else below as a last resort.
 * x/y are clamped so the bubble never leaves the viewport.
 */
export function placeTip(a: Box, tip: Size, vp: Size, input: 'mouse' | 'touch'): Placement {
  const cx = clamp((a.left + a.right) / 2 - tip.w / 2, EDGE, vp.w - tip.w - EDGE)
  const cy = clamp((a.top + a.bottom) / 2 - tip.h / 2, EDGE, vp.h - tip.h - EDGE)
  const above = a.top - GAP - tip.h
  const below = a.bottom + GAP
  const fitsAbove = above >= EDGE
  const fitsBelow = below + tip.h <= vp.h - EDGE
  if (input === 'mouse') {
    if (fitsBelow || !fitsAbove) return { x: cx, y: clamp(below, EDGE, vp.h - tip.h - EDGE), side: 'below' }
    return { x: cx, y: above, side: 'above' }
  }
  if (fitsAbove) return { x: cx, y: above, side: 'above' }
  const end = a.right + GAP
  if (end + tip.w <= vp.w - EDGE) return { x: end, y: cy, side: 'end' }
  const start = a.left - GAP - tip.w
  if (start >= EDGE) return { x: start, y: cy, side: 'start' }
  return { x: cx, y: clamp(below, EDGE, vp.h - tip.h - EDGE), side: 'below' }
}
