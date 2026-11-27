/**
 * Markdown to WhatsApp text: WhatsApp knows only *bold*, _italic_, ~strike~,
 * ```mono```, `> ` quotes and plain list prefixes. Images have no text form,
 * so they go, along with the line they leave empty. A last pass strips every
 * markdown-only mark the patterns did not pair up (`**`, `![`, a line-leading
 * `#`), code included, so nothing raw ever reaches the chat.
 */

const IMAGE_LINE = /^\s*(?:!\[[^\]]*\]\([^)]*\)\s*)+$/
const FENCE = /^\s*```/
const HEADING = /^\s{0,3}#{1,6}\s+(.*?)(?:\s+#+)?\s*$/
const QUOTE = /^(\s*)>\s?(.*)$/
const STAR_BULLET = /^(\s*)\*(\s)/
const RULE = /^\s*([*_])(?:\s*\1){2,}\s*$/
const CODE = /(`+)([\s\S]*?)\1/g

/** A private-use character absent from the text: bold's mark while the italic pass runs. */
function placeholder(text: string): string {
  let code = 0xe000
  while (text.includes(String.fromCodePoint(code))) code++
  return String.fromCodePoint(code)
}

/** Inline marks of a span with no code in it. */
function marks(s: string, bold: string): string {
  return s
    .replace(/ ?!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]*)\]\(([^)\s]*)[^)]*\)/g, (_m, t: string, u: string) => (t === '' || t === u ? u : `${t} (${u})`))
    .replace(/\*\*\*(?=\S)(.+?)\*\*\*/g, `${bold}_$1_${bold}`)
    .replace(/\*\*(?=\S)(.+?)\*\*/g, `${bold}$1${bold}`)
    .replace(/__(?=\S)(.+?)__/g, `${bold}$1${bold}`)
    .replace(/~~(?=\S)(.+?)~~/g, '~$1~')
    .replace(/\*(?=\S)([^*]*?\S)\*/g, '_$1_')
    .replaceAll(bold, '*')
}

/** Inline conversion; code spans become ```mono``` and keep their content. */
function inline(s: string, bold: string): string {
  let out = ''
  let last = 0
  for (const m of s.matchAll(CODE)) {
    out += marks(s.slice(last, m.index), bold)
    const body = m[2]
    out += body ? `\`\`\`${body}\`\`\`` : ''
    last = m.index + m[0].length
  }
  return out + marks(s.slice(last), bold)
}

function line(s: string, bold: string): string {
  if (RULE.test(s)) return '---'
  const heading = HEADING.exec(s)
  if (heading) {
    const inner = inline(heading[1], bold).replace(/^\*+|\*+$/g, '')
    return inner ? `*${inner}*` : ''
  }
  const quote = QUOTE.exec(s)
  if (quote) {
    const rest = line(quote[2], bold)
    return `${quote[1]}>${rest ? ` ${rest}` : ''}`
  }
  return inline(s.replace(STAR_BULLET, '$1-$2'), bold)
}

/** Drop marks WhatsApp would show raw, until none is left. */
function sanitize(s: string): string {
  let prev = ''
  let out = s
  while (out !== prev) {
    prev = out
    out = out
      .replace(/\*{2,}/g, '*')
      .replace(/_{2,}/g, '_')
      .replace(/~{2,}/g, '~')
      .replace(/!+\[/g, '[')
      .replace(/^#+/gm, '')
  }
  return out
}

const blank = (s: string | undefined): boolean => s === undefined || s.trim() === ''

export function toWhatsapp(markdown: string): string {
  const bold = placeholder(markdown)
  const lines = markdown.split('\n')
  const out: string[] = []
  for (let i = 0; i < lines.length; i++) {
    const s = lines[i]
    if (FENCE.test(s)) {
      out.push('```')
      for (i++; i < lines.length && !FENCE.test(lines[i]); i++) out.push(lines[i])
      out.push('```')
    } else if (IMAGE_LINE.test(s)) {
      // The image goes with its line; a blank line on both sides would leave a double gap.
      if (blank(out.at(-1)) && blank(lines[i + 1])) {
        if (i + 1 < lines.length) i++
        else out.pop()
      }
    } else {
      out.push(line(s, bold))
    }
  }
  return sanitize(out.join('\n')).replace(/\n{3,}/g, '\n\n')
}
