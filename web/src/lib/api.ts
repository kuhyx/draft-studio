/** Typed client for draft_studio.server; Zod checks every response. */
import { z } from 'zod'

const rangeSchema = z.tuple([z.number().int(), z.number().int()])
const askSchema = z.object({
  from: z.number().int(),
  to: z.number().int(),
  span: z.string(),
  question: z.string(),
  answer: z.string(),
  ts: z.number(),
})
const authorSchema = z.enum(['you', 'claude'])
const metaSchema = z.object({
  id: z.string(),
  kind: z.enum(['mail', 'text']),
  lang: z.string(),
  to: z.string(),
  subject: z.string(),
  toAuthor: authorSchema,
  subjectAuthor: authorSchema,
  gmailDraftId: z.string(),
  brief: z.string(),
  intake: z.array(z.object({ q: z.string(), a: z.string().nullable(), round: z.number().int() })),
  phase: z.enum(['brief', 'intake', 'editing', 'approved']),
  approvedSha256: z.string().optional(),
  asks: z.array(askSchema),
  claude: z.array(rangeSchema),
  version: z.number().int(),
  updated: z.number(),
})
/** A pending proposal the server holds for the draft (draft_studio/proposals.py): positions on the saved text. */
const pendingSchema = z.object({
  id: z.number().int(),
  rev: z.number().int(),
  from: z.number().int(),
  to: z.number().int(),
  replacement: z.string(),
})
/**
 * `approved`: the server re-checked the stored hash against draft.md on disk. `proposals`: Claude's pending
 * proposals; `gone`: how the last ones ended (stale: an edit touched them; accepted; rejected).
 */
const stateSchema = z.object({
  meta: metaSchema,
  text: z.string(),
  claude: z.array(rangeSchema),
  approved: z.boolean(),
  proposals: z.array(pendingSchema).default([]),
  gone: z.record(z.string(), z.string()).default({}),
})
const listItemSchema = metaSchema.extend({ preview: z.string() })
const versionSchema = z.object({
  v: z.number().int().optional(),
  ts: z.number(),
  type: z.string(),
  text: z.string().optional(),
  claude: z.array(rangeSchema).optional(),
  question: z.string().optional(),
  answer: z.string().optional(),
})
/** `stripped`: image links Claude made up that the server cut from its answer. */
const strippedSchema = z.array(z.string()).default([])
/** `dropped`: Claude's screenshots that could not be taken; their lines were cut (capture/__init__.py). */
const droppedSchema = z.array(z.object({ key: z.string(), reason: z.string() })).default([])
const stepSchema = z.object({
  questions: z.array(z.string()),
  state: stateSchema,
  stripped: strippedSchema,
  dropped: droppedSchema,
})
const uploadSchema = z.object({ name: z.string(), md: z.string() })
/** How the server landed it: ok; stale (your edit touched the span); gone (a revised one vanished); overlap. */
const outcomeSchema = z.enum(['ok', 'stale', 'gone', 'overlap']).default('ok')
const proposalSchema = z.object({
  replacement: z.string(),
  from: z.number().int(),
  to: z.number().int(),
  outcome: outcomeSchema,
  stripped: strippedSchema,
  dropped: droppedSchema,
})
/** A capture refusal kuhy grants or denies; loose: the permission card reads the other fields too. */
const requestSchema = z.looseObject({ id: z.string(), reason: z.string(), detail: z.string(), shot: z.string() })
/** An answer the server holds until a capture gets its permission, login or free profile. */
const heldSchema = z.object({
  id: z.string(),
  kind: z.enum(['proposal', 'draft']),
  from: z.number().int().nullable(),
  to: z.number().int().nullable(),
  requests: z.array(requestSchema),
  login: z.array(z.string()),
  busy: z.boolean(),
  /** Retries run so far: moves even when a retry fails the same way. */
  tries: z.number().int().default(0),
})
const heldReplySchema = z.object({ held: heldSchema })
const releasedSchema = z.discriminatedUnion('kind', [
  proposalSchema.extend({ id: z.string(), kind: z.literal('proposal'), span: z.string(), previous: z.string().nullable() }),
  stepSchema.extend({ id: z.string(), kind: z.literal('draft') }),
])
const listingSchema = z.object({ held: z.array(heldSchema), released: z.array(releasedSchema) })
const gmailSchema = z.object({ id: z.string(), created: z.boolean() })
const errorSchema = z.object({ error: z.string(), state: stateSchema.optional() })

export type Meta = z.infer<typeof metaSchema>
export type DraftState = z.infer<typeof stateSchema>
export type Ask = z.infer<typeof askSchema>
export type Version = z.infer<typeof versionSchema>
export type ListItem = z.infer<typeof listItemSchema>
export type Step = z.infer<typeof stepSchema>
export type Proposal = z.infer<typeof proposalSchema>
export type PendingProposal = z.infer<typeof pendingSchema>
export type CaptureRequest = z.infer<typeof requestSchema>
export type Held = z.infer<typeof heldSchema>
export type HeldReply = z.infer<typeof heldReplySchema>
export type HeldListing = z.infer<typeof listingSchema>
export type Released = HeldListing['released'][number]
export type ReleasedProposal = Extract<Released, { kind: 'proposal' }>
export type ReleasedDraft = Extract<Released, { kind: 'draft' }>
export type Kind = Meta['kind']
export type Phase = Meta['phase']
export type Author = z.infer<typeof authorSchema>

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
   * Allow one refused capture step: the server re-runs the capture (up to its timeout) and moves the
   * held answer; a 409/422 there only means the hold moved on (permission card: editorCard.ts).
   */
  grant: (rid: string) => call(z.looseObject({}), 'POST', `/api/captures/${encodeURIComponent(rid)}/grant`, {}),
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
