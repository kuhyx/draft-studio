/** Inline SVG icons (24x24, stroke = currentColor). The chrome has no text. */
const P: Partial<Record<string, string>> = {
  mailPlus: '<path d="M3 6h13v12H3z"/><path d="m3 6 6.5 6L16 6"/><path d="M19 8v6M16 11h6"/>',
  textPlus: '<path d="M5 3h9l4 4v6"/><path d="M5 3v18h9"/><path d="M8 9h6M8 13h5"/><path d="M19 15v6M16 18h6"/>',
  mail: '<path d="M3 6h18v12H3z"/><path d="m3 6 9 7 9-7"/>',
  text: '<path d="M6 3h9l4 4v14H6z"/><path d="M9 10h7M9 14h7M9 18h4"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6"/><path d="M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  back: '<path d="M15 5 8 12l7 7"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14 0 18M12 3c-3 3.5-3 14 0 18"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
  redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h3"/>',
  history: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
  send: '<path d="M4 12 20 4l-6 16-3-7z"/><path d="m11 13 9-9"/>',
  ask: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 1-1 1.7v.5"/><path d="M12 17.5v.01"/>',
  rewrite: '<path d="M20 11a8 8 0 0 0-14.6-4.5L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.6 4.5L20 16"/><path d="M20 20v-4h-4"/>',
  change: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13 7 4 4"/>',
  check: '<path d="m5 12 5 5 9-10"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  checkAll: '<path d="m2 12 5 5 9-10"/><path d="m11 16 1 1 9-10"/>',
  xAll: '<path d="M4 6l10 12M14 6 4 18"/><path d="M14 9l6 6M20 9l-6 6"/>',
  gmail: '<path d="M3 6h14v12H3z"/><path d="m3 6 7 6 7-6"/><path d="M17 15h5M19.5 12.5 22 15l-2.5 2.5"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5v.01"/>',
  spinner: '<path d="M12 3a9 9 0 1 0 9 9"/>',
  to: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  subject: '<path d="M5 5h14M12 5v14"/>',
  lang: '<path d="M4 5h9M8.5 3v2M6 5c1 4 4 7 7 8M11 5c-1 4-4 7-7 8"/><path d="m13 21 4-9 4 9M14.5 18h5"/>',
  quote: '<path d="M7 7h4v4c0 3-2 5-4 6M15 7h4v4c0 3-2 5-4 6"/>',
  approve: '<path d="M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.5 7-10V6z"/>',
  approved: '<path d="M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.5 7-10V6z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  pdf: '<path d="M6 3h9l4 4v14H6z"/><path d="M12 9v7M9 13l3 3 3-3"/>',
  whatsapp: '<path d="M4 20l1.4-4.2A8.5 8.5 0 1 1 8.4 18.7z"/><path d="M9 8.5c0 3.6 2.9 6.5 6.5 6.5l.9-1.6-2-1.1-1 .9a4.5 4.5 0 0 1-2.2-2.2l.9-1-1.1-2z"/>',
  imageOff: '<path d="M4 5h16v14H4z"/><path d="m4 16 5-5 4 4 2-2 5 5"/><circle cx="15.5" cy="9" r="1.5"/><path d="M3 3l18 18"/>',
  // The capture permission card (permCard.ts).
  login: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M16 7l3 3M14 9l2 2"/>',
  retry: '<path d="M20 12a8 8 0 1 1-2.4-5.7"/><path d="M20 4v5h-5"/>',
  expand: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  hourglass: '<path d="M7 3h10M7 21h10"/><path d="M8 3c0 6 8 6 8 9s-8 3-8 9M16 3c0 6-8 6-8 9s8 3 8 9"/>',
  ban: '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
  post: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 20h16"/>',
  keys: '<path d="M3 7h18v10H3z"/><path d="M7 11h.01M11 11h.01M15 11h.01M8 14h8"/>',
  swap: '<path d="M7 4v14M4 15l3 3 3-3"/><path d="M17 20V6M14 9l3-3 3 3"/>',
  form: '<path d="M4 4h16v16H4z"/><path d="M8 9h8M8 13h4"/><path d="M13 17h5M16 15l2 2-2 2"/>',
}

const ALERT = '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5v.01"/>'

export function icon(name: string): string {
  const body = P[name] ?? ALERT
  return `<svg class="icon" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`
}

export const iconNames = Object.keys(P)

/** An icon-only button; its accessible name comes from the locale catalog. */
export function iconButton(name: string, labelKey: string, cls = ''): HTMLButtonElement {
  const b = document.createElement('button')
  b.type = 'button'
  b.className = `icon-btn ${cls}`.trim()
  b.dataset.label = labelKey
  b.dataset.icon = name
  b.innerHTML = icon(name)
  return b
}
