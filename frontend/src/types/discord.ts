// Mirrors backend/app/api/routes/discord.py's `DiscordStatusOut`.

export type DiscordBotState = 'disabled' | 'stopped' | 'starting' | 'connected' | 'error'

export interface DiscordStatus {
  /** Whether `DISCORD_BOT_TOKEN` is set on the backend at all -- `state` is always
   * "disabled" when this is false, since there's nothing to start. */
  configured: boolean
  state: DiscordBotState
  /** The connected bot's Discord username (e.g. "Jarvis#1234"), only set once `state`
   * is "connected". */
  username: string | null
  /** The last connection error's message, only set once `state` is "error". */
  error: string | null
}
