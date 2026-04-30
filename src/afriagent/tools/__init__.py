"""Tool registry — central access to external integrations.

The TOOL_REGISTRY in registry.py is the single source of truth
for all tool metadata. The coordinator reads it for dispatch decisions.
"""

from __future__ import annotations

from afriagent.config.logging import get_logger
from afriagent.tools.whmcs import WHMCSClient
from afriagent.tools.mpesa import MpesaClient
from afriagent.tools.registry import (
    TOOL_REGISTRY,
    get_tool_info,
    get_all_tools,
    get_tools_by_latency,
    get_tools_by_class,
    register_tool,
)
from afriagent.tools.dns_check import DNSChecker, get_dns_checker

log = get_logger(__name__)

__all__ = [
    "ToolRegistry",
    "TOOL_REGISTRY",
    "get_tool_info",
    "get_all_tools",
    "get_tools_by_latency",
    "get_tools_by_class",
    "register_tool",
    "DNSChecker",
    "get_dns_checker",
]


class ToolRegistry:
    """Manages external tool instances."""

    def __init__(self) -> None:
        self.whmcs = WHMCSClient()
        self.mpesa = MpesaClient()
        self.dns = get_dns_checker()

    async def close_all(self) -> None:
        await self.whmcs.close()
        await self.mpesa.close()
        log.info("All tools disconnected")
