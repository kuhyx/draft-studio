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
  phase: z.enum(['brief', 'intake', 'editing']),
  asks: z.array(askSchema),
  claude: z.array(rangeSchema),
  version: z.number().int(),
  updated: z.number(),
})
const stateSchema = z.object({ meta: metaSchema, text: z.string(), claude: z.array(rangeSchema) })
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
const stepSchema = z.object({ questions: z.array(z.string()), state: stateSchema })
const uploadSchema = z.object({ name: z.string(), md: z.string() })
const proposalSchema = z.object({ replacement: z.string(), from: z.number().int(), to: z.number().int() })
const gmailSchema = z.object({ id: z.string(), created: z.boolean() })
const errorSchema = z.object({ error: z.string() })

export type Meta = z.infer<typeof metaSchema>
export type DraftState = z.infer<typeof stateSchema>
export type Ask = z.infer<typeof askSchema>
export type Version = z.infer<typeof versionSchema>
export type ListItem = z.infer<typeof listItemSchema>
export type Step = z.infer<typeof stepSchema>
export type Kind = Meta['kind']
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
  const data: unknown = await resp.json().catch(() => null)
  if (!resp.ok) {
    const parsed = errorSchema.safeParse(data)
    throw new ApiError(parsed.success ? parsed.data.error : 'server')
  }
  const parsed = schema.safeParse(data)
  if (!parsed.success) throw new ApiError('bad-response')
  return parsed.data
}

const d = (id: string) => `/api/drafts/${encodeURIComponent(id)}`

export const api = {
  list: async () => (await call(z.object({ drafts: z.array(listItemSchema) }), 'GET', '/api/drafts')).drafts,
  create: (kind: Kind) => call(metaSchema, 'POST', '/api/drafts', { kind }),
  remove: (id: string) => call(z.object({ ok: z.boolean() }), 'DELETE', d(id)),
  get: (id: string) => call(stateSchema, 'GET', d(id)),
  save: (id: string, patch: Record<string, unknown>) => call(stateSchema, 'PUT', d(id), patch),
  history: async (id: string) => (await call(z.object({ versions: z.array(versionSchema) }), 'GET', `${d(id)}/history`)).versions,
  restore: (id: string, v: number) => call(stateSchema, 'POST', `${d(id)}/restore`, { v }),
  brief: (id: string, brief: string) => call(stepSchema, 'POST', `${d(id)}/brief`, { brief }),
  intake: (id: string, answers: string[]) => call(stepSchema, 'POST', `${d(id)}/intake`, { answers }),
  ask: (id: string, text: string, from: number, to: number, question: string) =>
    call(askSchema, 'POST', `${d(id)}/ask`, { text, from, to, question }),
  propose: (id: string, text: string, from: number, to: number, instruction: string) =>
    call(proposalSchema, 'POST', `${d(id)}/propose`, { text, from, to, instruction }),
  gmail: (id: string) => call(gmailSchema, 'POST', `${d(id)}/gmail`, {}),
  upload: (id: string, file: File) =>
    call(uploadSchema, 'POST', `${d(id)}/assets?name=${encodeURIComponent(file.name)}`, file),
  locales: async () => (await call(z.object({ locales: z.array(z.string()) }), 'GET', '/api/locales')).locales,
  version: async () => (await call(z.object({ build: z.string() }), 'GET', '/version')).build,
}
