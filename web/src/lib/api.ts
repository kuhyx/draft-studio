/** Typed client for draft_studio.server; Zod checks every response. */
import { z } from 'zod'
import {
  askSchema,
  errorSchema,
  gmailSchema,
  heldReplySchema,
  listItemSchema,
  listingSchema,
  metaSchema,
  proposalSchema,
  stateSchema,
  stepSchema,
  uploadSchema,
  versionSchema,
} from './apiSchemas'
import type { DraftState, Kind } from './apiSchemas'
export { stateSchema } from './apiSchemas'
export type {
  Meta,
  DraftState,
  Ask,
  Version,
  ListItem,
  Step,
  Proposal,
  PendingProposal,
  CaptureRequest,
  Held,
  HeldReply,
  HeldListing,
  Released,
  ReleasedProposal,
  ReleasedDraft,
  Kind,
  Phase,
  Author,
} from './apiSchemas'

/** A failed call; `code` keys the locale catalog's error messages. */
export class ApiError extends Error {
  constructor(readonly code: string) {
    super(code)
  }
}

/** `body` goes out as JSON, except a Blob (image upload), which goes out raw with its own type. */
async function call<T>(schema: z.ZodType<T>, method: string, path: string, body?: unknown): Promise<T> {
  let resp: Response
  const blob = body instanceof Blob ? body : null
  try {
    resp = await fetch(path, {
      method,
      headers: blob ? { 'Content-Type': blob.type } : body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: blob ?? (body === undefined ? null : JSON.stringify(body)),
      // Autosave must survive closing the tab right after typing.
      keepalive: method === 'PUT',
    })
  } catch {
    throw new ApiError('offline')
  }
  if (!resp.ok) throw await failure(resp)
  const data: unknown = await resp.json().catch(() => null)
  const parsed = schema.safeParse(data)
  if (!parsed.success) throw new ApiError('bad-response')
  return parsed.data
}

/** A save based on an old version: `state` is what the server holds now (the tab rebases on it). */
export class StaleError extends ApiError {
  constructor(readonly state: DraftState) {
    super('stale')
  }
}

/** The error code a failed response carries ({"error": code}), else "server". */
async function failure(resp: Response): Promise<ApiError> {
  const parsed = errorSchema.safeParse(await resp.json().catch(() => null))
  if (parsed.success && parsed.data.state) return new StaleError(parsed.data.state)
  return new ApiError(parsed.success ? parsed.data.error : 'server')
}

/** The download name from Content-Disposition: the UTF-8 filename* if it decodes, else filename. */
export function fileName(header: string | null): string {
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header ?? '')?.[1]
  if (star) {
    try {
      return decodeURIComponent(star)
    } catch {
      // A malformed escape: fall back to the plain ASCII name.
    }
  }
  return /filename="([^"]+)"/i.exec(header ?? '')?.[1] ?? 'draft.pdf'
}

/** The approved draft printed to PDF by the server. */
async function pdf(id: string): Promise<{ blob: Blob; name: string }> {
  let resp: Response
  try {
    resp = await fetch(`${d(id)}/export.pdf`)
  } catch {
    throw new ApiError('offline')
  }
  if (!resp.ok) throw await failure(resp)
  return { blob: await resp.blob(), name: fileName(resp.headers.get('Content-Disposition')) }
}

const d = (id: string) => `/api/drafts/${encodeURIComponent(id)}`

