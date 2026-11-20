/** The status slot: saved dot, spinner, done tick or error icon (no text). */
import { errorText, label } from './i18n'
import { icon } from './icons'

export type StatusKind = 'saved' | 'unsaved' | 'busy' | 'done' | 'error'
const ICON: Record<StatusKind, string> = {
  saved: '',
  unsaved: '',
  busy: 'spinner',
  done: 'check',
  error: 'alert',
}

export function setStatus(el: HTMLElement, kind: StatusKind, code = ''): void {
  el.dataset.status = kind
  el.innerHTML = ICON[kind] ? icon(ICON[kind]) : '<span class="dot"></span>'
  el.setAttribute('aria-label', kind === 'error' ? errorText(code) : label(kind))
  if (kind === 'error') {
    el.dataset.error = code
    el.setAttribute('role', 'alert')
    el.classList.remove('shake')
    el.getBoundingClientRect() // reflow: restart the shake animation
    el.classList.add('shake')
  } else {
    el.removeAttribute('role')
    delete el.dataset.error
  }
}
