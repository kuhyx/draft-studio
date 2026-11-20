import { describe, expect, it } from 'vitest'
import { applyCatalog, parseCatalog } from './i18n'
import { icon, iconButton, iconNames } from './icons'
import { setStatus } from './status'

describe('status slot', () => {
  it('shows icon states with catalog labels and an alert on error', () => {
    applyCatalog(document, 'en', parseCatalog({ labels: { busy: 'Busy' }, errors: { claude: 'Claude failed' } }))
    const el = document.createElement('span')
    setStatus(el, 'busy')
    expect(el.dataset.status).toBe('busy')
    expect(el.getAttribute('aria-label')).toBe('Busy')
    expect(el.querySelector('svg')).not.toBeNull()
    setStatus(el, 'error', 'claude')
    expect(el.getAttribute('role')).toBe('alert')
    expect(el.getAttribute('aria-label')).toBe('Claude failed')
    expect(el.classList.contains('shake')).toBe(true)
    setStatus(el, 'saved')
    expect(el.getAttribute('role')).toBeNull()
    expect(el.dataset.error).toBeUndefined()
    expect(el.querySelector('.dot')).not.toBeNull()
  })
})

describe('icons', () => {
  it('renders every icon, unknown names fall back to alert', () => {
    for (const name of iconNames) expect(icon(name)).toContain('<svg')
    expect(icon('nope')).toBe(icon('alert'))
  })

  it('icon buttons carry no visible text', () => {
    const b = iconButton('undo', 'undo', 'extra')
    expect(b.textContent).toBe('')
    expect(b.dataset.label).toBe('undo')
    expect(b.className).toBe('icon-btn extra')
    expect(iconButton('x', 'close').className).toBe('icon-btn')
  })
})
