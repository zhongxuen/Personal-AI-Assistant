export interface Task {
  id: number
  title: string
  status: string
  due: string | null
  category: string | null
  priority: string
  overdue: boolean
}

export interface TaskFilters {
  status?: string
  category?: string
  due_before?: string
  due_after?: string
  overdue_only?: boolean
}

export interface TaskCreateInput {
  title: string
  due?: string | null
  category?: string | null
  priority?: string | null
}

export interface TaskUpdateInput {
  title?: string
  due?: string | null
  category?: string | null
  priority?: string
  status?: string
}
