import type { HealthResponse } from '../types/health'
import type { Task, TaskCreateInput, TaskFilters, TaskUpdateInput } from '../types/task'
import type { Routine, RoutineRunResult, RoutineStep, ToolInfo } from '../types/routine'
import type { LLMUsageResponse, ProviderHealth } from '../types/llmUsage'
import type { ActivityResponse } from '../types/activity'
import type { ApplicationMapping, DefaultProject } from '../types/memory'
import type { VoiceMessageResponse } from '../types/voice'
import type { AssistantResponse, AssistantStreamEvent } from '../types/assistant'
import type { DiscordStatus } from '../types/discord'
import type { WhatsAppLinkCode, WhatsAppLinkStatus } from '../types/whatsapp'
import type { DiagnosticCheck, DiagnosticsRunResult } from '../types/diagnostics'
import type { Project, ProjectRoots } from '../types/project'
import { authHeaders, clearToken, setToken } from './auth'

// In dev, Vite proxies /api to the FastAPI backend (see vite.config.ts).
// In prod this should be set to the deployed API's base URL.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

async function errorDetail(response: Response, fallback: string): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
      return body.detail
    }
  } catch {
    // Body wasn't JSON (or was empty) -- fall through to the generic message.
  }
  return fallback
}

/** Throws `errorDetail`'s message when `response` isn't ok. Also drops a stored token
 * on a 401 specifically (§34, file 12 prompt 2) -- a protected route only ever 401s
 * when the token is missing/expired/for a since-deleted user (`app.api.dependencies`),
 * so leaving the stale token in `localStorage` would just make every subsequent
 * request 401 again forever instead of prompting the user to log back in. No-op call
 * when there was no token to clear in the first place (e.g. `/api/auth/login` itself
 * rejecting bad credentials).
 */
async function ensureOk(response: Response, fallback: string): Promise<void> {
  if (response.ok) return
  if (response.status === 401) clearToken()
  throw new Error(await errorDetail(response, fallback))
}

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/api/health`)
  if (!response.ok) {
    throw new Error(`Health check failed: ${response.status}`)
  }
  return response.json() as Promise<HealthResponse>
}

// --- Auth (§34, file 12 prompt 1/2) ---------------------------------------------------
//
// Every function below this section attaches `authHeaders()` and routes failures
// through `ensureOk` -- the routes they call all require a bearer token
// (docs/security.md's "Authentication" section), unlike getHealth/voice above.

export interface LoginResult {
  access_token: string
  token_type: string
  user_id: number
  username: string
}

/** POST /api/auth/login (form-encoded `username`/`password`, matching FastAPI's
 * `OAuth2PasswordRequestForm` on the backend -- not JSON). Persists the returned token
 * via `services/auth.ts`'s `setToken` on success so every subsequent protected call
 * below picks it up automatically; throws (leaving any previous session untouched)
 * on wrong credentials.
 */
export async function login(username: string, password: string): Promise<LoginResult> {
  const response = await fetch(`${API_BASE_URL}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ username, password }),
  })
  await ensureOk(response, `Login failed: ${response.status}`)
  const result = (await response.json()) as LoginResult
  setToken(result.access_token)
  return result
}

/** Clears the stored token. There's no server-side session to invalidate (a JWT is
 * stateless until it expires on its own -- see docs/security.md) so this is purely
 * client-side, same effect as clearToken() on a 401.
 */
export function logout(): void {
  clearToken()
}

// --- Tasks -------------------------------------------------------------------------

export async function getTasks(filters: TaskFilters = {}): Promise<Task[]> {
  const params = new URLSearchParams()
  if (filters.status) params.set('status', filters.status)
  if (filters.category) params.set('category', filters.category)
  if (filters.due_before) params.set('due_before', filters.due_before)
  if (filters.due_after) params.set('due_after', filters.due_after)
  if (filters.overdue_only) params.set('overdue_only', 'true')
  const query = params.toString()

  const response = await fetch(`${API_BASE_URL}/api/tasks${query ? `?${query}` : ''}`, {
    headers: authHeaders(),
  })
  await ensureOk(response, `Failed to load tasks: ${response.status}`)
  return response.json() as Promise<Task[]>
}

