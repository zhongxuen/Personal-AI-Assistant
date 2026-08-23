"""
Platform abstraction (§20-22).

Every platform (desktop, web, discord, ...) implements this Protocol to translate its
native input into the one shape AssistantCore accepts (AssistantRequest), and to render
the AssistantResponse it gets back into whatever that platform expects. AssistantCore
itself never knows which platform called it -- keeping that boundary here is what lets
file 12 (web) and file 13 (discord) plug in without touching AssistantCore.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.core.models import AssistantRequest, AssistantResponse


@runtime_checkable
class PlatformAdapter(Protocol):
    def to_request(self, raw_input: Any) -> AssistantRequest: ...

    def to_platform_output(self, response: AssistantResponse) -> Any: ...
