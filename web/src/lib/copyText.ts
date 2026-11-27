/**
 * Copy as WhatsApp text. The text comes from the server, not the editor: only
 * the saved draft.md the approval hash covers may leave, so a GET that no
 * longer reports `approved` copies nothing. Success shows as a short icon
 * swap to a check glyph; the chrome has no text.
 */
import { icon } from './icons'
import { toWhatsapp } from './whatsapp'

interface Saved {
  approved: boolean
  text: string
}

/** Fetch the saved draft and, if still approved, put its WhatsApp form on the clipboard. */
export async function copyApproved<S extends Saved>(
  client: { get: (id: string) => Promise<S> },
  id: string,
  write: (text: string) => Promise<void>,
): Promise<S> {
  const s = await client.get(id)
  if (s.approved) await write(toWhatsapp(s.text))
  return s
}

const flashes = new WeakMap<HTMLElement, { name: string; timer: ReturnType<typeof setTimeout> }>()

/** Swap the button's glyph to a check for `ms`; a repeat click restarts the moment. */
export function flashIcon(btn: HTMLElement, ms: number): void {
  const prev = flashes.get(btn)
  const name = prev?.name ?? String(btn.dataset.icon)
  clearTimeout(prev?.timer)
  btn.dataset.icon = 'check'
  btn.innerHTML = icon('check')
  const timer = setTimeout(() => {
    flashes.delete(btn)
    btn.dataset.icon = name
    btn.innerHTML = icon(name)
  }, ms)
  flashes.set(btn, { name, timer })
}