export async function createTask(input: TaskCreateInput): Promise<Task> {
  const response = await fetch(`${API_BASE_URL}/api/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(input),
  })
  await ensureOk(response, `Failed to create task: ${response.status}`)
  return response.json() as Promise<Task>
}

export async function updateTask(id: number, input: TaskUpdateInput): Promise<Task> {
  const response = await fetch(`${API_BASE_URL}/api/tasks/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(input),
  })
  await ensureOk(response, `Failed to update task: ${response.status}`)
  return response.json() as Promise<Task>
}

export async function completeTask(id: number): Promise<Task> {
  const response = await fetch(`${API_BASE_URL}/api/tasks/${id}/complete`, {
    method: 'POST',
    headers: authHeaders(),
  })
  await ensureOk(response, `Failed to complete task: ${response.status}`)
  return response.json() as Promise<Task>
}

export async function deleteTask(id: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/tasks/${id}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  await ensureOk(response, `Failed to delete task: ${response.status}`)
}

// --- Routines & tools ----------------------------------------------------------------

export async function getTools(): Promise<ToolInfo[]> {
  const response = await fetch(`${API_BASE_URL}/api/tools`, { headers: authHeaders() })
  await ensureOk(response, `Failed to load tools: ${response.status}`)
  return response.json() as Promise<ToolInfo[]>
}

export async function getRoutines(): Promise<Routine[]> {
  const response = await fetch(`${API_BASE_URL}/api/routines`, { headers: authHeaders() })
  await ensureOk(response, `Failed to load routines: ${response.status}`)
  return response.json() as Promise<Routine[]>
}

export async function createRoutine(name: string, steps: RoutineStep[]): Promise<Routine> {
  const response = await fetch(`${API_BASE_URL}/api/routines`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ name, steps }),
  })
  await ensureOk(response, `Failed to create routine: ${response.status}`)
  return response.json() as Promise<Routine>
}

export async function updateRoutineSteps(name: string, steps: RoutineStep[]): Promise<Routine> {
  const response = await fetch(`${API_BASE_URL}/api/routines/${encodeURIComponent(name)}/steps`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ steps }),
  })
  await ensureOk(response, `Failed to update routine: ${response.status}`)
  return response.json() as Promise<Routine>
}

export async function setRoutineEnabled(name: string, enabled: boolean): Promise<Routine> {
  const response = await fetch(`${API_BASE_URL}/api/routines/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ enabled }),
  })
  await ensureOk(response, `Failed to ${enabled ? 'start' : 'stop'} routine: ${response.status}`)
  return response.json() as Promise<Routine>
}

export async function renameRoutine(name: string, newName: string): Promise<Routine> {
  const response = await fetch(`${API_BASE_URL}/api/routines/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ name: newName }),
  })
  await ensureOk(response, `Failed to rename routine: ${response.status}`)
  return response.json() as Promise<Routine>
}

export async function deleteRoutine(name: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/routines/${encodeURIComponent(name)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  await ensureOk(response, `Failed to delete routine: ${response.status}`)
}

export async function runRoutine(name: string): Promise<RoutineRunResult> {
  const response = await fetch(`${API_BASE_URL}/api/routines/${encodeURIComponent(name)}/run`, {
    method: 'POST',
    headers: authHeaders(),
  })
  await ensureOk(response, `Failed to run routine: ${response.status}`)
  return response.json() as Promise<RoutineRunResult>
}

// --- Project discovery (Coding Routine template) ---------------------------------------
//
// Wraps `app.api.routes.projects` (backend/app/projects/discovery.py) -- the "which
// project am I working on today?" picker behind the Coding Routine builder
// (frontend/src/components/CodingRoutinePanel.tsx). `roots` is the scan-folder list
// (edited from Settings); `getProjects` is that list's immediate subdirectories.

export async function getProjects(): Promise<Project[]> {
  const response = await fetch(`${API_BASE_URL}/api/projects`, { headers: authHeaders() })
  await ensureOk(response, `Failed to load projects: ${response.status}`)
  return response.json() as Promise<Project[]>
}

export async function getProjectRoots(): Promise<ProjectRoots> {
  const response = await fetch(`${API_BASE_URL}/api/projects/roots`, { headers: authHeaders() })
  await ensureOk(response, `Failed to load project folders: ${response.status}`)
  return response.json() as Promise<ProjectRoots>
}

export async function setProjectRoots(roots: string[]): Promise<ProjectRoots> {
  const response = await fetch(`${API_BASE_URL}/api/projects/roots`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ roots }),
  })
  await ensureOk(response, `Failed to save project folders: ${response.status}`)
  return response.json() as Promise<ProjectRoots>
}

