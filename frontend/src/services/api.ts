import type { HealthResponse } from '../types/health'

// In dev, Vite proxies /api to the FastAPI backend (see vite.config.ts).
// In prod this should be set to the deployed API's base URL.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/api/health`)
  if (!response.ok) {
    throw new Error(`Health check failed: ${response.status}`)
  }
  return response.json() as Promise<HealthResponse>
}
