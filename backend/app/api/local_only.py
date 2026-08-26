"""
Local-only boundary for platform="desktop" requests (§23, §33, file 11 prompt 3).

Desktop-agent tools (process info, file ops, clipboard, the restricted terminal
command -- `app/tools/system.py`, `files.py`, `clipboard.py`, `terminal.py`) all
declare `platforms=["desktop"]` (§22), so `ToolExecutor` already refuses to run them
for any `RequesterContext` whose `platform` isn't `"desktop"`. That's a *tool*
boundary, not a *network* one: it only stops an already-accepted request from routing
to a desktop-only tool under the wrong platform -- it does nothing to stop a request
that reaches this backend over the network and simply *claims* `platform="desktop"`
from somewhere that isn't this machine (§23: "Never expose unrestricted Windows
control directly to a public web endpoint").

`enforce_desktop_local_only()` closes that gap at the HTTP layer, ahead of
`AssistantCore`/`ToolExecutor`: any request whose effective platform is `"desktop"`
must also arrive from a loopback client, or it's rejected with 403 before any tool
lookup, permission check, or handler call happens.

This is a stopgap, not real authentication (§23, §33) -- checking "does the client
socket look like loopback" only works because nothing today puts another host, a
mobile client, or a proxy between a legitimate desktop client and this backend; it
works *for now* because the desktop client and this backend still run on the same
machine (file 11) with no remote caller in the picture yet. File 12 (Web client)
is what actually introduces a second, remote caller of this same backend -- once that
lands, this check must be replaced with real per-client authentication (e.g. signed
tokens issued to the desktop client), not widened to trust more hosts. See
docs/security.md.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

# 127.0.0.1 / ::1 are the addresses a same-machine client actually connects from;
# "localhost" is included too since some HTTP clients resolve/report the hostname
# rather than the raw address. A module-level set (not an inline literal) so tests can
# monkeypatch it -- ASGI test transports (Starlette's TestClient) report a fixed
# synthetic client host ("testclient") that isn't real loopback, so a test simulating
# an *allowed* desktop request swaps this set rather than trying to fake a real socket
# peer address.
LOCAL_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost"}


def is_local_client(request: Request) -> bool:
    """Whether `request` arrived from this same machine (loopback), per
    `LOCAL_CLIENT_HOSTS`. Shared by `enforce_desktop_local_only` (raise-if-not-local for
    an explicit `platform="desktop"` claim) and by routes that have no explicit
    `platform` on the request body at all but still need to tell a same-machine caller
    apart from a remote one -- e.g. `app.api.routes.routines.run_routine` (file 12
    prompt 2), which infers `platform="desktop"` vs `"web"` from this rather than
    defaulting to `"desktop"` unconditionally the way `RoutineEngine.run()` used to.
    """
    client_host = request.client.host if request.client else None
    return client_host in LOCAL_CLIENT_HOSTS


def enforce_desktop_local_only(request: Request, platform: str) -> None:
    """Raise 403 if `platform` is "desktop" and `request` didn't arrive from loopback.

    No-op for every other platform value -- this boundary only applies to
    desktop-declared requests (§22/§23); web/discord/etc. requests are unaffected here
    and remain gated by whatever the eventual per-platform auth for *those* looks like.
    """
    if platform != "desktop":
        return

    if not is_local_client(request):
        raise HTTPException(
            status_code=403,
            detail="Requests with platform='desktop' are only accepted from the local machine (localhost).",
        )