// --- LLM provider status (§8, §39) ----------------------------------------------------

export async function getLlmUsage(): Promise<LLMUsageResponse> {
  const response = await fetch(`${API_BASE_URL}/api/llm/usage`, { headers: authHeaders() })
  await ensureOk(response, `Failed to load LLM usage: ${response.status}`)
  return response.json() as Promise<LLMUsageResponse>
}

// --- Recent activity (tool calls + LLM calls, merged) ---------------------------------

export async function getActivity(limit = 50): Promise<ActivityResponse> {
  const response = await fetch(`${API_BASE_URL}/api/activity?limit=${limit}`, {
    headers: authHeaders(),
  })
  await ensureOk(response, `Failed to load recent activity: ${response.status}`)
  return response.json() as Promise<ActivityResponse>
}

// --- Discord bot control (web dashboard follow-up to file 13) -------------------------
//
// Wraps `app.api.routes.discord`'s thin control surface over `DiscordBotManager`
// (backend/app/platforms/discord.py) -- replaces having to run
// scripts/start-discord-bot.ps1 locally just to get the bot online/offline; these hit
// whatever backend is already running (local dev or the deployed Render instance).

export async function getDiscordStatus(): Promise<DiscordStatus> {
  const response = await fetch(`${API_BASE_URL}/api/discord/status`, { headers: authHeaders() })
  await ensureOk(response, `Failed to load Discord bot status: ${response.status}`)
  return response.json() as Promise<DiscordStatus>
}

export async function startDiscordBot(): Promise<DiscordStatus> {
  const response = await fetch(`${API_BASE_URL}/api/discord/start`, {
    method: 'POST',
    headers: authHeaders(),
  })
  await ensureOk(response, `Failed to start the Discord bot: ${response.status}`)
  return response.json() as Promise<DiscordStatus>
}

export async function stopDiscordBot(): Promise<DiscordStatus> {
  const response = await fetch(`${API_BASE_URL}/api/discord/stop`, {
    method: 'POST',
    headers: authHeaders(),
  })
  await ensureOk(response, `Failed to stop the Discord bot: ${response.status}`)
  return response.json() as Promise<DiscordStatus>
}

// --- WhatsApp account linking (file 18 prompt 1) --------------------------------------
//
// Wraps `app.api.routes.whatsapp`'s pairing surface over `WhatsAppLinkService`
// (backend/app/whatsapp/linking.py). WhatsApp identifies a sender by phone number and
// nothing else, so this is the half of the flow that happens over an authenticated
// connection: a code minted here is what lets the (bearer-token-less, HMAC-verified)
// webhook trust a bare number later. There is deliberately no "link this number"
// call -- the number is only ever learned from a message the user actually sent.

export async function getWhatsAppLinkStatus(): Promise<WhatsAppLinkStatus> {
  const response = await fetch(`${API_BASE_URL}/api/whatsapp/link`, { headers: authHeaders() })
  await ensureOk(response, `Failed to load WhatsApp link status: ${response.status}`)
  return response.json() as Promise<WhatsAppLinkStatus>
}

/** POST /api/whatsapp/link-code. Always mints a *new* code and invalidates any previous
 * one, so calling this twice is safe -- only the newest code works. The returned code is
 * shown once and never refetchable; `getWhatsAppLinkStatus` only reports that one is
 * outstanding.
 */
export async function createWhatsAppLinkCode(): Promise<WhatsAppLinkCode> {
  const response = await fetch(`${API_BASE_URL}/api/whatsapp/link-code`, {
    method: 'POST',
    headers: authHeaders(),
  })
  await ensureOk(response, `Failed to generate a WhatsApp pairing code: ${response.status}`)
  return response.json() as Promise<WhatsAppLinkCode>
}

/** DELETE /api/whatsapp/link -- drops the caller's linked number and any outstanding
 * code. 404s when there was nothing linked, mirroring `DELETE /api/push/subscribe`.
 */
