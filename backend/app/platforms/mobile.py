"""
Mobile platform -- thin-client shape, no adapter class (§20-22, file 14).

`docs/architecture.md`'s "Two adapter shapes" section: a new platform gets a real
server-side `to_request`/`to_platform_output` class (`DiscordAdapter`,
`app/platforms/discord.py`) only when its native input *isn't* already JSON the client
controls. A mobile app is exactly the opposite case, same as web
(`app/platforms/web.py`) -- it can build an `AssistantRequest`-shaped JSON body
directly (`{"user_id": ..., "platform": "mobile", "message": ..., "conversation_id":
...}`) and POST it straight to the one shared `POST /api/assistant/message` route
(`app/api/routes/assistant.py`), the same endpoint desktop/web/discord all funnel
through. `AssistantResponse` comes back as JSON the same way. There's no native input
shape to translate here, so writing a no-op passthrough class here would only exist to
satisfy the pattern -- the architecture doc explicitly says not to do that.

`platform="mobile"` gets the same public auth boundary every non-desktop platform gets
(`get_optional_current_user` in `app/api/routes/assistant.py`) automatically, since
that boundary is keyed on `request.platform != "desktop"`, not an allowlist of known
platform strings -- no route change was needed to add this platform. Family/friends
who want to try the mobile client get an account the same way any other non-owner user
would (`AuthService.create_user`, `app/auth/service.py`) and log in through the
existing `POST /api/auth/login` route to get a bearer token; there's no separate
mobile-specific auth path.

See `app/tools/tasks.py`, `app/tools/system.py`, and `app/tools/timers.py` for the
tools whose `platforms` lists were extended to include `"mobile"` alongside `"web"`
(same chat-appropriate, non-desktop-only tier) -- and `app/tools/routines.py`'s
`RunRoutineTool` for the desktop-only tool that was deliberately *not* extended, for
the same reason it isn't reachable from web/discord either.
"""
