// Mirrors backend/app/api/routes/diagnostics.py's `CheckOut`/`CheckResultOut`/`DiagnosticsRunOut`.

/** One diagnosable component, before any test has run -- just enough to render a
 * checkbox for it. */
export interface DiagnosticCheck {
  name: string
  label: string
}

/** One component's outcome from a diagnostics run. */
export interface DiagnosticResult {
  name: string
  label: string
  ok: boolean
  message: string
  duration_ms: number
}

export interface DiagnosticsRunResult {
  /** True only when every result in `results` is ok -- false if even one component
   * failed. */
  ok: boolean
  results: DiagnosticResult[]
}