export async function unlinkWhatsApp(): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/whatsapp/link`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  await ensureOk(response, `Failed to unlink WhatsApp: ${response.status}`)
}

// --- System diagnostics (Status tab's "Run system test" button) -----------------------
//
// Wraps `app.api.routes.diagnostics`'s read-only self-test battery
// (backend/app/diagnostics/service.py) -- one click to see which component (database,
// an LLM provider, voice, Discord, ...) is actually broken instead of guessing from
// backend logs.

export async function getDiagnosticChecks(): Promise<DiagnosticCheck[]> {
  const response = await fetch(`${API_BASE_URL}/api/diagnostics/checks`, { headers: authHeaders() })
  await ensureOk(response, `Failed to load diagnostic checks: ${response.status}`)
  return response.json() as Promise<DiagnosticCheck[]>
}

/** `checks` omitted (or `undefined`) runs every component; a specific list narrows the
 * run down to just those (e.g. re-testing just Ollama after restarting it). */
export async function runDiagnostics(checks?: string[]): Promise<DiagnosticsRunResult> {
  const response = await fetch(`${API_BASE_URL}/api/diagnostics/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ checks: checks ?? null }),
  })
  await ensureOk(response, `Failed to run diagnostics: ${response.status}`)
  return response.json() as Promise<DiagnosticsRunResult>
}

/** Clear one provider's in-memory health back to AVAILABLE so `AIRouter` tries it again
 * on the next request.
 *
 * The only way out of `HealthManager`'s sticky MISCONFIGURED/DISABLED states short of
 * restarting the backend: a single PERMANENT_ERROR (bad `GEMINI_API_KEY`, or a
 * `GEMINI_MODEL` that key can't reach) benches a provider for the rest of the
 * process's life, so after fixing the config there'd otherwise be nothing to do but
 * redeploy. Purely bookkeeping -- it doesn't call the provider or change any config,
 * so if the underlying problem is still there the next request just re-benches it.
 */
export async function resetProviderHealth(provider: string): Promise<ProviderHealth & { provider: string }> {
  const response = await fetch(
    `${API_BASE_URL}/api/diagnostics/providers/${encodeURIComponent(provider)}/reset`,
    { method: 'POST', headers: authHeaders() },
  )
  await ensureOk(response, `Failed to reset ${provider} health: ${response.status}`)
  return response.json() as Promise<ProviderHealth & { provider: string }>
}

// --- Memory / settings (§37 Phase 8, file 09 prompt 3) --------------------------------

export async function getApplicationMappings(): Promise<Record<string, ApplicationMapping>> {
  const response = await fetch(`${API_BASE_URL}/api/memory/applications`, { headers: authHeaders() })
  await ensureOk(response, `Failed to load application mappings: ${response.status}`)
  return response.json() as Promise<Record<string, ApplicationMapping>>
}

export async function setApplicationMapping(
  alias: string,
  mapping: ApplicationMapping,
): Promise<ApplicationMapping> {
  const response = await fetch(`${API_BASE_URL}/api/memory/applications/${encodeURIComponent(alias)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(mapping),
  })
  await ensureOk(response, `Failed to save '${alias}': ${response.status}`)
  return response.json() as Promise<ApplicationMapping>
}

export async function deleteApplicationMapping(alias: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/memory/applications/${encodeURIComponent(alias)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  await ensureOk(response, `Failed to delete '${alias}': ${response.status}`)
}

// --- Voice (§24, §25, file 10 prompt 2) ------------------------------------------------
//
// Both calls below hit the same POST /api/voice/message endpoint (backend/app/api/
// routes/voice.py) -- `transcribeVoiceAudio` previews a recording with `dry_run: true`
// (STT only, nothing executes yet) so the caller can show/let the user edit the
// transcript before anything runs; `sendVoiceText` submits that (possibly edited)
// transcript for real, the same way a second call to the same endpoint does server-side.

async function postVoiceMessage(form: FormData): Promise<VoiceMessageResponse> {
  const response = await fetch(`${API_BASE_URL}/api/voice/message`, {
    method: 'POST',
    body: form,
  })
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Voice request failed: ${response.status}`))
  }
  return response.json() as Promise<VoiceMessageResponse>
}

export async function transcribeVoiceAudio(audio: Blob): Promise<VoiceMessageResponse> {
  const form = new FormData()
  form.set('audio', audio, 'recording.webm')
  form.set('dry_run', 'true')
  return postVoiceMessage(form)
}

