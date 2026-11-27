/**
 * Accept or reject pending hunks: the server applies it to the draft (proposalapi.py), so every tab
 * follows through the change feed. This tab then pulls the saved state at once. A 409 only means the
 * proposal moved on (Claude revised it, another tab resolved it): the pull shows what is left.
 */
import { api, StaleError } from './api'
import type { EditorCore } from './editorCore'
import { type HunkPick, resolutions } from './editorState'

export async function resolveHunks(page: EditorCore, action: 'accept' | 'reject', which: HunkPick): Promise<void> {
  const jobs = resolutions(page.view.state, action, which)
  if (jobs.length === 0) return
  await page.busy(async () => {
    for (const { id, rev, text } of jobs) {
      try {
        await (action === 'accept' ? api.accept(page.id, id, rev, text) : api.reject(page.id, id, rev, text))
      } catch (err) {
        if (!(err instanceof StaleError)) throw err
      }
    }
  })
  // The accepted text is a new edit for the undo walk (editorHistory.ts).
  if (action === 'accept') page.cursor = Number.POSITIVE_INFINITY
  await page.pull()
}
