import type { HealthResponse } from '../types/health'
import type { Task, TaskCreateInput, TaskFilters, TaskUpdateInput } from '../types/task'
import type { Routine, RoutineRunResult, RoutineStep, ToolInfo } from '../types/routine'
import type { LLMUsageResponse } from '../types/llmUsage'

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
