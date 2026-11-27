/** One open intake question with its answer box. */
export function questionRow(question: string, i: number, lang: string): HTMLElement {
  const row = document.createElement('label')
  row.className = 'question'
  const text = document.createElement('span')
  text.className = 'q-text'
  text.textContent = question
  text.dir = 'auto'
  const input = document.createElement('textarea')
  input.rows = 2
  input.name = `a${i}`
  input.lang = lang
  input.dir = 'auto'
  row.append(text, input)
  return row
}
