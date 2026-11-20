import { describe, expect, it } from 'vitest'
import { DEMOS, placeTip } from './tipPlace'

const vp = { w: 400, h: 800 }
const bubble = { w: 100, h: 40 }

describe('tooltip placement', () => {
  it('mouse: below the button, centred', () => {
    const r = placeTip({ left: 150, top: 100, right: 186, bottom: 136 }, bubble, vp, 'mouse')
    expect(r).toEqual({ x: 118, y: 142, side: 'below' })
  })

  it('mouse: flips above near the bottom edge', () => {
    const r = placeTip({ left: 150, top: 760, right: 186, bottom: 796 }, bubble, vp, 'mouse')
    expect(r.side).toBe('above')
    expect(r.y).toBe(714)
  })

  it('touch: above the button so the finger does not cover it', () => {
    const r = placeTip({ left: 150, top: 400, right: 186, bottom: 436 }, bubble, vp, 'touch')
    expect(r).toEqual({ x: 118, y: 354, side: 'above' })
  })

  it('touch at the top edge: beside the button, never under the finger', () => {
    const left = placeTip({ left: 10, top: 2, right: 46, bottom: 38 }, bubble, vp, 'touch')
    expect(left.side).toBe('end')
    expect(left.x).toBe(52)
    const right = placeTip({ left: 354, top: 2, right: 390, bottom: 38 }, bubble, vp, 'touch')
    expect(right.side).toBe('start')
    expect(right.x).toBe(248)
    expect(right.y).toBe(6)
  })

  it('touch with no room anywhere else: below', () => {
    const r = placeTip({ left: 0, top: 0, right: 400, bottom: 36 }, bubble, vp, 'touch')
    expect(r.side).toBe('below')
  })

  it('clamps into the viewport at both edges', () => {
    expect(placeTip({ left: 0, top: 100, right: 20, bottom: 120 }, bubble, vp, 'mouse').x).toBe(6)
    expect(placeTip({ left: 390, top: 100, right: 400, bottom: 120 }, bubble, vp, 'mouse').x).toBe(294)
    const tall = placeTip({ left: 0, top: 0, right: 400, bottom: 800 }, { w: 100, h: 900 }, vp, 'mouse')
    expect(tall.y).toBe(6)
  })

  it('animates only the five selection and diff actions', () => {
    expect([...DEMOS].sort()).toEqual(['acceptHunk', 'ask', 'change', 'rejectHunk', 'rewrite'])
  })
})
