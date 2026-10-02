"""GraphOS messaging channel adapters.

Each backend module in this package implements the ``MessagingBackend`` ABC
for a specific messaging platform. Backends are lazily imported via
``importlib.metadata.entry_points`` to avoid pulling in platform-specific
dependencies unless explicitly installed.

Supported backends:
    - ``telegram`` — Telegram via ``python-telegram-bot``
    - ``mattermost`` — Mattermost via ``mattermostdriver``

Further platforms (Discord, Slack, WhatsApp, Teams, Google Chat/Meet,
Matrix, IRC, Signal, iMessage, LINE, Twitch, Synology Chat, voice calls,
Nextcloud Talk) follow the same adapter shape and remain to be landed.

Install individual backends with::

    pip install graph-os[messaging-telegram]

Or install all currently-shipped backends with::

    pip install graph-os[messaging]
"""

# CONCEPT:AU-ECO.messaging.native-backend-abstraction — Native Messaging Backend Abstraction
