"""
DiscordAdapter unit tests (§20-22, file 13 prompt 3).

Pure translation-layer tests for `DiscordAdapter` in isolation -- no `AssistantCore`,
no real tools, no database. `to_request()` and `to_platform_output()` are the adapter's
entire surface (§41 Rule 7: no assistant logic lives here), so these tests feed each a
mocked `discord.Message`-shaped object / a plain `AssistantResponse` and check the
translation is exactly right: bot-prefix stripping, field mapping, and Discord's
2000-character message cap.

`tests/platforms/test_discord_capability.py` covers the other half -- the same adapter
wired to a real `AssistantCore` end to end.
"""

from __future__ import annotations

from unittest.mock import Mock

from app.core.models import AssistantResponse
from app.platforms.discord import _DISCORD_MESSAGE_LIMIT, DiscordAdapter


def _mock_message(content: str, *, author_id: int = 111, channel_id: int = 222) -> Mock:
    """A mocked `discord.Message` exposing only what `DiscordMessage` (the Protocol in
    `app.platforms.discord`) actually reads: `content`, `author.id`, `channel.id`.
    """
    message = Mock()
    message.content = content
    message.author = Mock(id=author_id)
    message.channel = Mock(id=channel_id)
    return message


class TestToRequest:
    def test_builds_assistant_request_with_platform_discord(self):
        adapter = DiscordAdapter()
        message = _mock_message("Jarvis, what time is it")

        request = adapter.to_request(message)

        assert request.platform == "discord"

    def test_maps_author_id_to_user_id(self):
        adapter = DiscordAdapter()
        message = _mock_message("Jarvis, what time is it", author_id=98765)

        request = adapter.to_request(message)

        assert request.user_id == "98765"  # AssistantRequest.user_id is a str

    def test_maps_channel_id_to_conversation_id(self):
        adapter = DiscordAdapter()
        message = _mock_message("Jarvis, what time is it", channel_id=13579)

        request = adapter.to_request(message)

        assert request.conversation_id == "13579"

    def test_strips_leading_name_prefix_with_comma(self):
        adapter = DiscordAdapter()
        message = _mock_message("Jarvis, what are my tasks?")

        request = adapter.to_request(message)

        assert request.message == "what are my tasks?"

    def test_strips_leading_name_prefix_case_insensitively_without_punctuation(self):
        adapter = DiscordAdapter()
        message = _mock_message("jarvis open vscode")

        request = adapter.to_request(message)

        assert request.message == "open vscode"

    def test_strips_leading_at_mention_prefix(self):
        adapter = DiscordAdapter()
        message = _mock_message("<@123456> what time is it")

        request = adapter.to_request(message)

        assert request.message == "what time is it"

    def test_strips_leading_at_mention_and_name_prefix_together(self):
        # A client that renders "@Jarvis" as an actual mention token followed by the
        # typed name is exactly the double-prefix case _strip_bot_prefix guards.
        adapter = DiscordAdapter()
        message = _mock_message("<@!123456> Jarvis: start coding")

        request = adapter.to_request(message)

        assert request.message == "start coding"

    def test_leaves_message_untouched_when_no_bot_prefix_present(self):
        adapter = DiscordAdapter()
        message = _mock_message("what are my tasks?")

        request = adapter.to_request(message)

        assert request.message == "what are my tasks?"


class TestToPlatformOutput:
    def test_returns_response_text_unchanged_when_under_limit(self):
        adapter = DiscordAdapter()
        response = AssistantResponse(text="You have no tasks.")

        assert adapter.to_platform_output(response) == "You have no tasks."

    def test_falls_back_to_done_when_response_text_is_empty(self):
        adapter = DiscordAdapter()
        response = AssistantResponse(text="")

        assert adapter.to_platform_output(response) == "Done."

    def test_truncates_to_discord_character_limit(self):
        adapter = DiscordAdapter()
        response = AssistantResponse(text="x" * (_DISCORD_MESSAGE_LIMIT + 500))

        output = adapter.to_platform_output(response)

        assert len(output) == _DISCORD_MESSAGE_LIMIT
        assert output.endswith("...")

    def test_does_not_truncate_text_exactly_at_the_limit(self):
        adapter = DiscordAdapter()
        response = AssistantResponse(text="x" * _DISCORD_MESSAGE_LIMIT)

        output = adapter.to_platform_output(response)

        assert output == "x" * _DISCORD_MESSAGE_LIMIT