export const api = {
  list: async () => (await call(z.object({ drafts: z.array(listItemSchema) }), 'GET', '/api/drafts')).drafts,
  create: (kind: Kind) => call(metaSchema, 'POST', '/api/drafts', { kind }),
  remove: (id: string) => call(z.object({ ok: z.boolean() }), 'DELETE', d(id)),
  get: (id: string) => call(stateSchema, 'GET', d(id)),
  /** `base` in the patch: the version the text grew from; an older one throws StaleError. */
  save: (id: string, patch: Record<string, unknown>) => call(stateSchema, 'PUT', d(id), patch),
  history: async (id: string) => (await call(z.object({ versions: z.array(versionSchema) }), 'GET', `${d(id)}/history`)).versions,
  restore: (id: string, v: number) => call(stateSchema, 'POST', `${d(id)}/restore`, { v }),
  brief: (id: string, brief: string) => call(z.union([stepSchema, heldReplySchema]), 'POST', `${d(id)}/brief`, { brief }),
  intake: (id: string, answers: string[]) => call(z.union([stepSchema, heldReplySchema]), 'POST', `${d(id)}/intake`, { answers }),
  ask: (id: string, text: string, from: number, to: number, question: string) =>
    call(askSchema, 'POST', `${d(id)}/ask`, { text, from, to, question }),
  /** previous: the pending replacement for [from, to) that the instruction comments on. */
  propose: (id: string, text: string, from: number, to: number, instruction: string, previous?: string, revises?: readonly number[]) =>
    call(z.union([proposalSchema, heldReplySchema]), 'POST', `${d(id)}/propose`, { text, from, to, instruction, previous, revises }),
  /** Apply a pending proposal (all of it, or `text` for some hunks) to the draft; StaleError when its rev moved. */
  accept: (id: string, pid: number, rev: number, text?: string) =>
    call(stateSchema, 'POST', `${d(id)}/proposals/${String(pid)}/accept`, { rev, text }),
  /** Drop a pending proposal (all of it, or down to `replacement` for the hunks kept). */
  reject: (id: string, pid: number, rev: number, replacement?: string) =>
    call(stateSchema, 'POST', `${d(id)}/proposals/${String(pid)}/reject`, { rev, replacement }),
  held: (id: string) => call(listingSchema, 'GET', `${d(id)}/held`),
  /** Re-run a hold's login/busy captures (after `capture login`); answers the new listing. */
  retry: (id: string, hid: string) => call(listingSchema, 'POST', `${d(id)}/held`, { retry: hid }),
  /** Open the login window for one of a hold's `login` origins; the server retries the hold when it closes. */
  login: (id: string, hid: string, origin: string) =>
    call(z.object({ started: z.literal(true) }), 'POST', `${d(id)}/held/${encodeURIComponent(hid)}/login`, { origin }),
  /**
   * Allow one refused capture step (`allow`: only these blocked pairs, the rest stay denied): the server
   * re-runs the capture (up to its timeout) and moves the
   * held answer; a 409/422 there only means the hold moved on (permission card: editorCard.ts).
   */
  grant: (rid: string, allow?: readonly (readonly [string, string])[]) =>
    call(z.looseObject({}), 'POST', `/api/captures/${encodeURIComponent(rid)}/grant`, allow ? { allow } : {}),
  /**
   * Keep that request blocked and shoot anyway: the server re-runs the capture with it denied (never sent),
   * so the answer, like a grant's, is the asset or the next refusal; the held answer moves the same way.
   */
  deny: (rid: string) => call(z.looseObject({}), 'POST', `/api/captures/${encodeURIComponent(rid)}/deny`, {}),
  /** Forget released answers the page has shown. */
  collect: async (id: string, ids: string[]) =>
    (await call(z.object({ collected: z.number().int() }), 'POST', `${d(id)}/held`, { collect: ids })).collected,
  gmail: (id: string) => call(gmailSchema, 'POST', `${d(id)}/gmail`, {}),
  pdf,
  approve: (id: string, text: string) => call(stateSchema, 'POST', `${d(id)}/approve`, { text }),
  unapprove: (id: string) => call(stateSchema, 'POST', `${d(id)}/unapprove`, {}),
  upload: (id: string, file: File) =>
    call(uploadSchema, 'POST', `${d(id)}/assets?name=${encodeURIComponent(file.name)}`, file),
  locales: async () => (await call(z.object({ locales: z.array(z.string()) }), 'GET', '/api/locales')).locales,
  version: async () => (await call(z.object({ build: z.string() }), 'GET', '/version')).build,
}
