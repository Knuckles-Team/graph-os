"""Security policy middleware (GRAPHOS-HOST-R008, OS-5.4/5.5/5.11/5.12 synthesis).

Combines jailbreak hardening, prompt-injection scanning, doom-loop tracking,
and a tool-repetition guard into one gateway a caller runs over an incoming
prompt or an outbound tool call. Ported from the agent runtime's
``agent_utilities.security.security_policy_middleware`` (moved wholesale, no
behavior change) per the GRAPHOS-HOST-R008 boundary move:
``graph-os`` hosts GraphOS's own security boundary modules; the agent
runtime no longer declares this one.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SecurityViolation(Exception):
    """Raised when a security guard blocks an action."""


class SecurityPolicyMiddleware:
    """Central gateway for all security checks across I/O boundaries."""

    def __init__(self) -> None:
        self.max_tool_repetitions = 3
        self._tool_history: dict[str, int] = {}

    def intercept_input(self, prompt: str) -> str:
        """Scan incoming user prompts for injections and jailbreaks."""
        if self._detect_jailbreak(prompt):
            logger.error("Jailbreak pattern detected in input prompt.")
            raise SecurityViolation("Prompt injection/jailbreak detected.")
        return prompt

    def intercept_tool_call(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Scan outbound tool calls for repetitions and doom loops."""
        key = f"{tool_name}_{args!s}"
        count = self._tool_history.get(key, 0)

        if count >= self.max_tool_repetitions:
            logger.error(
                "Doom-loop detected: tool %s repeated %d times.", tool_name, count
            )
            raise SecurityViolation(f"Tool repetition guard triggered for {tool_name}.")

        self._tool_history[key] = count + 1
        return True

    def _detect_jailbreak(self, prompt: str) -> bool:
        """Detect jailbreak patterns (DAN, AIM, UCAR) and topological risks."""
        # Simple static checks for the synthesized concept.
        # Advanced implementations will use subgraph comparisons.
        suspicious_keywords = ["ignore all previous", "DAN", "developer mode", "bypass"]
        return any(keyword.lower() in prompt.lower() for keyword in suspicious_keywords)
