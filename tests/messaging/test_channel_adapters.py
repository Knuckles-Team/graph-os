"""The public channel adapters are supplied by GraphOS."""

from __future__ import annotations

from importlib import import_module

import pytest
from agent_utilities.messaging.base import MessagingBackend


@pytest.mark.parametrize(
    ("platform", "class_name"),
    [
        ("discord", "DiscordBackend"),
        ("googlechat", "GoogleChatBackend"),
        ("googlemeet", "GoogleMeetBackend"),
        ("imessage", "IMessageBackend"),
        ("irc", "IRCBackend"),
        ("line", "LINEBackend"),
        ("matrix", "MatrixBackend"),
        ("mattermost", "MattermostBackend"),
        ("nextcloud", "NextcloudTalkBackend"),
        ("signal", "SignalBackend"),
        ("slack", "SlackBackend"),
        ("synology", "SynologyChatBackend"),
        ("teams", "TeamsBackend"),
        ("telegram", "TelegramBackend"),
        ("twitch", "TwitchBackend"),
        ("voicecall", "VoiceCallBackend"),
        ("whatsapp", "WhatsAppBackend"),
    ],
)
def test_graphos_owns_channel_adapter(platform: str, class_name: str) -> None:
    adapter = getattr(
        import_module(f"graph_os.messaging.backends.{platform}"), class_name
    )

    assert issubclass(adapter, MessagingBackend)
    assert adapter.__module__ == f"graph_os.messaging.backends.{platform}"
