"""GraphOS-owned messaging entrypoints, channel adapters, and router.

The agent command and durable bus contracts remain with their respective
owners. Channel adapters are loaded by the messaging registry's entry points.
"""

# Touches only this lightweight, docstring-only subpackage init — each
# platform SDK stays lazily loaded through the entry-point registry
# (``graph_os.messaging.registry``), never imported here.
from graph_os.messaging import backends

__all__ = ["backends"]
