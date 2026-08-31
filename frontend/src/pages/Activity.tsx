import { Bot, RefreshCw, Wrench } from 'lucide-react'
import { useActivity } from '../hooks/useActivity'
import type { ActivityItem } from '../types/activity'
import { Badge, Button, Panel, Skeleton, StaggerItem, StaggerList } from '../components/ui'
import type { BadgeTone } from '../components/ui'

const STATUS_TONE: Record<ActivityItem['status'], BadgeTone> = {
  ok: 'success',
  error: 'danger',
}

function timeLabel(timestamp: string): string {
  // Backend timestamps are naive UTC (no offset) -- append 'Z' so the browser parses
  // them as UTC instead of local time before converting back for display.
  const iso = timestamp.endsWith('Z') ? timestamp : `${timestamp}Z`
  return new Date(iso).toLocaleString()
}

function ActivityRow({ item }: { item: ActivityItem }) {
  const Icon = item.type === 'llm_call' ? Bot : Wrench

  return (
    <Panel className="flex flex-wrap items-center gap-3 p-3">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border bg-surface-2/60 text-text-muted">
        <Icon className="h-4 w-4" />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate font-medium text-text">{item.summary}</span>
          {item.scope_label && <Badge tone="secondary">{item.scope_label}</Badge>}
          {item.fallback_used && <Badge tone="warning">fallback</Badge>}
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-text-muted">
          <span className="font-mono">{timeLabel(item.timestamp)}</span>
          {item.type === 'tool_call' && item.platform && <span>via {item.platform}</span>}
          {item.type === 'llm_call' && (
            <span>
              {item.request_tokens ?? 0} / {item.response_tokens ?? 0} tokens
              {item.latency !== null ? ` · ${item.latency}ms` : ''}
            </span>
          )}
          {item.error && <span className="text-danger">— {item.error}</span>}
        </div>
      </div>

      <Badge tone={STATUS_TONE[item.status]}>{item.status === 'ok' ? 'OK' : 'ERROR'}</Badge>
    </Panel>
  )
}

export function ActivityPage() {
  const { data, loading, error, refresh } = useActivity()

  return (
    <main className="px-6 py-10">
      <div className="mx-auto max-w-4xl">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-text-muted">
            Recent tool calls, routine runs, and LLM requests, most recent first.
          </p>
          <Button variant="ghost" onClick={refresh}>
            <RefreshCw className="h-4 w-4" />
            Refresh
          </Button>
        </div>

        {loading && (
          <div className="mt-6 space-y-2">
            {[0, 1, 2, 3, 4].map((i) => (
              <Panel key={i} className="flex items-center gap-3 p-3">
                <Skeleton className="h-8 w-8 shrink-0 rounded-md" />
                <Skeleton className="h-8 flex-1" />
                <Skeleton className="h-5 w-14 rounded" />
              </Panel>
            ))}
          </div>
        )}
        {error && <p className="mt-6 text-danger">Failed to load recent activity: {error}</p>}

        {data && (
          <>
            <p className="mt-6 font-mono text-xs text-text-muted">
              Generated at {new Date(data.generated_at).toLocaleString()}
            </p>
            {data.items.length === 0 && (
              <p className="mt-3 text-text-muted">
                Nothing yet — activity shows up here as commands, routines, and LLM calls run.
              </p>
            )}
            <StaggerList className="mt-3 space-y-2">
              {data.items.map((item) => (
                <StaggerItem key={item.id}>
                  <ActivityRow item={item} />
                </StaggerItem>
              ))}
            </StaggerList>
          </>
        )}
      </div>
    </main>
  )
}
