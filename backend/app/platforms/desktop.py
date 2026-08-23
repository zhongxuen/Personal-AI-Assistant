"""
Desktop platform adapter (§20-22).

Trivial passthrough used for local testing/CLI ahead of the real desktop agent (file 11,
md-files/11-desktop-agent.md): raw input is a plain string message, output is just the
response text.
"""

from __future__ import annotations

from app.core.models import AssistantRequest, AssistantResponse

DEFAULT_USER_ID = "local-user"


class DesktopAdapter:
    """Local/CLI adapter: message in, text out -- no platform-specific translation needed."""

    def to_request(self, raw_input: str) -> AssistantRequest:
        return AssistantRequest(user_id=DEFAULT_USER_ID, platform="desktop", message=raw_input)

    def to_platform_output(self, response: AssistantResponse) -> str:
        return response.text
