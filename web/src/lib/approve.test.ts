import { describe, expect, it } from 'vitest'
import { applyCatalog, parseCatalog } from './i18n'
import { icon } from './icons'
import { inWork, paintApprove, toggleApproval } from './approve'

describe('approve button', () => {
  it('shows approved vs not through aria-pressed and the icon', () => {
    applyCatalog(document, 'en', parseCatalog({ labels: { approve: 'Approve draft' }, errors: {} }))
    const b = document.createElement('button')
    paintApprove(b, true)
    expect(b.getAttribute('aria-pressed')).toBe('true')
    expect(b.dataset.icon).toBe('approved')
    expect(b.querySelectorAll('path')).toHaveLength(2)
    paintApprove(b, false)
    expect(b.getAttribute('aria-pressed')).toBe('false')
    expect(b.dataset.icon).toBe('approve')
    expect(b.querySelectorAll('path')).toHaveLength(1)
    expect(icon('approve')).not.toBe(icon('approved'))
    expect(b.textContent).toBe('')
  })

  it('keeps the editor open while approved', () => {
    expect(inWork('editing')).toBe(true)
    expect(inWork('approved')).toBe(true)
    expect(inWork('brief')).toBe(false)
    expect(inWork('intake')).toBe(false)
  })

  it('approves what the editor shows, or takes the approval back', async () => {
    const calls: string[] = []
    const client = {
      approve: (id: string, text: string) => {
        calls.push(`approve ${id} ${text}`)
        return Promise.resolve('on')
      },
      unapprove: (id: string) => {
        calls.push(`unapprove ${id}`)
        return Promise.resolve('off')
      },
    }
    expect(await toggleApproval(client, 'd1', false, 'text')).toBe('on')
    expect(await toggleApproval(client, 'd1', true, 'text')).toBe('off')
    expect(calls).toEqual(['approve d1 text', 'unapprove d1'])
  })
})
