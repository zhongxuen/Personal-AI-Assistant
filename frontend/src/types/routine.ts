export interface RoutineStep {
  tool_name: string
  params: Record<string, unknown>
}

export interface Routine {
  id: number
  name: string
  trigger_type: string
  enabled: boolean
  steps: RoutineStep[]
}

export interface ToolInfo {
  name: string
  description: string
  parameters: Record<string, unknown>
  platforms: string[]
}

export interface RoutineStepRunResult {
  tool_name: string
  params: Record<string, unknown>
  result: {
    success: boolean
    data: Record<string, unknown> | null
    error: string | null
  }
}

export interface RoutineRunResult {
  success: boolean
  message: string | null
  error: string | null
  steps: RoutineStepRunResult[]
}
