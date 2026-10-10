"""GraphOS messaging backend registry with entry-point discovery.

Provides ``MessagingRegistry`` — a singleton that discovers, instantiates,
and manages messaging backends via Python entry-points. This follows the
``PluginRegistry`` pattern from ``graph/plugin_registry.py`` but uses
``importlib.metadata`` for zero-config backend discovery.

Discovery Flow::

    1. User installs: ``pip install graph-os[messaging-telegram]``
    2. The ``telegram`` entry-point is registered in pyproject.toml
    3. ``MessagingRegistry`` scans ``agent_utilities.messaging`` entry-points
    4. ``registry.create_backend("telegram")`` loads the backend class
    5. Backend is instantiated with config and ready to connect

CONCEPT:AU-ECO.messaging.native-backend-abstraction — Native Messaging Backend Abstraction

See Also:
    - ``graph/plugin_registry.py`` for the dynamic tool hydration pattern.
    - OpenClaw ``src/channels/plugins/bundled-ids.ts`` for discovery logic.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import logging
from typing import Any

from agent_utilities.core.config import setting
from agent_utilities.messaging.models import MessagingConfig

from graph_os.messaging.supervision import (
    AdapterImportFailed,
    AdapterMissing,
    ChannelSupervisionState,
)

logger = logging.getLogger(__name__)

# Entry-point group name for messaging backend discovery.
_ENTRY_POINT_GROUP = "agent_utilities.messaging"

# Environment variable prefix for backend configuration.
_ENV_PREFIX = "MESSAGING_"


class MessagingRegistry:
    """Singleton registry for discovering and managing messaging backends.

    CONCEPT:AU-ECO.messaging.native-backend-abstraction — Native Messaging Backend Abstraction

    Discovers installed backends via ``importlib.metadata.entry_points``
    and provides factory methods to create configured instances.

    Usage::

        registry = MessagingRegistry()

        # List what's installed
        print(registry.list_backends())
        # ['mattermost', 'telegram']

        # Create a backend
        telegram = registry.create_backend("telegram")
        await telegram.connect()

        # Create with explicit config (the token comes from the caller's own
        # secret store, never a literal in code)
        config = MessagingConfig(platform="mattermost", token=mattermost_token)
        mattermost = registry.create_backend("mattermost", config=config)

        # Auto-configure all enabled backends from environment
        backends = registry.create_all_enabled()

    Attributes:
        _entry_points: Cached mapping of backend ID → entry-point.
        _instances: Active backend instances.
    """

    _instance: MessagingRegistry | None = None

    def __init__(self) -> None:
        self._entry_points: dict[str, Any] = {}
        self._instances: dict[str, Any] = {}
        # CONCEPT:GRAPHOS-MESSAGING-R005 — typed DEGRADED reason per backend id
        # that create_all_enabled() could not instantiate; reuses the existing
        # ChannelSupervisionState.DEGRADED value (graph_os.messaging.supervision)
        # rather than a new record type, see degraded_backends()/degraded_reason().
        self._degraded: dict[str, AdapterMissing | AdapterImportFailed] = {}
        self._discover()

    @classmethod
    def instance(cls) -> MessagingRegistry:
        """Get or create the singleton registry instance.

        Returns:
            The global ``MessagingRegistry`` singleton.
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _discover(self) -> None:
        """Scan entry-points for installed messaging backends.

        CONCEPT:AU-ECO.messaging.native-backend-abstraction

        Reads the ``agent_utilities.messaging`` entry-point group to find
        all installed backend classes. This is lazy — no backend code is
        imported until ``create_backend()`` is called.
        """
        try:
            eps: Any = importlib.metadata.entry_points()
            # Python 3.12+ returns a SelectableGroups, older returns dict
            if hasattr(eps, "select"):
                group_eps = eps.select(group=_ENTRY_POINT_GROUP)
            else:
                group_eps = dict(eps).get(_ENTRY_POINT_GROUP, [])

            for ep in group_eps:
                self._entry_points[ep.name] = ep
                logger.debug(
                    "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] Discovered messaging backend: %s → %s",
                    ep.name,
                    ep.value,
                )
        except Exception as e:  # entry-point scan is best-effort; downstream is_installed()/list_backends() correctly reflect only what was discovered
            logger.debug(
                "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] Entry-point discovery failed: %s",
                e,
            )

        if self._entry_points:
            logger.info(
                "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] Discovered %d messaging backends: %s",
                len(self._entry_points),
                ", ".join(sorted(self._entry_points)),
            )
        else:
            logger.debug(
                "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] No messaging backends discovered. "
                "Install with: pip install graph-os[messaging-discord]"
            )

    def list_backends(self) -> list[str]:
        """List all discovered (installed) backend identifiers.

        Returns:
            Sorted list of backend IDs (e.g., ``["discord", "slack"]``).
        """
        return sorted(self._entry_points.keys())

    def is_installed(self, backend_id: str) -> bool:
        """Check if a specific backend is installed.

        Args:
            backend_id: Platform identifier (e.g., ``"discord"``).

        Returns:
            True if the backend's entry-point was discovered.
        """
        return backend_id in self._entry_points

    def create_backend(
        self,
        backend_id: str,
        config: MessagingConfig | None = None,
    ) -> Any:
        """Create a new backend instance.

        CONCEPT:AU-ECO.messaging.native-backend-abstraction

        Loads the backend class from the entry-point and instantiates it
        with the provided or auto-detected configuration.

        Args:
            backend_id: Platform identifier (e.g., ``"discord"``).
            config: Optional explicit configuration. If not provided,
                auto-configures from environment variables.

        Returns:
            Configured ``MessagingBackend`` instance (not yet connected).

        Raises:
            ValueError: If the backend is not installed.
            ImportError: If the backend's dependencies are missing.
        """
        if backend_id not in self._entry_points:
            available = ", ".join(self.list_backends()) or "none"
            raise AdapterMissing(
                f"Messaging backend '{backend_id}' is not installed. "
                f"Available: {available}. "
                f"Install with: pip install graph-os[messaging-{backend_id}]"
            )

        ep = self._entry_points[backend_id]

        try:
            backend_cls = ep.load()
        except ImportError as e:
            raise AdapterImportFailed(
                f"Failed to load messaging backend '{backend_id}': {e}. "
                f"Install dependencies with: pip install graph-os[messaging-{backend_id}]"
            ) from e

        # Auto-configure from environment if no explicit config
        if config is None:
            config = self._auto_config(backend_id)

        instance = backend_cls(config=config)
        self._instances[backend_id] = instance

        logger.info(
            "[CONCEPT:AU-ECO.messaging.native-backend-abstraction] Created messaging backend: %s (%s)",
            backend_id,
            backend_cls.__name__,
        )
        return instance

    def get_backend(self, backend_id: str) -> Any | None:
        """Get an existing backend instance (if created).

        Args:
            backend_id: Platform identifier.

        Returns:
            The backend instance, or None if not yet created.
        """
        return self._instances.get(backend_id)

    def _is_configured(
        self, backend_id: str, config: MessagingConfig | None = None
    ) -> bool:
        """True when ``backend_id`` has credentials/webhook material set in the
        environment — the SAME predicate :meth:`create_all_enabled` uses to decide
        whether to auto-create a backend, extracted so a health probe can ask
        "is this platform configured" without instantiating anything (CONCEPT:
        AU-ECO.messaging.native-backend-abstraction).
        """
        cfg = config if config is not None else self._auto_config(backend_id)
        reference_configured = backend_id == "synology" and bool(
            str(setting("SYNOLOGY_CHAT_WEBHOOK_URL_REF", "") or "").strip()
        )
        return bool(cfg.token or cfg.app_id or cfg.webhook_url or reference_configured)

    def configured_backend_ids(self) -> list[str]:
        """List discovered backend ids that have credentials configured in the
        environment, WITHOUT instantiating any backend (no side effects, no
        network I/O) — the read-only counterpart of :meth:`create_all_enabled`,
        built for health/status probes.

        Returns:
            Sorted list of platform ids (e.g. ``["telegram", "slack"]``).
        """
        return sorted(
            backend_id
            for backend_id in self.list_backends()
            if self._is_configured(backend_id)
        )

    def identity_digest(self, platform: str) -> str:
        """Stable, secret-free identity for the native per-platform intake lease."""
        config = self._auto_config(platform)
        token = str(getattr(config, "token", "") or "").strip()
        app_id = str(getattr(config, "app_id", "") or "").strip()
        webhook_url = str(getattr(config, "webhook_url", "") or "").strip()
        if platform == "telegram" and token:
            material = token.split(":", 1)[0]
        else:
            material = app_id or token or webhook_url or platform
        return hashlib.sha256(f"{platform}:{material}".encode()).hexdigest()

    def create_all_enabled(self) -> dict[str, Any]:
        """Create backend instances for all platforms with env-var tokens.

        CONCEPT:AU-ECO.messaging.native-backend-abstraction

        Scans environment variables to find configured platforms and
        auto-creates backend instances for each. Every OTHER configured
        channel is still created even when one fails.

        CONCEPT:GRAPHOS-MESSAGING-R005 — a channel whose adapter is missing
        or fails to import is no longer only logged and omitted: it is also
        recorded DEGRADED with a typed reason, readable via
        :meth:`degraded_backends` / :meth:`degraded_reason`.

        Returns:
            Dict mapping backend_id → backend instance.
        """
        created: dict[str, Any] = {}
        for backend_id in self.list_backends():
            config = self._auto_config(backend_id)
            if self._is_configured(backend_id, config):
                try:
                    instance = self.create_backend(backend_id, config=config)
                    created[backend_id] = instance
                    self._degraded.pop(backend_id, None)
                except (AdapterMissing, AdapterImportFailed) as e:
                    self._degraded[backend_id] = e
                    logger.warning(
                        "[CONCEPT:GRAPHOS-MESSAGING-R005] Channel '%s' DEGRADED (%s): %s",
                        backend_id,
                        type(e).__name__,
                        e,
                    )
        return created

    def degraded_backends(self) -> dict[str, ChannelSupervisionState]:
        """Configured channels :meth:`create_all_enabled` could not create.

        CONCEPT:GRAPHOS-MESSAGING-R005 — reuses the existing
        ``ChannelSupervisionState.DEGRADED`` value for every entry rather than
        a new record type; pair with :meth:`degraded_reason` for the typed
        reason (:class:`~graph_os.messaging.supervision.AdapterMissing` or
        :class:`~graph_os.messaging.supervision.AdapterImportFailed`).

        Returns:
            Dict mapping backend_id → ``ChannelSupervisionState.DEGRADED``.
        """
        return {
            backend_id: ChannelSupervisionState.DEGRADED
            for backend_id in self._degraded
        }

    def degraded_reason(
        self, backend_id: str
    ) -> AdapterMissing | AdapterImportFailed | None:
        """The typed reason ``backend_id`` is DEGRADED, or ``None``.

        CONCEPT:GRAPHOS-MESSAGING-R005
        """
        return self._degraded.get(backend_id)

    def _auto_config(self, backend_id: str) -> MessagingConfig:
        """Build a MessagingConfig from environment variables.

        CONCEPT:AU-ECO.messaging.native-backend-abstraction

        Convention: ``MESSAGING_<PLATFORM>_TOKEN``, ``MESSAGING_<PLATFORM>_APP_ID``, etc.
        Also checks platform-native env vars (e.g., ``DISCORD_BOT_TOKEN``).

        Args:
            backend_id: Platform identifier.

        Returns:
            Auto-populated ``MessagingConfig``.
        """
        prefix = f"{_ENV_PREFIX}{backend_id.upper()}_"

        # Platform-native env var mappings (from OpenClaw plugin manifests)
        native_token_vars: dict[str, list[str]] = {
            "discord": ["DISCORD_BOT_TOKEN"],
            "slack": ["SLACK_BOT_TOKEN"],
            "telegram": ["TELEGRAM_BOT_TOKEN"],
            "whatsapp": ["WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"],
            "teams": ["MSTEAMS_APP_PASSWORD"],
            "googlechat": ["GOOGLE_CHAT_SERVICE_ACCOUNT"],
            "googlemeet": ["GOOGLE_MEET_SERVICE_ACCOUNT"],
            "mattermost": ["MATTERMOST_TOKEN"],
            "matrix": ["MATRIX_ACCESS_TOKEN"],
            "irc": ["IRC_SERVER"],
            "signal": ["SIGNAL_PHONE_NUMBER"],
            "imessage": [],  # macOS-only
            "line": ["LINE_CHANNEL_ACCESS_TOKEN"],
            "twitch": ["TWITCH_OAUTH_TOKEN"],
            "synology": [],
            "voicecall": ["TWILIO_AUTH_TOKEN"],
            "nextcloud": ["NEXTCLOUD_TOKEN"],
        }

        # Try generic prefix first, then platform-native vars
        token = setting(f"{prefix}TOKEN", "")
        if not token:
            for var in native_token_vars.get(backend_id, []):
                token = setting(var, "")
                if token:
                    break

        app_id_vars: dict[str, str] = {
            "teams": "MSTEAMS_APP_ID",
            "whatsapp": "WHATSAPP_PHONE_NUMBER_ID",
            "googlechat": "GOOGLE_CHAT_PROJECT_ID",
            "line": "LINE_CHANNEL_ID",
            "voicecall": "TWILIO_ACCOUNT_SID",
        }
        app_id = setting(f"{prefix}APP_ID", "")
        if not app_id:
            app_id = setting(app_id_vars.get(backend_id, ""), "")

        app_secret = setting(f"{prefix}APP_SECRET", "")
        if not app_secret and backend_id == "teams":
            app_secret = setting("MSTEAMS_APP_PASSWORD", "")

        # WhatsApp config switch
        use_business_api = setting(f"{prefix}USE_BUSINESS_API", "false").lower() in (
            "true",
            "1",
            "yes",
        )

        webhook_url = (
            "" if backend_id == "synology" else setting(f"{prefix}WEBHOOK_URL", "")
        )
        return MessagingConfig(
            platform=backend_id,
            enabled=True,
            token=token,
            app_id=app_id,
            app_secret=app_secret,
            webhook_url=webhook_url,
            webhook_port=int(setting(f"{prefix}WEBHOOK_PORT", "0")),
            use_business_api=use_business_api,
        )
