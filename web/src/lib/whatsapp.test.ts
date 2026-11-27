import fc from 'fast-check'
import { describe, expect, it } from 'vitest'
import { toWhatsapp } from './whatsapp'

/**
 * A scrubbed excerpt of a real owner guide (fictional names, numbers and
 * addresses): headings at three levels, numbered steps, a whole-line bold,
 * a quote with a bare `>` line, indented screenshots under list items.
 */
const GUIDE = [
  '# Instrukcja dla Pani Ewy: adres, hosting i Google',
  '',
  '## 1. Odnowienie adresu strony (PILNE)',
  '',
  '**Termin: adres przyklad.pl wygasa 28.12.2026. Proszę opłacić go przed tą datą.**',
  '',
  '1. Proszę otworzyć https://panel.example.com (panel firmy, u której jest adres).',
  '2. Proszę kliknąć „Domeny”.',
  '   ![Karta w panelu: lista domen, przycisk „ODNÓW”](docs-guide/01-domeny.png)',
  '3. Proszę wybrać okres odnowienia (najlepiej 1 rok).',
  '',
  '> Cześć Jan, mam prośbę w sprawie hostingu. Dziękuję, Ewa',
  '>',
  '> (Jeśli zwracacie się do siebie na „Pan/Pani”, proszę zmienić formę.)',
  '',
  '### 3a. Znalezienie profilu',
  '- Proszę wpisać *nazwę gabinetu* i dotknąć ~~drugiego~~ pierwszego wyniku.',
  '- Więcej: [instrukcja Google](https://support.google.com/x) i [https://example.com](https://example.com).',
  '',
  '![](assets/zrzut.png)',
  '',
  'Kod `ABC__1` z wiadomości.',
].join('\n')

const GUIDE_WA = [
  '*Instrukcja dla Pani Ewy: adres, hosting i Google*',
  '',
  '*1. Odnowienie adresu strony (PILNE)*',
  '',
  '*Termin: adres przyklad.pl wygasa 28.12.2026. Proszę opłacić go przed tą datą.*',
  '',
  '1. Proszę otworzyć https://panel.example.com (panel firmy, u której jest adres).',
  '2. Proszę kliknąć „Domeny”.',
  '3. Proszę wybrać okres odnowienia (najlepiej 1 rok).',
  '',
  '> Cześć Jan, mam prośbę w sprawie hostingu. Dziękuję, Ewa',
  '>',
  '> (Jeśli zwracacie się do siebie na „Pan/Pani”, proszę zmienić formę.)',
  '',
  '*3a. Znalezienie profilu*',
  '- Proszę wpisać _nazwę gabinetu_ i dotknąć ~drugiego~ pierwszego wyniku.',
  '- Więcej: instrukcja Google (https://support.google.com/x) i https://example.com.',
  '',
  'Kod ```ABC_1``` z wiadomości.',
].join('\n')

describe('markdown to WhatsApp text', () => {
  it('converts the owner guide excerpt', () => {
    expect(toWhatsapp(GUIDE)).toBe(GUIDE_WA)
  })

  it('maps each inline mark', () => {
    expect(toWhatsapp('**b** __b__ *i* _i_ ~~s~~')).toBe('*b* *b* _i_ _i_ ~s~')
    expect(toWhatsapp('**bold _it_ x**')).toBe('*bold _it_ x*')
    expect(toWhatsapp('a `x` b')).toBe('a ```x``` b')
    expect(toWhatsapp('a `` b')).toBe('a  b')
    expect(toWhatsapp('[t](u "title")')).toBe('t (u)')
    expect(toWhatsapp('[](https://a.b)')).toBe('https://a.b')
  })

  it('turns headings and stray hashes into bold lines', () => {
    expect(toWhatsapp('# Title #')).toBe('*Title*')
    expect(toWhatsapp('###### **Deep**')).toBe('*Deep*')
    expect(toWhatsapp('#tag')).toBe('tag')
    expect(toWhatsapp('# **\nx')).toBe('\nx')
    expect(toWhatsapp('#\nx')).toBe('\nx')
  })

  it('keeps lists and quotes, nesting included', () => {
    expect(toWhatsapp('* a\n+ b\n  - c\n10. d')).toBe('- a\n+ b\n  - c\n10. d')
    expect(toWhatsapp('>quote\n> > ## inner')).toBe('> quote\n> > *inner*')
  })

  it('fences code blocks and leaves their content unconverted', () => {
    expect(toWhatsapp('```js\nlet a = *b*\n```\nafter')).toBe('```\nlet a = *b*\n```\nafter')
    expect(toWhatsapp('```\nopen to the end')).toBe('```\nopen to the end\n```')
  })

  it('removes images and the lines they leave empty', () => {
    expect(toWhatsapp('a\n\n![x](y.png)\n\nb')).toBe('a\n\nb')
    expect(toWhatsapp('see ![x](y.png) here')).toBe('see here')
    expect(toWhatsapp('![x](y.png)\n\nb')).toBe('b')
    expect(toWhatsapp('a\n\n![x](y.png)')).toBe('a')
    expect(toWhatsapp('[![x](y.png)](https://h)')).toBe('https://h')
  })

  it('leaves no markdown residue behind', () => {
    expect(toWhatsapp('***\n_ _ _')).toBe('---\n---')
    expect(toWhatsapp('a ** b __ c ~~ d')).toBe('a * b _ c ~ d')
    expect(toWhatsapp('!![x')).toBe('[x')
    expect(toWhatsapp('***x***')).toBe('*_x_*')
    expect(toWhatsapp('****x**')).toBe('*x*')
  })
})

/** Mostly markdown syntax, so `**`, `![`, fences and headings come up often. */
const markdownish = fc
  .array(fc.constantFrom('*', '_', '~', '`', '#', '>', '[', ']', '!', '(', ')', ' ', '\n', '-', '1', '.', 'a', 'b', '\u{E000}'))
  .map((cs) => cs.join(''))

/** Text the converter must not touch: none of its trigger characters, no list starts, no 3+ newlines. */
const plain = fc
  .array(fc.oneof(fc.constantFrom(' ', '\n', '-', '1', '+', '.', '(', ')', '\\', '<', '='), fc.string({ maxLength: 4 })))
  .map((cs) => cs.join(''))
  .map((s) => s.replace(/[*_~`#>[\]!]/g, ' ').replace(/\n{3,}/g, '\n\n').replace(/^(- |\d)/gm, 'x'))
  .filter((s) => !/[*_~`#>[\]!]/.test(s) && !/^(- |\d)/m.test(s) && !/\n{3,}/.test(s))

describe('converter properties', () => {
  it('returns plain text unchanged', () => {
    fc.assert(fc.property(plain, (s) => toWhatsapp(s) === s), { numRuns: 500 })
  })

  it('never leaves markdown-only syntax', () => {
    fc.assert(
      fc.property(markdownish, (s) => {
        const out = toWhatsapp(s)
        return !/\*\*|__|~~|!\[|^#/m.test(out)
      }),
      { numRuns: 1000 },
    )
  })

  it('never leaves three newlines in a row', () => {
    fc.assert(fc.property(markdownish, (s) => !toWhatsapp(s).includes('\n\n\n')), { numRuns: 1000 })
  })
})
