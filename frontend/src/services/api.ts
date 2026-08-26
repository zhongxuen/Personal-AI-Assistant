import type { HealthResponse } from '../types/health'
import type { Task, TaskCreateInput, TaskFilters, TaskUpdateInput } from '../types/task'
import type { Routine, RoutineRunResult, RoutineStep, ToolInfo } from '../types/routine'
import type { LLMUsageResponse } from '../types/llmUsage'
import type { ApplicationMapping, DefaultProject } from '../types/memory'
import type { VoiceMessageResponse } from '../types/voice'

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

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/api/health`)
  if (!response.ok) {
    throw new Error(`Health check failed: ${response.status}`)
  }
  return response.json() as Promise<HealthResponse>
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

  const response = await fetch(`${API_BASE_URL}/api/tasks${query ? `?${query}` : ''}`)
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to load tasks: ${response.status}`))
  }
  return response.json() as Promise<Task[]>
}

export async function createTask(input: TaskCreateInput): Promise<Task> {
  const response = await fetch(`${API_BASE_URL}/api/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to create task: ${response.status}`))
  }
  return response.json() as Promise<Task>
}

export async function updateTask(id: number, input: TaskUpdateInput): Promise<Task> {
  const response = await fetch(`${API_BASE_URL}/api/tasks/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to update task: ${response.status}`))
  }
  return response.json() as Promise<Task>
}

export async function completeTask(id: number): Promise<Task> {
  const response = await fetch(`${API_BASE_URL}/api/tasks/${id}/complete`, { method: 'POST' })
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to complete task: ${response.status}`))
  }
  return response.json() as Promise<Task>
}

export async function deleteTask(id: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/tasks/${id}`, { method: 'DELETE' })
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to delete task: ${response.status}`))
  }
}

// --- Routines & tools ----------------------------------------------------------------

export async function getTools(): Promise<ToolInfo[]> {
  const response = await fetch(`${API_BASE_URL}/api/tools`)
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to load tools: ${response.status}`))
  }
  return response.json() as Promise<ToolInfo[]>
}

export async function getRoutines(): Promise<Routine[]> {
  const response = await fetch(`${API_BASE_URL}/api/routines`)
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to load routines: ${response.status}`))
  }
  return response.json() as Promise<Routine[]>
}

export async function createRoutine(name: string, steps: RoutineStep[]): Promise<Routine> {
  const response = await fetch(`${API_BASE_URL}/api/routines`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, steps }),
  })
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to create routine: ${response.status}`))
  }
  return response.json() as Promise<Routine>
}

export async function updateRoutineSteps(name: string, steps: RoutineStep[]): Promise<Routine> {
  const response = await fetch(`${API_BASE_URL}/api/routines/${encodeURIComponent(name)}/steps`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ steps }),
  })
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to update routine: ${response.status}`))
  }
  return response.json() as Promise<Routine>
}

export async function deleteRoutine(name: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/routines/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to delete routine: ${response.status}`))
  }
}

export async function runRoutine(name: string): Promise<RoutineRunResult> {
  const response = await fetch(`${API_BASE_URL}/api/routines/${encodeURIComponent(name)}/run`, {
    method: 'POST',
  })
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to run routine: ${response.status}`))
  }
  return response.json() as Promise<RoutineRunResult>
}

// --- LLM provider status (§8, §39) ----------------------------------------------------

export async function getLlmUsage(): Promise<LLMUsageResponse> {
  const response = await fetch(`${API_BASE_URL}/api/llm/usage`)
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to load LLM usage: ${response.status}`))
  }
  return response.json() as Promise<LLMUsageResponse>
}

// --- Memory / settings (§37 Phase 8, file 09 prompt 3) --------------------------------

export async function getApplicationMappings(): Promise<Record<string, ApplicationMapping>> {
  const response = await fetch(`${API_BASE_URL}/api/memory/applications`)
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to load application mappings: ${response.status}`))
  }
  return response.json() as Promise<Record<string, ApplicationMapping>>
}

export async function setApplicationMapping(
  alias: string,
  mapping: ApplicationMapping,
): Promise<ApplicationMapping> {
  const response = await fetch(`${API_BASE_URL}/api/memory/applications/${encodeURIComponent(alias)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(mapping),
  })
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to save '${alias}': ${response.status}`))
  }
  return response.json() as Promise<ApplicationMapping>
}

export async function deleteApplicationMapping(alias: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/memory/applications/${encodeURIComponent(alias)}`, {
    method: 'DELETE',
  })
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to delete '${alias}': ${response.status}`))
  }
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
  const response = await fetch(`${API_BASE_URL}/api/memory/default-project`)
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to load default project: ${response.status}`))
  }
  return response.json() as Promise<DefaultProject>
}

export async function setDefaultProject(defaultProject: string): Promise<DefaultProject> {
  const response = await fetch(`${API_BASE_URL}/api/memory/default-project`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ default_project: defaultProject }),
  })
  if (!response.ok) {
    throw new Error(await errorDetail(response, `Failed to save default project: ${response.status}`))
  }
  return response.json() as Promise<DefaultProject>
}