export interface SendVoiceTextOptions {
  confirmed?: boolean
  override?: boolean
  conversationId?: string
}

export async function sendVoiceText(
  text: string,
  options: SendVoiceTextOptions = {},
): Promise<VoiceMessageResponse> {
  const form = new FormData()
  form.set('text', text)
  form.set('dry_run', 'false')
  form.set('confirmed', String(options.confirmed ?? false))
  form.set('override', String(options.override ?? false))
  if (options.conversationId) form.set('conversation_id', options.conversationId)
  return postVoiceMessage(form)
}

export async function getDefaultProject(): Promise<DefaultProject> {
  const response = await fetch(`${API_BASE_URL}/api/memory/default-project`, { headers: authHeaders() })
  await ensureOk(response, `Failed to load default project: ${response.status}`)
  return response.json() as Promise<DefaultProject>
}

export async function setDefaultProject(defaultProject: string): Promise<DefaultProject> {
  const response = await fetch(`${API_BASE_URL}/api/memory/default-project`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ default_project: defaultProject }),
  })
  await ensureOk(response, `Failed to save default project: ${response.status}`)
  return response.json() as Promise<DefaultProject>
}

// --- Chat (web platform adapter, §37 Phase 11, file 12 prompt 2) ----------------------
//
// Hits the exact same POST /api/assistant/message endpoint desktop/voice use (§41 Rule
// 7) with platform="web" -- no web-specific business logic on the backend, just a
// different caller. `user_id` in the request body is ignored by the backend for any
// non-desktop platform anyway (app/api/routes/assistant.py overwrites it with the
// authenticated user's own identity), so it's a fixed placeholder here.

export async function sendChatMessage(
  message: string,
  conversationId: string | undefined,
): Promise<AssistantResponse> {
  const response = await fetch(`${API_BASE_URL}/api/assistant/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({
      user_id: 'web-client',
      platform: 'web',
      message,
      conversation_id: conversationId ?? null,
    }),
  })
  await ensureOk(response, `Message failed: ${response.status}`)
  return response.json() as Promise<AssistantResponse>
}

/** POST /api/assistant/stream -- the same message, the same auth, the same final
 * answer as `sendChatMessage` above, but delivered as Server-Sent Events so the reply
 * can be rendered while it's still being generated.
 *
 * `onEvent` is called for every event in order; see `AssistantStreamEvent` for the
 * taxonomy. The promise resolves when the stream ends normally and rejects on a
 * transport/HTTP failure, so callers can keep their existing try/catch.
 *
 * Implemented over `fetch` + a `ReadableStream` reader rather than `EventSource`,
 * because `EventSource` can only issue GETs and cannot send an `Authorization` header
 * -- this endpoint needs both a JSON body and a bearer token.
 *
 * `signal` lets the caller abort in flight (component unmount, or a user starting a new
 * message). Aborting closes the connection, which the server sees as a disconnect.
 */
export async function streamChatMessage(
  message: string,
  conversationId: string | undefined,
  onEvent: (event: AssistantStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/assistant/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({
      user_id: 'web-client',
      platform: 'web',
      message,
      conversation_id: conversationId ?? null,
    }),
    signal,
  })
  await ensureOk(response, `Message failed: ${response.status}`)

  if (!response.body) {
    throw new Error('Streaming is not supported by this browser.')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  // SSE frames are separated by a blank line, and a single read can land mid-frame or
  // carry several frames at once -- so completed frames are peeled off the front and
  // whatever partial text is left stays buffered for the next read.
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      let separator = buffer.indexOf('\n\n')
      while (separator !== -1) {
        const frame = buffer.slice(0, separator)
        buffer = buffer.slice(separator + 2)
        const payload = frame
          .split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice('data:'.length).trim())
          .join('')
        if (payload) {
          try {
            onEvent(JSON.parse(payload) as AssistantStreamEvent)
          } catch {
            // A frame we can't parse is not worth tearing the whole reply down over --
            // the authoritative `done` event may still be on its way.
          }
        }
        separator = buffer.indexOf('\n\n')
      }
    }
  } finally {
    // Releasing the lock lets the body be cancelled cleanly on an early return/abort
    // instead of leaving the connection pinned open.
    reader.releaseLock()
  }
}
