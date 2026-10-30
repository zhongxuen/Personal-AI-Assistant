/** `"local-durable"`: the backend stores data on its own disk and keeps it across
 * restarts (the desktop install). `"ephemeral"`: the deployed Render backend, whose disk
 * is wiped on every restart/redeploy. */
export type StorageMode = 'local-durable' | 'ephemeral'

export interface HealthResponse {
  status: string
  service: string
  storage_mode: StorageMode
}
