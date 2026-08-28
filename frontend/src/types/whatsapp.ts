// Mirrors backend/app/api/routes/whatsapp.py's `LinkStatusOut` / `LinkCodeOut`.

export interface WhatsAppLinkStatus {
  /** Whether the *backend* has Meta credentials at all (`WHATSAPP_ACCESS_TOKEN` +
   * `WHATSAPP_PHONE_NUMBER_ID`). Independent of `linked`: without this a pairing code
   * can be generated but never delivered, so the panel points at the setup docs
   * instead. Same role `DiscordStatus.configured` plays. */
  configured: boolean
  /** Whether *this* user has a WhatsApp number linked. */
  linked: boolean
  /** The caller's own linked number, digits only, or null when unlinked. */
  phone_number: string | null
  /** Whether a pairing code is still outstanding on this account. The code itself is
   * never in this response -- it's returned exactly once, by the POST that created it
   * (backend comment: so reloading the page can't re-reveal a code left on screen). */
  code_pending: boolean
  code_expires_at: string | null
}

export interface WhatsAppLinkCode {
  code: string
  expires_at: string
  /** The TTL in seconds, so a countdown doesn't have to reconcile the backend's clock
   * with the browser's. */
  expires_in_seconds: number
}
