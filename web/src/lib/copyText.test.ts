import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyApproved, flashIcon } from './copyText'
import { icon } from './icons'

/** innerHTML as the DOM serialises it, for comparing against a button's content. */
const html = (name: string): string => {
  const d = document.createElement('div')
  d.innerHTML = icon(name)
  return d.innerHTML
}

afterEach(() => {
  vi.useRealTimers()
})

describe('copy as WhatsApp text', () => {
  it('copies the converted saved text only while the server reports it approved', async () => {
    const written: string[] = []
    const write = (t: string) => {
      written.push(t)
      return Promise.resolve()
    }
    const on = { approved: true, text: '# Hi\n\n**bold**' }
    expect(await copyApproved({ get: () => Promise.resolve(on) }, 'd1', write)).toBe(on)
    const off = { approved: false, text: 'edited' }
    expect(await copyApproved({ get: () => Promise.resolve(off) }, 'd1', write)).toBe(off)
    expect(written).toEqual(['*Hi*\n\n*bold*'])
  })

  it('passes a clipboard failure on to the caller', async () => {
    const client = { get: () => Promise.resolve({ approved: true, text: 'x' }) }
    await expect(copyApproved(client, 'd1', () => Promise.reject(new Error('denied')))).rejects.toThrow('denied')
  })

  it('shows the check glyph for a moment, then the button icon again', () => {
    vi.useFakeTimers()
    const b = document.createElement('button')
    b.dataset.icon = 'whatsapp'
    b.innerHTML = icon('whatsapp')
    flashIcon(b, 1500)
    expect(b.dataset.icon).toBe('check')
    expect(b.innerHTML).toBe(html('check'))
    vi.advanceTimersByTime(1000)
    flashIcon(b, 1500) // a second click restarts the moment
    vi.advanceTimersByTime(1000)
    expect(b.dataset.icon).toBe('check')
    vi.advanceTimersByTime(500)
    expect(b.dataset.icon).toBe('whatsapp')
    expect(b.innerHTML).toBe(html('whatsapp'))
    expect(b.textContent).toBe('')
  })

  it('has a glyph of its own', () => {
    expect(icon('whatsapp')).not.toBe(icon('alert'))
  })
})
